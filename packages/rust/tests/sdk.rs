use std::collections::BTreeMap;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use leanctx_sdk::{
    AgentContext, AgentPermissions, ContextFailure, ContextMeasurement, ContextPlan,
    ContextReceipt, ContextReceiptLink, ContextSession, ContextSource, ContextView, EngineClient,
    EngineStatus, ExecutionPolicy, FailureCode, Freshness, HostOutcome, Integrity, ReadMode,
    RecoveredSource, SessionState, SubprocessEngineClient, ValidationError,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new() -> Result<Self, Box<dyn Error + Send + Sync>> {
        for _ in 0..100 {
            let number = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "leanctx-rust-sdk-test-{}-{number}",
                std::process::id()
            ));
            match fs::create_dir(&path) {
                Ok(()) => return Ok(Self { path }),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err("could not create an isolated test directory".into())
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn digest(text: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(text.as_bytes()))
}

fn canonical_hash(value: &Value) -> String {
    format!("{:x}", Sha256::digest(serde_json::to_vec(value).unwrap()))
}

fn synthetic_source() -> ContextSource {
    ContextSource::new("fixture/source.txt", "/PROJECT").unwrap()
}

fn synthetic_view(source: &ContextSource) -> ContextView {
    let text = "fresh synthetic view\n";
    let output_digest = digest(text);
    let source_ref = format!("source:synthetic-path-sha256:{}", "a".repeat(64));
    let input_ref = format!("input:synthetic-request-sha256:{}", "b".repeat(64));
    let invocation_id = "engine-invocation-synthetic";
    let invocation = json!({
        "schema_version": 1,
        "invocation_id": invocation_id,
        "engine": {"engine_id": "lean-ctx-local", "engine_version": "3.9.20"},
        "operation": {
            "capability_id": "capability://leanctx/context-optimization",
            "capability_version": "1.0.0"
        },
        "input_ref": input_ref,
        "input_digest": format!("sha256:{}", "c".repeat(64)),
        "source_refs": [input_ref, source_ref],
        "policy_admission": {"policy_ref": "policy:synthetic", "decision": "admitted"}
    });
    let measurements = vec![
        ContextMeasurement::new("input_tokens", "token", "measured", Some(1)).unwrap(),
        ContextMeasurement::new("output_tokens", "token", "measured", Some(2)).unwrap(),
    ];
    let receipt_link = ContextReceiptLink::new(
        1,
        "engine-receipt-synthetic",
        format!("receipt:sha256:{}", "d".repeat(64)),
        format!("sha256:{}", "d".repeat(64)),
        invocation_id,
    )
    .unwrap();
    let observation = json!({
        "schema_version": 1,
        "invocation_id": invocation_id,
        "status": "succeeded",
        "output_ref": format!("output:{}", &output_digest[7..]),
        "output_digest": output_digest,
        "source_lineage": [input_ref, source_ref],
        "measurements": [
            {"name": "input_tokens", "unit": "token", "classification": "measured", "value": 1},
            {"name": "output_tokens", "unit": "token", "classification": "measured", "value": 2}
        ],
        "failure": null,
        "receipt_link": {
            "schema_version": 1,
            "receipt_id": "engine-receipt-synthetic",
            "receipt_ref": format!("receipt:sha256:{}", "d".repeat(64)),
            "receipt_digest": format!("sha256:{}", "d".repeat(64)),
            "invocation_id": invocation_id
        }
    });
    ContextView::new(
        source.clone(),
        Some(text.to_owned()),
        Some(format!("output:{}", &output_digest[7..])),
        Some(output_digest),
        source_ref,
        digest("fresh synthetic source\n"),
        Some(input_ref),
        EngineStatus::Succeeded,
        measurements,
        None,
        Some(receipt_link),
        invocation,
        observation,
    )
    .unwrap()
}

#[derive(Clone, Debug)]
struct FakeClient {
    view: ContextView,
}

impl EngineClient for FakeClient {
    fn context_view(
        &self,
        _plan: &ContextPlan,
    ) -> Result<ContextView, Box<dyn Error + Send + Sync>> {
        Ok(self.view.clone())
    }

    fn recover(
        &self,
        _project_root: &Path,
        _path: &str,
        _recovery_ref: &str,
        _source_ref: &str,
        _source_digest: &str,
    ) -> Result<RecoveredSource, Box<dyn Error + Send + Sync>> {
        Err(Box::new(ValidationError::new(
            "fake client does not provide recovery",
        )))
    }
}

#[test]
fn product_primitives_match_frozen_serialization_fingerprints(
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let source = synthetic_source();
    let plan = ContextPlan::new("session-fixed", "task-fixed", "inspect", source.clone())?;
    let view = synthetic_view(&source);
    let receipt = ContextReceipt::new(
        "session-fixed",
        "task-fixed",
        Some(plan.plan_id().to_owned()),
        Some(view.clone()),
        HostOutcome::Completed,
        Integrity::Sealed,
        Vec::new(),
        Some(json!({"requests": 1})),
        None,
        None,
    )?;
    let session = json!({
        "session_id": "session-fixed",
        "task_id": "task-fixed",
        "task": "inspect",
        "state": SessionState::Created.to_string()
    });
    let actual = BTreeMap::from([
        ("ContextPlan", canonical_hash(&plan.to_dict()?)),
        ("ContextReceipt", canonical_hash(&receipt.to_dict())),
        ("ContextSession", canonical_hash(&session)),
        ("ContextSource", canonical_hash(&source.to_dict()?)),
        ("ContextView", canonical_hash(&view.to_dict())),
    ]);
    let expected: BTreeMap<&str, String> = BTreeMap::from([
        (
            "ContextPlan",
            "a948177b44cfd1fd22b5aa59bd4d0210510675eb0742d219ac2ac36ed09a6d75".to_owned(),
        ),
        (
            "ContextReceipt",
            "0edf6bdc1afd5eb605a01900a99ff1d18579d98ba09719c4397d7366bfeca963".to_owned(),
        ),
        (
            "ContextSession",
            "219d600e70f8421386b034395f7db4e8d6494cb14d57cd34e63058e51834735c".to_owned(),
        ),
        (
            "ContextSource",
            "814ab90ae5f1ab6e93d1f447c703572c04174f7c0dccdd8939daeb304828ee9f".to_owned(),
        ),
        (
            "ContextView",
            "b80a6a0055e6ff06724f99990d59f03bbd4cf407d0143085a858cd1949b18918".to_owned(),
        ),
    ]);
    assert_eq!(actual, expected);
    Ok(())
}

#[test]
fn product_lifecycle_is_idempotent_and_receipt_stays_sealed(
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let source = synthetic_source();
    let view = synthetic_view(&source);
    let mut session = ContextSession::new_with_configuration(
        "inspect",
        Some(PathBuf::from("/PROJECT")),
        Some("session-fixed".to_owned()),
        Some("task-fixed".to_owned()),
        false,
        Box::new(FakeClient { view }),
    )?;
    session.plan_for(source.clone(), "aggressive", Freshness::Reuse)?;
    assert_eq!(session.state(), SessionState::Planned);
    assert!(session.prepare(&source)?.is_some());
    assert_eq!(session.state(), SessionState::Executing);
    let first_receipt_dict = {
        let first_receipt =
            session.complete(HostOutcome::Unknown, None, Some(json!({"requests": 1})))?;
        assert!(first_receipt.verify());
        assert_eq!(first_receipt.integrity_status(), Integrity::Sealed);
        first_receipt.to_dict()
    };
    let receipt_again =
        session.complete(HostOutcome::Unknown, None, Some(json!({"requests": 1})))?;
    assert_eq!(receipt_again.to_dict(), first_receipt_dict);
    assert!(session
        .complete(HostOutcome::Completed, None, None)
        .is_err());
    assert!(session.prepare(&source).is_err());
    Ok(())
}

#[test]
fn strict_product_validation_rejects_escape_and_invalid_bindings(
) -> Result<(), Box<dyn Error + Send + Sync>> {
    assert!(ContextSource::new("../escape.txt", "/PROJECT").is_err());
    assert!(ContextMeasurement::new("not lowercase", "token", "measured", Some(1)).is_err());
    assert!(ContextFailure::new(FailureCode::SourceUnavailable, true, None::<&str>).is_ok());
    let source = synthetic_source();
    let view = synthetic_view(&source);
    let mut forged = view.to_dict();
    forged["output_digest"] = Value::String(digest("forged"));
    assert!(!forged["output_digest"].is_null());
    assert!(!view.verify() || view.output_digest().is_some());
    Ok(())
}

#[cfg(unix)]
fn write_executable(root: &TempRoot, body: &str) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
    use std::os::unix::fs::PermissionsExt;

    let path = root.path().join("fake-engine.sh");
    fs::write(&path, format!("#!/bin/sh\nset -eu\n{body}\n"))?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
    Ok(path)
}

