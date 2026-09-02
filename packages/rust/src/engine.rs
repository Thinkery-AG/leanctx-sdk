use std::collections::BTreeSet;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Map, Value};

use crate::errors::{
    boxed, ArtifactIntegrityError, CompatibilityError, EngineExecutionError, EngineProtocolError,
    EngineRejected, EngineTimeout, EngineUnavailable, PolicyAdmissionError, SdkResult,
    SourceUnavailableError, UnsupportedEngineError, ValidationError,
};
use crate::process::terminate_process_tree;
use crate::protocol::{
    canonical_bytes, exact_keys, existing_directory, json_integer, json_string, normalize_path,
    sha256_digest, strict_json_loads, validate_digest, validate_output_ref, validate_ref,
    ContextFailure, ContextMeasurement, ContextPlan, ContextReceiptLink, ContextSource,
    ContextView, EngineStatus, FailureCode, RecoveredSource, ENGINE_INTERFACE_VERSION,
    MAX_MEASUREMENTS, MAX_PATH_BYTES, MAX_REFS, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
    MAX_STDERR_BYTES, MAX_TEXT_BYTES, SCHEMA_VERSION, TRANSPORT_VERSION,
};

/// The transport seam used by the Product lifecycle.
pub trait EngineClient: Send + Sync {
    fn context_view(&self, plan: &ContextPlan) -> SdkResult<ContextView>;

    fn recover(
        &self,
        project_root: &Path,
        path: &str,
        recovery_ref: &str,
        source_ref: &str,
        source_digest: &str,
    ) -> SdkResult<RecoveredSource>;
}

#[derive(Clone, Debug)]
pub struct SubprocessEngineClient {
    engine_binary: Option<PathBuf>,
    timeout: Duration,
}

impl SubprocessEngineClient {
    pub fn new(engine_binary: Option<PathBuf>, timeout: Duration) -> SdkResult<Self> {
        validate_timeout(timeout)?;
        Ok(Self {
            engine_binary,
            timeout,
        })
    }

    pub fn with_binary(path: impl AsRef<Path>) -> SdkResult<Self> {
        Self::new(Some(path.as_ref().to_owned()), Duration::from_secs(30))
    }

    pub fn default_client() -> SdkResult<Self> {
        Self::new(None, Duration::from_secs(30))
    }

    pub fn engine_binary(&self) -> Option<&Path> {
        self.engine_binary.as_deref()
    }

    pub fn timeout(&self) -> Duration {
        self.timeout
    }

    fn validate_root(&self, project_root: &Path) -> SdkResult<PathBuf> {
        existing_directory(project_root)
            .map_err(|_| boxed(SourceUnavailableError::new("project_root is unavailable")))
    }

    fn resolve_binary(&self) -> SdkResult<PathBuf> {
        let requested = self
            .engine_binary
            .clone()
            .unwrap_or_else(|| PathBuf::from("lean-ctx"));
        let has_separator = requested
            .to_string_lossy()
            .bytes()
            .any(|byte| byte == b'/' || byte == b'\\');
        let candidate = if requested.is_absolute() || has_separator {
            fs::canonicalize(&requested).map_err(|_| {
                boxed(EngineUnavailable::new(
                    "configured Engine binary is unavailable",
                ))
            })?
        } else {
            let path = std::env::var_os("PATH").unwrap_or_default();
            let mut resolved = None;
            for entry in std::env::split_paths(&path) {
                if entry.as_os_str().is_empty() {
                    continue;
                }
                let candidate = entry.join(&requested);
                if candidate.is_file() {
                    resolved = fs::canonicalize(candidate).ok();
                    if resolved.is_some() {
                        break;
                    }
                }
            }
            resolved.ok_or_else(|| {
                boxed(EngineUnavailable::new(
                    "configured Engine binary is unavailable",
                ))
            })?
        };
        if !candidate.is_file() || !is_executable(&candidate) {
            return Err(boxed(EngineUnavailable::new(
                "configured Engine binary is unavailable",
            )));
        }
        Ok(candidate)
    }

    fn invoke(
        &self,
        operation: &str,
        project_root: &Path,
        request: &Value,
    ) -> SdkResult<ParsedResponse> {
        let root = self.validate_root(project_root)?;
        let payload = canonical_bytes(request).map_err(boxed)?;
        if payload.len() > MAX_REQUEST_BYTES {
            return Err(boxed(EngineProtocolError::new(
                "Engine request exceeds the bound",
            )));
        }
        let request_path = create_request_file(&root, &payload)?;
        let result = self.run(operation, &root, &request_path);
        let _ = fs::remove_file(&request_path);
        result.and_then(|raw| parse_response(&raw))
    }