#[cfg(unix)]
fn engine_response(recover: bool) -> String {
    let source_text = "fresh synthetic source\n";
    let output_text = if recover {
        source_text
    } else {
        "fresh synthetic view\n"
    };
    let output_digest = digest(output_text);
    let source_digest = digest(source_text);
    let source_ref = format!("source:synthetic-path-sha256:{}", "a".repeat(64));
    let input_ref = format!("input:synthetic-request-sha256:{}", "b".repeat(64));
    let view = json!({
        "text": output_text,
        "output_ref": format!("output:{}", &output_digest[7..]),
        "output_digest": output_digest
    });
    let (invocation, observation) = if recover {
        (Value::Null, Value::Null)
    } else {
        let invocation_id = "engine-invocation-synthetic";
        (
            json!({
                "schema_version": 1,
                "invocation_id": invocation_id,
                "engine": {"engine_id": "lean-ctx-local", "engine_version": "3.9.20"},
                "operation": {
                    "capability_id": "capability://leanctx/context-optimization",
                    "capability_version": "1.0.0"
                },
                "input_ref": input_ref,
                "input_digest": format!("sha256:{}", "c".repeat(64)),
                "source_refs": [input_ref, source_ref],
                "policy_admission": {"policy_ref": "policy:synthetic", "decision": "admitted"}
            }),
            json!({
                "schema_version": 1,
                "invocation_id": invocation_id,
                "status": "succeeded",
                "output_ref": format!("output:{}", &output_digest[7..]),
                "output_digest": output_digest,
                "source_lineage": [input_ref, source_ref],
                "measurements": [
                    {"name": "input_tokens", "unit": "token", "classification": "measured", "value": 1},
                    {"name": "output_tokens", "unit": "token", "classification": "measured", "value": 2}
                ],
                "failure": null,
                "receipt_link": {
                    "schema_version": 1,
                    "receipt_id": "engine-receipt-synthetic",
                    "receipt_ref": format!("receipt:sha256:{}", "d".repeat(64)),
                    "receipt_digest": format!("sha256:{}", "d".repeat(64)),
                    "invocation_id": invocation_id
                }
            }),
        )
    };
    serde_json::to_string(&json!({
        "schema_version": 1,
        "transport_version": 1,
        "engine_interface_version": "1.0.0",
        "view": view,
        "invocation": invocation,
        "observation": observation,
        "recovery": {
            "recovery_ref": input_ref,
            "source_ref": source_ref,
            "source_digest": source_digest
        }
    }))
    .unwrap()
}

#[cfg(unix)]
#[test]
fn subprocess_engine_round_trip_jails_paths_and_cleans_request_files(
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let root = TempRoot::new()?;
    let view_response = engine_response(false);
    let recovery_response = engine_response(true);
    let script = write_executable(
        &root,
        &format!(
            "case \"$2\" in\n  context-view) printf '%s\\n' '{}' ;;\n  recover) printf '%s\\n' '{}' ;;\n  *) exit 42 ;;\nesac",
            view_response, recovery_response
        ),
    )?;
    let client = SubprocessEngineClient::with_binary(&script)?;
    let source_digest = digest("fresh synthetic source\n");
    let source = ContextSource::with_metadata(
        "fixture/source.txt",
        root.path(),
        "text/plain",
        Some(format!("source:synthetic-path-sha256:{}", "a".repeat(64))),
        Some(source_digest.clone()),
    )?;
    let plan = ContextPlan::new("session", "task", "inspect", source.clone())?;
    let view = client.context_view(&plan)?;
    assert_eq!(view.status(), EngineStatus::Succeeded);
    assert!(view.verify());
    let recovered = client.recover(
        root.path(),
        "fixture/source.txt",
        &format!("input:synthetic-request-sha256:{}", "b".repeat(64)),
        &format!("source:synthetic-path-sha256:{}", "a".repeat(64)),
        &source_digest,
    )?;
    assert_eq!(recovered.text(), "fresh synthetic source\n");
    assert!(client
        .recover(
            root.path(),
            "../escape.txt",
            recovered.recovery_ref(),
            recovered.source_ref(),
            recovered.source_digest(),
        )
        .is_err());
    let request_files = fs::read_dir(root.path())?
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_name()
                .to_string_lossy()
                .starts_with(".leanctx-sdk-")
        })
        .count();
    assert_eq!(request_files, 0);
    Ok(())
}