    fn run(&self, operation: &str, project_root: &Path, request_path: &Path) -> SdkResult<Vec<u8>> {
        let binary = self.resolve_binary()?;
        let mut command = Command::new(binary);
        command
            .arg("engine")
            .arg(operation)
            .arg("--project-root")
            .arg(project_root)
            .arg("--json-file")
            .arg(request_path)
            .current_dir(project_root)
            .env_clear()
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .env("TZ", "UTC")
            .env("PYTHONHASHSEED", "0")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = command.spawn().map_err(|_| {
            boxed(EngineUnavailable::new(
                "Engine process could not be started",
            ))
        })?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| boxed(EngineProtocolError::new("Engine stdout is unavailable")))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| boxed(EngineProtocolError::new("Engine stderr is unavailable")))?;
        let overflow = Arc::new(AtomicBool::new(false));
        let stdout_overflow = Arc::clone(&overflow);
        let stdout_reader =
            thread::spawn(move || read_limited(stdout, MAX_RESPONSE_BYTES, stdout_overflow));
        let stderr_overflow = Arc::clone(&overflow);
        let stderr_reader =
            thread::spawn(move || read_limited(stderr, MAX_STDERR_BYTES, stderr_overflow));
        let deadline = Instant::now() + self.timeout;
        let status = loop {
            if overflow.load(Ordering::Acquire) {
                kill_and_reap(&mut child)?;
                let _ = stdout_reader.join();
                let _ = stderr_reader.join();
                return Err(boxed(EngineProtocolError::new(
                    "Engine process output exceeds its bound",
                )));
            }
            match child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) if Instant::now() >= deadline => {
                    kill_and_reap(&mut child)?;
                    let _ = stdout_reader.join();
                    let _ = stderr_reader.join();
                    return Err(boxed(EngineTimeout::new(
                        "Engine process exceeded its deadline",
                    )));
                }
                Ok(None) => thread::sleep(Duration::from_millis(2)),
                Err(_) => {
                    kill_and_reap(&mut child)?;
                    let _ = stdout_reader.join();
                    let _ = stderr_reader.join();
                    return Err(boxed(EngineExecutionError::new(
                        "Engine process status could not be read",
                    )));
                }
            }
        };
        let (stdout, stdout_overflowed) =
            stdout_reader.join().unwrap_or_else(|_| (Vec::new(), true));
        let (stderr, stderr_overflowed) =
            stderr_reader.join().unwrap_or_else(|_| (Vec::new(), true));
        if stdout_overflowed || stderr_overflowed || overflow.load(Ordering::Acquire) {
            return Err(boxed(EngineProtocolError::new(
                "Engine process output exceeds its bound",
            )));
        }
        if !status.success() {
            return Err(map_process_failure(&stderr));
        }
        if stdout.is_empty() {
            return Err(boxed(EngineProtocolError::new(
                "Engine returned empty stdout",
            )));
        }
        if std::str::from_utf8(&stdout).is_err() {
            return Err(boxed(EngineProtocolError::new(
                "Engine returned invalid UTF-8",
            )));
        }
        Ok(stdout)
    }

    fn build_view(&self, source: &ContextSource, parsed: ParsedResponse) -> SdkResult<ContextView> {
        let invocation = parsed
            .invocation
            .ok_or_else(|| boxed(EngineProtocolError::new("context-view omitted invocation")))?;
        let observation = parsed
            .observation
            .ok_or_else(|| boxed(EngineProtocolError::new("context-view omitted observation")))?;
        if !invocation
            .source_refs
            .iter()
            .any(|value| value == &parsed.recovery.source_ref)
        {
            return Err(boxed(EngineProtocolError::new(
                "recovery source_ref is not admitted by invocation",
            )));
        }
        if source.source_ref() != Some(parsed.recovery.source_ref.as_str())
            && source.source_ref().is_some()
        {
            return Err(boxed(EngineProtocolError::new(
                "Engine source_ref differs from requested binding",
            )));
        }
        if source.source_digest() != Some(parsed.recovery.source_digest.as_str())
            && source.source_digest().is_some()
        {
            return Err(boxed(EngineProtocolError::new(
                "Engine source_digest differs from requested binding",
            )));
        }
        if observation.source_lineage != invocation.source_refs {
            return Err(boxed(EngineProtocolError::new(
                "observation lineage mismatch",
            )));
        }
        let view = ContextView::new(
            source.clone(),
            Some(parsed.view.text),
            parsed.view.output_ref,
            parsed.view.output_digest,
            parsed.recovery.source_ref,
            parsed.recovery.source_digest,
            Some(parsed.recovery.recovery_ref),
            observation.status,
            observation.measurements,
            observation.failure,
            observation.receipt_link,
            invocation.value,
            observation.value,
        )?;
        if matches!(view.status(), EngineStatus::Succeeded) && !view.verify() {
            return Err(boxed(ArtifactIntegrityError::new(
                "succeeded Engine evidence is not sealed",
            )));
        }
        Ok(view)
    }
}

impl EngineClient for SubprocessEngineClient {
    fn context_view(&self, plan: &ContextPlan) -> SdkResult<ContextView> {
        let source = plan.source();
        let mut request = Map::new();
        request.insert("schema_version".to_owned(), Value::from(SCHEMA_VERSION));
        request.insert(
            "transport_version".to_owned(),
            Value::from(TRANSPORT_VERSION),
        );
        request.insert(
            "engine_interface_version".to_owned(),
            Value::String(ENGINE_INTERFACE_VERSION.to_owned()),
        );
        request.insert("path".to_owned(), Value::String(source.relative_path()?));
        request.insert("mode".to_owned(), Value::String(plan.mode().to_owned()));
        let parsed = self.invoke(
            "context-view",
            source.project_root(),
            &Value::Object(request),
        )?;
        let view = self.build_view(source, parsed)?;
        match view.status() {
            EngineStatus::Rejected => {
                let code = view
                    .failure()
                    .map(ContextFailure::code)
                    .map(|value| value.as_str())
                    .unwrap_or("rejected");
                match code {
                    "policy_rejected" => Err(boxed(PolicyAdmissionError::new(format!(
                        "Engine rejected request: {code}"
                    )))),
                    "source_unavailable" => Err(boxed(SourceUnavailableError::new(format!(
                        "Engine rejected request: {code}"
                    )))),
                    _ => Err(boxed(EngineRejected::new(format!(
                        "Engine rejected request: {code}"
                    )))),
                }
            }
            EngineStatus::Failed => {
                let code = view
                    .failure()
                    .map(ContextFailure::code)
                    .map(|value| value.as_str())
                    .unwrap_or("failed");
                match code {
                    "source_unavailable" => Err(boxed(SourceUnavailableError::new(format!(
                        "Engine execution failed: {code}"
                    )))),
                    "source_integrity_mismatch" => Err(boxed(ArtifactIntegrityError::new(
                        format!("Engine execution failed: {code}"),
                    ))),
                    "unsupported_operation" => Err(boxed(UnsupportedEngineError::new(format!(
                        "Engine execution failed: {code}"
                    )))),
                    _ => Err(boxed(EngineExecutionError::new(format!(
                        "Engine execution failed: {code}"
                    )))),
                }
            }
            EngineStatus::Succeeded | EngineStatus::Degraded => Ok(view),
        }
    }

    fn recover(
        &self,
        project_root: &Path,
        path: &str,
        recovery_ref: &str,
        source_ref: &str,
        source_digest: &str,
    ) -> SdkResult<RecoveredSource> {
        let root = self.validate_root(project_root)?;
        let relative_path = safe_relative_path(path)?;
        let recovery_ref = required_ref(recovery_ref, "recovery_ref")?;
        let source_ref = required_ref(source_ref, "source_ref")?;
        let source_digest = validate_digest(source_digest, "source_digest")
            .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))?;
        let mut request = Map::new();
        request.insert("schema_version".to_owned(), Value::from(SCHEMA_VERSION));
        request.insert(
            "transport_version".to_owned(),
            Value::from(TRANSPORT_VERSION),
        );
        request.insert(
            "engine_interface_version".to_owned(),
            Value::String(ENGINE_INTERFACE_VERSION.to_owned()),
        );
        request.insert("path".to_owned(), Value::String(relative_path));
        request.insert(
            "recovery_ref".to_owned(),
            Value::String(recovery_ref.clone()),
        );
        request.insert("source_ref".to_owned(), Value::String(source_ref.clone()));
        request.insert(
            "source_digest".to_owned(),
            Value::String(source_digest.clone()),
        );
        let parsed = self.invoke("recover", &root, &Value::Object(request))?;
        if parsed.invocation.is_some() || parsed.observation.is_some() {
            return Err(boxed(EngineProtocolError::new(
                "recover response must have null invocation/observation",
            )));
        }
        if parsed.recovery.recovery_ref != recovery_ref
            || parsed.recovery.source_ref != source_ref
            || parsed.recovery.source_digest != source_digest
        {
            return Err(boxed(ArtifactIntegrityError::new(
                "recover response binding mismatch",
            )));
        }
        if parsed.view.output_digest.as_deref() != Some(source_digest.as_str()) {
            return Err(boxed(ArtifactIntegrityError::new(
                "recover output digest does not match source digest",
            )));
        }
        let expected_output_ref = format!("output:{}", &source_digest[7..]);
        if parsed
            .view
            .output_ref
            .as_deref()
            .is_some_and(|value| value != expected_output_ref)
        {
            return Err(boxed(ArtifactIntegrityError::new(
                "recover output reference does not match source digest",
            )));
        }
        RecoveredSource::new(parsed.view.text, source_ref, source_digest, recovery_ref)
            .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))
    }
}

impl Default for SubprocessEngineClient {
    fn default() -> Self {
        Self {
            engine_binary: None,
            timeout: Duration::from_secs(30),
        }
    }
}

#[derive(Debug)]
struct WireView {
    text: String,
    output_ref: Option<String>,
    output_digest: Option<String>,
}

#[derive(Debug)]
struct WireInvocation {
    value: Value,
    invocation_id: String,
    source_refs: Vec<String>,
}

#[derive(Debug)]
struct WireObservation {
    value: Value,
    status: EngineStatus,
    source_lineage: Vec<String>,
    measurements: Vec<ContextMeasurement>,
    failure: Option<ContextFailure>,
    receipt_link: Option<ContextReceiptLink>,
}

#[derive(Debug)]
struct WireRecovery {
    recovery_ref: String,
    source_ref: String,
    source_digest: String,
}

#[derive(Debug)]
struct ParsedResponse {
    view: WireView,
    invocation: Option<WireInvocation>,
    observation: Option<WireObservation>,
    recovery: WireRecovery,
}