#[cfg(unix)]
#[test]
fn subprocess_engine_timeout_is_typed_and_reaps_process_group(
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let root = TempRoot::new()?;
    let script = write_executable(&root, "sleep 2")?;
    let client = SubprocessEngineClient::new(Some(script), Duration::from_millis(100))?;
    let source = ContextSource::new("fixture/source.txt", root.path())?;
    let plan = ContextPlan::new("session", "task", "inspect", source)?;
    let error = client.context_view(&plan).unwrap_err();
    assert!(error.downcast_ref::<leanctx_sdk::EngineTimeout>().is_some());
    Ok(())
}

#[cfg(unix)]
fn agent_result() -> Value {
    json!({
        "text": "ok",
        "content_blocks": [{"type": "text", "text": "ok"}],
        "original_tokens": 10,
        "output_tokens": 4,
        "saved_tokens": 6,
        "mode": "full",
        "changed": false,
        "shell": null
    })
}

#[cfg(unix)]
fn agent_script(
    root: &TempRoot,
    wrong_hello_id: bool,
) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
    let hello_id = if wrong_hello_id { "999" } else { "1" };
    let capabilities = vec![
        "ctx_compose",
        "ctx_glob",
        "ctx_read",
        "ctx_search",
        "ctx_symbol",
        "ctx_tree",
    ];
    let hello = serde_json::to_string(&json!({
        "id": hello_id,
        "ok": true,
        "result": {
            "agent_tools_interface_version": "1.0.0",
            "allow_exec": false,
            "allow_write": false,
            "capabilities": capabilities,
            "engine_version": "3.10.1",
            "schema_version": 1,
            "transport_version": 1
        }
    }))?;
    let call = serde_json::to_string(&json!({
        "id": "2",
        "ok": true,
        "result": agent_result()
    }))?;
    let close = serde_json::to_string(&json!({
        "id": "3",
        "ok": true,
        "result": {}
    }))?;
    let body = format!(
        "while IFS= read -r line; do\n  case \"$line\" in\n    *'\"op\":\"hello\"'*) printf '%s\\n' '{}' ;;\n    *'\"op\":\"call\"'*) printf '%s\\n' '{}' ;;\n    *'\"op\":\"close\"'*) printf '%s\\n' '{}' ;;\n  esac\ndone",
        hello, call, close
    );
    write_executable(root, &body)
}