fn parse_response(raw: &[u8]) -> SdkResult<ParsedResponse> {
    if raw.len() > MAX_RESPONSE_BYTES {
        return Err(boxed(EngineProtocolError::new(
            "Engine response exceeds the bound",
        )));
    }
    let decoded = strict_json_loads(raw, "Engine response")
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))?;
    exact_keys(
        &decoded,
        &[
            "schema_version",
            "transport_version",
            "engine_interface_version",
            "view",
            "invocation",
            "observation",
            "recovery",
        ],
        "Engine response",
    )?;
    let object = decoded
        .as_object()
        .ok_or_else(|| boxed(EngineProtocolError::new("Engine response is not an object")))?;
    let schema = json_integer(
        object.get("schema_version").unwrap_or(&Value::Null),
        "response.schema_version",
    )?;
    if schema != SCHEMA_VERSION {
        return Err(boxed(CompatibilityError::new("unsupported schema version")));
    }
    let transport = json_integer(
        object.get("transport_version").unwrap_or(&Value::Null),
        "response.transport_version",
    )?;
    if transport != TRANSPORT_VERSION {
        return Err(boxed(CompatibilityError::new(
            "unsupported transport version",
        )));
    }
    if object
        .get("engine_interface_version")
        .and_then(Value::as_str)
        != Some(ENGINE_INTERFACE_VERSION)
    {
        return Err(boxed(CompatibilityError::new(
            "unsupported Engine Interface version",
        )));
    }
    let view = parse_view(object.get("view").unwrap_or(&Value::Null))?;
    let recovery = parse_recovery(object.get("recovery").unwrap_or(&Value::Null))?;
    let invocation_value = object.get("invocation").unwrap_or(&Value::Null);
    let observation_value = object.get("observation").unwrap_or(&Value::Null);
    if invocation_value.is_null() || observation_value.is_null() {
        if !invocation_value.is_null() || !observation_value.is_null() {
            return Err(boxed(EngineProtocolError::new(
                "invocation and observation must both be null or present",
            )));
        }
        return Ok(ParsedResponse {
            view,
            invocation: None,
            observation: None,
            recovery,
        });
    }
    let invocation = parse_invocation(invocation_value)?;
    let observation = parse_observation(observation_value, &invocation.invocation_id)?;
    if observation.source_lineage != invocation.source_refs {
        return Err(boxed(EngineProtocolError::new(
            "observation source lineage does not match invocation",
        )));
    }
    if observation.value.get("output_ref") != Some(&option_to_value(&view.output_ref))
        || observation.value.get("output_digest") != Some(&option_to_value(&view.output_digest))
    {
        return Err(boxed(EngineProtocolError::new(
            "view and observation output binding mismatch",
        )));
    }
    Ok(ParsedResponse {
        view,
        invocation: Some(invocation),
        observation: Some(observation),
        recovery,
    })
}

fn parse_view(value: &Value) -> SdkResult<WireView> {
    exact_keys(value, &["text", "output_ref", "output_digest"], "view")?;
    let object = value
        .as_object()
        .ok_or_else(|| boxed(EngineProtocolError::new("view must be an object")))?;
    let text = json_string(
        object.get("text").unwrap_or(&Value::Null),
        "view.text",
        MAX_TEXT_BYTES,
    )?;
    let output_ref = optional_output_ref(object.get("output_ref"), "view.output_ref")?;
    let output_digest = optional_digest(object.get("output_digest"), "view.output_digest")?;
    validate_output_pair(&output_ref, &output_digest, "view")?;
    if let Some(digest) = &output_digest {
        if sha256_digest(text.as_bytes()) != *digest {
            return Err(boxed(EngineProtocolError::new(
                "view output digest mismatch",
            )));
        }
    }
    Ok(WireView {
        text,
        output_ref,
        output_digest,
    })
}

fn parse_recovery(value: &Value) -> SdkResult<WireRecovery> {
    exact_keys(
        value,
        &["recovery_ref", "source_ref", "source_digest"],
        "recovery",
    )?;
    let object = value
        .as_object()
        .ok_or_else(|| boxed(EngineProtocolError::new("recovery must be an object")))?;
    Ok(WireRecovery {
        recovery_ref: required_ref_value(object.get("recovery_ref"), "recovery.recovery_ref")?,
        source_ref: required_ref_value(object.get("source_ref"), "recovery.source_ref")?,
        source_digest: required_digest_value(
            object.get("source_digest"),
            "recovery.source_digest",
        )?,
    })
}

fn parse_invocation(value: &Value) -> SdkResult<WireInvocation> {
    exact_keys(
        value,
        &[
            "schema_version",
            "invocation_id",
            "engine",
            "operation",
            "input_ref",
            "input_digest",
            "source_refs",
            "policy_admission",
        ],
        "invocation",
    )?;
    let object = value
        .as_object()
        .ok_or_else(|| boxed(EngineProtocolError::new("invocation must be an object")))?;
    if json_integer(
        object.get("schema_version").unwrap_or(&Value::Null),
        "invocation.schema_version",
    )? != SCHEMA_VERSION
    {
        return Err(boxed(EngineProtocolError::new(
            "unsupported invocation schema version",
        )));
    }
    let invocation_id = json_string(
        object.get("invocation_id").unwrap_or(&Value::Null),
        "invocation.invocation_id",
        crate::protocol::MAX_REF_BYTES,
    )?;
    let engine = object
        .get("engine")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "invocation.engine must be an object",
            ))
        })?;
    exact_keys(
        &Value::Object(engine.clone()),
        &["engine_id", "engine_version"],
        "invocation.engine",
    )?;
    let engine_id = json_string(
        engine.get("engine_id").unwrap_or(&Value::Null),
        "invocation.engine.engine_id",
        crate::protocol::MAX_REF_BYTES,
    )?;
    let engine_version = json_string(
        engine.get("engine_version").unwrap_or(&Value::Null),
        "invocation.engine.engine_version",
        crate::protocol::MAX_REF_BYTES,
    )?;
    if engine_id != "lean-ctx-local" || !is_semver(&engine_version) {
        return Err(boxed(UnsupportedEngineError::new(
            "unsupported Engine identity",
        )));
    }
    if engine_version.split('.').next() != Some("3") {
        return Err(boxed(UnsupportedEngineError::new(
            "unsupported Engine major version",
        )));
    }
    let operation = object
        .get("operation")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "invocation.operation must be an object",
            ))
        })?;
    exact_keys(
        &Value::Object(operation.clone()),
        &["capability_id", "capability_version"],
        "invocation.operation",
    )?;
    let capability_id = json_string(
        operation.get("capability_id").unwrap_or(&Value::Null),
        "invocation.operation.capability_id",
        crate::protocol::MAX_REF_BYTES,
    )?;
    let capability_version = json_string(
        operation.get("capability_version").unwrap_or(&Value::Null),
        "invocation.operation.capability_version",
        crate::protocol::MAX_REF_BYTES,
    )?;
    if capability_id != "capability://leanctx/context-optimization" || capability_version != "1.0.0"
    {
        return Err(boxed(UnsupportedEngineError::new(
            "unsupported Engine capability",
        )));
    }
    let policy = object
        .get("policy_admission")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "invocation.policy_admission must be an object",
            ))
        })?;
    exact_keys(
        &Value::Object(policy.clone()),
        &["policy_ref", "decision"],
        "invocation.policy_admission",
    )?;
    let policy_ref = required_ref_value(
        policy.get("policy_ref"),
        "invocation.policy_admission.policy_ref",
    )?;
    let decision = policy
        .get("decision")
        .and_then(Value::as_str)
        .ok_or_else(|| boxed(EngineProtocolError::new("unknown policy decision")))?;
    if !matches!(decision, "admitted" | "rejected") {
        return Err(boxed(EngineProtocolError::new("unknown policy decision")));
    }
    let input_ref = required_ref_value(object.get("input_ref"), "invocation.input_ref")?;
    let input_digest =
        required_digest_value(object.get("input_digest"), "invocation.input_digest")?;
    let source_refs = required_refs(object.get("source_refs"), "invocation.source_refs")?;
    if !source_refs.iter().any(|value| value == &input_ref) {
        return Err(boxed(EngineProtocolError::new(
            "invocation input_ref is not in source_refs",
        )));
    }
    let mut normalized = Map::new();
    normalized.insert("schema_version".to_owned(), Value::from(SCHEMA_VERSION));
    normalized.insert(
        "invocation_id".to_owned(),
        Value::String(invocation_id.clone()),
    );
    normalized.insert(
        "engine".to_owned(),
        Value::Object(
            [
                ("engine_id".to_owned(), Value::String(engine_id)),
                ("engine_version".to_owned(), Value::String(engine_version)),
            ]
            .into_iter()
            .collect(),
        ),
    );
    normalized.insert(
        "operation".to_owned(),
        Value::Object(
            [
                ("capability_id".to_owned(), Value::String(capability_id)),
                (
                    "capability_version".to_owned(),
                    Value::String(capability_version),
                ),
            ]
            .into_iter()
            .collect(),
        ),
    );
    normalized.insert("input_ref".to_owned(), Value::String(input_ref));
    normalized.insert("input_digest".to_owned(), Value::String(input_digest));
    normalized.insert(
        "source_refs".to_owned(),
        Value::Array(source_refs.iter().cloned().map(Value::String).collect()),
    );
    normalized.insert(
        "policy_admission".to_owned(),
        Value::Object(
            [
                ("policy_ref".to_owned(), Value::String(policy_ref)),
                ("decision".to_owned(), Value::String(decision.to_owned())),
            ]
            .into_iter()
            .collect(),
        ),
    );
    Ok(WireInvocation {
        value: Value::Object(normalized),
        invocation_id,
        source_refs,
    })
}