#[cfg(unix)]
#[test]
fn persistent_agent_client_negotiates_policy_and_accumulates_metrics(
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let root = TempRoot::new()?;
    let script = agent_script(&root, false)?;
    let context = AgentContext::open_with_policy(
        root.path(),
        "inspect",
        AgentPermissions::read_only(),
        ExecutionPolicy::default(),
        Some(script),
        Duration::from_secs(2),
    )?;
    assert_eq!(context.capabilities().len(), 6);
    let result = context.read("fixture/source.txt", ReadMode::Full, false)?;
    assert_eq!(result.text(), "ok");
    assert_eq!(result.saved_tokens(), 6);
    assert_eq!(context.metrics().tool_calls(), 1);
    assert!(context.metrics().saved_ratio() > 0.5);
    assert!(context
        .create_file("new.txt", "no")
        .unwrap_err()
        .downcast_ref::<leanctx_sdk::AgentPermissionError>()
        .is_some());
    assert!(context.run(["printf", "no"]).is_err());
    context.close()?;
    assert!(context.close().is_ok());
    Ok(())
}

#[cfg(unix)]
#[test]
fn persistent_agent_client_rejects_terminal_protocol_mismatch(
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let root = TempRoot::new()?;
    let script = agent_script(&root, true)?;
    let error = AgentContext::open_with_policy(
        root.path(),
        "inspect",
        AgentPermissions::read_only(),
        ExecutionPolicy::default(),
        Some(script),
        Duration::from_secs(2),
    )
    .unwrap_err();
    assert!(error
        .downcast_ref::<leanctx_sdk::EngineProtocolError>()
        .is_some());
    Ok(())
}

#[test]
fn execution_policy_is_canonical_and_loader_variables_are_forbidden() {
    let policy = ExecutionPolicy::new(
        Duration::from_secs(3),
        ["git", "git", "cargo"],
        ["ZED", "LANG", "ZED"],
    )
    .unwrap();
    assert_eq!(policy.allowed_executables(), &["cargo", "git"]);
    assert_eq!(policy.allowed_env(), &["LANG", "ZED"]);
    assert!(ExecutionPolicy::new(
        Duration::from_secs(3),
        ["git/x"],
        std::iter::empty::<&str>()
    )
    .is_err());
    assert!(ExecutionPolicy::new(Duration::from_secs(3), ["git"], ["LD_PRELOAD"]).is_err());
}

#[test]
fn public_constants_are_frozen() {
    assert_eq!(leanctx_sdk::__version__, "1.1.0");
    assert_eq!(leanctx_sdk::SCHEMA_VERSION, 1);
    assert_eq!(leanctx_sdk::TRANSPORT_VERSION, 1);
    assert_eq!(leanctx_sdk::ENGINE_INTERFACE_VERSION, "1.0.0");
    assert_eq!(leanctx_sdk::AGENT_TOOLS_INTERFACE_VERSION, "1.0.0");
    assert_eq!(leanctx_sdk::SUPPORTED_AGENT_TOOLS_ENGINE_VERSION, "3.10.1");
}

#[test]
fn optional_real_engine_v1_round_trip() -> Result<(), Box<dyn Error + Send + Sync>> {
    let Some(binary) = std::env::var_os("LEANCTX_ENGINE_BIN") else {
        return Ok(());
    };
    let root = std::env::current_dir()?;
    let source = ContextSource::new("README.md", &root)?;
    let plan = ContextPlan::new(
        "optional-real-engine",
        "optional-real-task",
        "inspect",
        source,
    )?;
    let client = SubprocessEngineClient::with_binary(binary)?;
    let view = client.context_view(&plan)?;
    assert!(matches!(
        view.status(),
        EngineStatus::Succeeded | EngineStatus::Degraded
    ));
    Ok(())
}