fn parse_observation(value: &Value, invocation_id: &str) -> SdkResult<WireObservation> {
    let object = value
        .as_object()
        .ok_or_else(|| boxed(EngineProtocolError::new("observation must be an object")))?;
    let allowed = [
        "schema_version",
        "invocation_id",
        "status",
        "output_ref",
        "output_digest",
        "source_lineage",
        "measurements",
        "failure",
        "receipt_link",
    ];
    let required = [
        "schema_version",
        "invocation_id",
        "status",
        "source_lineage",
        "measurements",
    ];
    if object.keys().any(|key| !allowed.contains(&key.as_str()))
        || required.iter().any(|key| !object.contains_key(*key))
    {
        return Err(boxed(EngineProtocolError::new(
            "observation fields do not match the v1 contract",
        )));
    }
    if json_integer(
        object.get("schema_version").unwrap_or(&Value::Null),
        "observation.schema_version",
    )? != SCHEMA_VERSION
    {
        return Err(boxed(EngineProtocolError::new(
            "unsupported observation schema version",
        )));
    }
    let observed_invocation_id = json_string(
        object.get("invocation_id").unwrap_or(&Value::Null),
        "observation.invocation_id",
        crate::protocol::MAX_REF_BYTES,
    )?;
    if observed_invocation_id != invocation_id {
        return Err(boxed(EngineProtocolError::new(
            "observation invocation binding mismatch",
        )));
    }
    let status = object
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| boxed(EngineProtocolError::new("unknown observation status")))?
        .parse::<EngineStatus>()
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))?;
    let output_ref = optional_output_ref(object.get("output_ref"), "observation.output_ref")?;
    let output_digest = optional_digest(object.get("output_digest"), "observation.output_digest")?;
    validate_output_pair(&output_ref, &output_digest, "observation")?;
    let source_lineage = required_refs(object.get("source_lineage"), "observation.source_lineage")?;
    let measurement_values = object
        .get("measurements")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "observation.measurements must be an array",
            ))
        })?;
    if measurement_values.len() > MAX_MEASUREMENTS {
        return Err(boxed(EngineProtocolError::new(
            "observation.measurements exceeds its bound",
        )));
    }
    let measurements = measurement_values
        .iter()
        .map(parse_measurement)
        .collect::<SdkResult<Vec<_>>>()?;
    let failure = parse_failure(object.get("failure"))?;
    let receipt_link = parse_receipt_link(object.get("receipt_link"), invocation_id)?;
    if matches!(status, EngineStatus::Succeeded | EngineStatus::Degraded) && failure.is_some() {
        return Err(boxed(EngineProtocolError::new(
            "successful/degraded observation cannot contain failure",
        )));
    }
    if matches!(status, EngineStatus::Failed | EngineStatus::Rejected) && failure.is_none() {
        return Err(boxed(EngineProtocolError::new(
            "failed/rejected observation requires failure",
        )));
    }
    if matches!(status, EngineStatus::Succeeded) && receipt_link.is_none() {
        return Err(boxed(EngineProtocolError::new(
            "succeeded observation requires receipt_link",
        )));
    }
    let mut normalized = Map::new();
    normalized.insert("schema_version".to_owned(), Value::from(SCHEMA_VERSION));
    normalized.insert(
        "invocation_id".to_owned(),
        Value::String(observed_invocation_id),
    );
    normalized.insert("status".to_owned(), Value::String(status.to_string()));
    normalized.insert("output_ref".to_owned(), option_to_value(&output_ref));
    normalized.insert("output_digest".to_owned(), option_to_value(&output_digest));
    normalized.insert(
        "source_lineage".to_owned(),
        Value::Array(source_lineage.iter().cloned().map(Value::String).collect()),
    );
    normalized.insert(
        "measurements".to_owned(),
        Value::Array(
            measurements
                .iter()
                .map(ContextMeasurement::to_value)
                .collect(),
        ),
    );
    normalized.insert(
        "failure".to_owned(),
        failure
            .as_ref()
            .map_or(Value::Null, ContextFailure::to_value),
    );
    normalized.insert(
        "receipt_link".to_owned(),
        receipt_link
            .as_ref()
            .map_or(Value::Null, ContextReceiptLink::to_value),
    );
    Ok(WireObservation {
        value: Value::Object(normalized),
        status,
        source_lineage,
        measurements,
        failure,
        receipt_link,
    })
}

fn parse_measurement(value: &Value) -> SdkResult<ContextMeasurement> {
    exact_keys(
        value,
        &["name", "unit", "classification", "value"],
        "measurement",
    )?;
    let object = value
        .as_object()
        .ok_or_else(|| boxed(EngineProtocolError::new("measurement must be an object")))?;
    let name = json_string(
        object.get("name").unwrap_or(&Value::Null),
        "measurement.name",
        crate::protocol::MAX_REF_BYTES,
    )?;
    let unit = json_string(
        object.get("unit").unwrap_or(&Value::Null),
        "measurement.unit",
        crate::protocol::MAX_REF_BYTES,
    )?;
    let classification = json_string(
        object.get("classification").unwrap_or(&Value::Null),
        "measurement.classification",
        crate::protocol::MAX_REF_BYTES,
    )?;
    let measurement_value = object.get("value").unwrap_or(&Value::Null);
    let number = if measurement_value.is_null() {
        None
    } else {
        Some(json_integer(measurement_value, "measurement.value")?)
    };
    ContextMeasurement::new(name, unit, classification, number)
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))
}

fn parse_failure(value: Option<&Value>) -> SdkResult<Option<ContextFailure>> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    exact_keys(
        value,
        &["code", "retryable_by_host", "recovery_ref"],
        "failure",
    )?;
    let object = value.as_object().ok_or_else(|| {
        boxed(EngineProtocolError::new(
            "failure must be an object or null",
        ))
    })?;
    let code = object
        .get("code")
        .and_then(Value::as_str)
        .ok_or_else(|| boxed(EngineProtocolError::new("unknown Engine failure code")))?
        .parse::<FailureCode>()
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))?;
    let retryable = object
        .get("retryable_by_host")
        .and_then(Value::as_bool)
        .ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "retryable_by_host must be boolean",
            ))
        })?;
    let recovery_ref = optional_ref(object.get("recovery_ref"), "failure.recovery_ref")?;
    ContextFailure::new(code, retryable, recovery_ref)
        .map(Some)
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))
}

fn parse_receipt_link(
    value: Option<&Value>,
    invocation_id: &str,
) -> SdkResult<Option<ContextReceiptLink>> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    exact_keys(
        value,
        &[
            "schema_version",
            "receipt_id",
            "receipt_ref",
            "receipt_digest",
            "invocation_id",
        ],
        "receipt_link",
    )?;
    let object = value.as_object().ok_or_else(|| {
        boxed(EngineProtocolError::new(
            "receipt_link must be an object or null",
        ))
    })?;
    let schema = json_integer(
        object.get("schema_version").unwrap_or(&Value::Null),
        "receipt_link.schema_version",
    )?;
    let receipt_id = required_ref_value(object.get("receipt_id"), "receipt_link.receipt_id")?;
    let receipt_ref = required_ref_value(object.get("receipt_ref"), "receipt_link.receipt_ref")?;
    let receipt_digest =
        required_digest_value(object.get("receipt_digest"), "receipt_link.receipt_digest")?;
    if receipt_ref != format!("receipt:{receipt_digest}") {
        return Err(boxed(EngineProtocolError::new(
            "receipt_link.receipt_ref does not match digest",
        )));
    }
    let linked_invocation = json_string(
        object.get("invocation_id").unwrap_or(&Value::Null),
        "receipt_link.invocation_id",
        crate::protocol::MAX_REF_BYTES,
    )?;
    if linked_invocation != invocation_id {
        return Err(boxed(EngineProtocolError::new(
            "receipt_link invocation binding mismatch",
        )));
    }
    ContextReceiptLink::new(
        schema,
        receipt_id,
        receipt_ref,
        receipt_digest,
        linked_invocation,
    )
    .map(Some)
    .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))
}

fn required_refs(value: Option<&Value>, field_name: &str) -> SdkResult<Vec<String>> {
    let values = value.and_then(Value::as_array).ok_or_else(|| {
        boxed(EngineProtocolError::new(format!(
            "{field_name} must be an array"
        )))
    })?;
    if values.is_empty() || values.len() > MAX_REFS {
        return Err(boxed(EngineProtocolError::new(format!(
            "{field_name} exceeds its bound"
        ))));
    }
    let mut result = Vec::with_capacity(values.len());
    for value in values {
        result.push(required_ref_value(Some(value), field_name)?);
    }
    let unique: BTreeSet<&str> = result.iter().map(String::as_str).collect();
    if unique.len() != result.len() {
        return Err(boxed(EngineProtocolError::new(format!(
            "{field_name} contains duplicates"
        ))));
    }
    Ok(result)
}

fn required_ref_value(value: Option<&Value>, field_name: &str) -> SdkResult<String> {
    let value = value.and_then(Value::as_str).ok_or_else(|| {
        boxed(EngineProtocolError::new(format!(
            "{field_name} must be a ref"
        )))
    })?;
    required_ref(value, field_name)
}

fn required_ref(value: &str, field_name: &str) -> SdkResult<String> {
    validate_ref(value, field_name)
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))
}

fn required_digest_value(value: Option<&Value>, field_name: &str) -> SdkResult<String> {
    let value = value.and_then(Value::as_str).ok_or_else(|| {
        boxed(EngineProtocolError::new(format!(
            "{field_name} is required"
        )))
    })?;
    validate_digest(value, field_name)
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))
}

fn optional_ref(value: Option<&Value>, field_name: &str) -> SdkResult<Option<String>> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => required_ref(value, field_name).map(Some),
        Some(_) => Err(boxed(EngineProtocolError::new(format!(
            "{field_name} must be a ref or null"
        )))),
    }
}

fn optional_digest(value: Option<&Value>, field_name: &str) -> SdkResult<Option<String>> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => validate_digest(value, field_name)
            .map(Some)
            .map_err(|error| boxed(EngineProtocolError::new(error.to_string()))),
        Some(_) => Err(boxed(EngineProtocolError::new(format!(
            "{field_name} must be a digest or null"
        )))),
    }
}

fn optional_output_ref(value: Option<&Value>, field_name: &str) -> SdkResult<Option<String>> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => validate_output_ref(value, field_name)
            .map(Some)
            .map_err(|error| boxed(EngineProtocolError::new(error.to_string()))),
        Some(_) => Err(boxed(EngineProtocolError::new(format!(
            "{field_name} must be an output ref or null"
        )))),
    }
}

fn validate_output_pair(
    output_ref: &Option<String>,
    output_digest: &Option<String>,
    label: &str,
) -> SdkResult<()> {
    if output_ref.is_some() && output_digest.is_none() {
        return Err(boxed(EngineProtocolError::new(format!(
            "{label} output reference requires a digest"
        ))));
    }
    if let (Some(output_ref), Some(output_digest)) = (output_ref, output_digest) {
        if output_ref != &format!("output:{}", &output_digest[7..]) {
            return Err(boxed(EngineProtocolError::new(format!(
                "{label} output reference does not match digest"
            ))));
        }
    }
    Ok(())
}

fn option_to_value<T>(value: &Option<T>) -> Value
where
    T: Clone + Into<Value>,
{
    value.clone().map_or(Value::Null, Into::into)
}

fn is_semver(value: &str) -> bool {
    let mut parts = value.split('.');
    let Some(first) = parts.next() else {
        return false;
    };
    let Some(second) = parts.next() else {
        return false;
    };
    let Some(third) = parts.next() else {
        return false;
    };
    parts.next().is_none()
        && [first, second, third]
            .iter()
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
}

fn safe_relative_path(path: &str) -> SdkResult<String> {
    if path.is_empty()
        || path.len() > MAX_PATH_BYTES
        || path.contains('\0')
        || path.chars().any(|character| character < '\u{20}')
        || Path::new(path).is_absolute()
    {
        return Err(boxed(EngineProtocolError::new(
            "path must be a rooted relative path",
        )));
    }
    let normalized = normalize_path(Path::new(path));
    if normalized == Path::new(".")
        || normalized == Path::new("..")
        || normalized.starts_with("../")
        || normalized.is_absolute()
    {
        return Err(boxed(EngineProtocolError::new("path escapes project root")));
    }
    Ok(normalized.to_string_lossy().replace('\\', "/"))
}

fn create_request_file(root: &Path, payload: &[u8]) -> SdkResult<PathBuf> {
    static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(0);
    for _ in 0..100 {
        let counter = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = root.join(format!(
            ".leanctx-sdk-{}-{counter}.json",
            std::process::id()
        ));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        match options.open(&path) {
            Ok(mut file) => {
                if let Err(error) = write_sync(&mut file, payload) {
                    let _ = fs::remove_file(&path);
                    return Err(boxed(EngineUnavailable::new(format!(
                        "Engine request file could not be written: {error}"
                    ))));
                }
                return Ok(path);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => {
                return Err(boxed(EngineUnavailable::new(
                    "Engine request file could not be created",
                )))
            }
        }
    }
    Err(boxed(EngineUnavailable::new(
        "Engine request file could not be created",
    )))
}

fn write_sync(file: &mut File, payload: &[u8]) -> std::io::Result<()> {
    file.write_all(payload)?;
    file.flush()?;
    file.sync_all()
}

fn read_limited<R: Read>(
    mut reader: R,
    maximum: usize,
    overflow: Arc<AtomicBool>,
) -> (Vec<u8>, bool) {
    let mut result = Vec::new();
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        match reader.read(&mut chunk) {
            Ok(0) => return (result, false),
            Ok(size) => {
                if result.len().saturating_add(size) > maximum {
                    overflow.store(true, Ordering::Release);
                    return (result, true);
                }
                result.extend_from_slice(&chunk[..size]);
            }
            Err(_) => return (result, false),
        }
    }
}

fn kill_and_reap(child: &mut Child) -> SdkResult<()> {
    terminate_process_tree(child)
}

fn map_process_failure(stderr: &[u8]) -> Box<dyn std::error::Error + Send + Sync> {
    let code = stderr_code(stderr).unwrap_or("nonzero_exit");
    match code {
        "unsafe_root" | "source_outside_root" | "source_symlink" | "policy_rejected" => boxed(
            PolicyAdmissionError::new(format!("Engine rejected request: {code}")),
        ),
        "source_unavailable" => boxed(SourceUnavailableError::new("Engine source is unavailable")),
        "unsupported_mode" => boxed(UnsupportedEngineError::new(
            "Engine operation is unsupported",
        )),
        _ => boxed(EngineExecutionError::new(format!(
            "Engine process failed: {code}"
        ))),
    }
}

fn stderr_code(stderr: &[u8]) -> Option<&str> {
    let text = std::str::from_utf8(stderr).ok()?;
    for line in text.lines() {
        let Some(value) = line.strip_prefix("engine:") else {
            continue;
        };
        let value = value.trim();
        if !value.is_empty()
            && value
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
        {
            return Some(value);
        }
    }
    None
}

fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::metadata(path)
            .map(|metadata| metadata.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

fn validate_timeout(timeout: Duration) -> SdkResult<()> {
    if timeout < Duration::from_millis(100) || timeout > Duration::from_secs(120) {
        return Err(boxed(ValidationError::new(
            "timeout must be between 0.1 and 120 seconds",
        )));
    }
    Ok(())
}
