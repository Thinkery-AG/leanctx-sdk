use std::error::Error as StdError;
use std::fmt;
use std::io::{self, Read};
use std::net::IpAddr;
use std::time::{Duration, Instant};

use serde_json::{Map, Value};
use ureq::OrAnyStatus;
use url::Url;

use crate::errors::{
    boxed, ConfigurationError, InstanceProtocolError, InstanceRejected, InstanceTimeout,
    InstanceUnavailable, PolicyAdmissionError, SdkResult,
};
use crate::planning::{
    canonical_bytes, canonical_uuid, normalize_source_ids, parse_source_plan, validate_timestamp,
    InstancePlanningRequest, MAX_REQUEST_BYTES,
};
use crate::protocol::{
    sha256_digest, strict_json_loads, validate_digest, ENGINE_INTERFACE_VERSION,
};

const CONTEXT_PLAN_PATH: &str = "/v1/engine/context-plan";
const CONTEXT_MATERIALIZE_PATH: &str = "/v1/engine/context-materialize";
const CONTEXT_MATERIALIZE_V2_PATH: &str = "/v1/engine/context-materialize-v2";
const PRESENTER_ENVELOPE_BYTES: usize = 512;
const PRESENTED_USE_PATH: &str = "/v1/engine/presented-use";
const PRESENTER_REVOKE_PATH: &str = "/v1/engine/presenter-revoke";
const PRESENTER_UNSUPPORTED: &str =
    "Enterprise suite does not serve presenter capabilities: LeanCTX 3.11.0 or later is required";
const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
const MAX_MATERIALIZED_CONTENT_BYTES: usize = 1024 * 1024;
const MAX_MATERIALIZATION_RESPONSE_BYTES: usize =
    MAX_RESPONSE_BYTES + MAX_MATERIALIZED_CONTENT_BYTES;
const MAX_CREDENTIAL_BYTES: usize = 4096;
const MAX_URL_BYTES: usize = 4096;
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_TIMEOUT: Duration = Duration::from_secs(120);

/// Authenticated source planning and context materialization over HTTPS.
///
/// The client sends source identifiers to the Enterprise instance and validates
/// its tenant-bound response. Materialized context is prepared data; it is not
/// evidence that context reached a model or that any model execution occurred.
#[derive(Clone)]
pub struct EnterpriseInstanceClient {
    base_url: Url,
    credential: String,
    tenant_id: String,
    timeout: Duration,
    agent: ureq::Agent,
}

impl fmt::Debug for EnterpriseInstanceClient {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EnterpriseInstanceClient")
            .field("base_url", &self.base_url.origin().ascii_serialization())
            .field("credential", &"[redacted]")
            .field("tenant_id", &self.tenant_id)
            .field("timeout", &self.timeout)
            .finish()
    }
}

impl EnterpriseInstanceClient {
    /// Creates a production HTTPS client with a 30-second request deadline.
    pub fn new(
        base_url: impl AsRef<str>,
        credential: impl AsRef<str>,
        tenant_id: impl AsRef<str>,
    ) -> SdkResult<Self> {
        Self::with_options(base_url, credential, tenant_id, DEFAULT_TIMEOUT, false)
    }

    /// Creates a client with an explicit deadline and optional loopback HTTP.
    ///
    /// HTTP is accepted only when `allow_loopback_http` is true and the URL
    /// contains a literal loopback IP address. HTTPS keeps ureq's built-in
    /// rustls certificate-chain and hostname verification enabled.
    pub fn with_options(
        base_url: impl AsRef<str>,
        credential: impl AsRef<str>,
        tenant_id: impl AsRef<str>,
        timeout: Duration,
        allow_loopback_http: bool,
    ) -> SdkResult<Self> {
        let base_url_text = base_url.as_ref();
        let base_url = validate_base_url(base_url_text, allow_loopback_http)?;
        let credential = validate_credential(credential.as_ref())?;
        let tenant_id = canonical_uuid(tenant_id.as_ref(), "tenant_id")
            .map_err(|_| boxed(ConfigurationError::new("tenant_id must be a non-nil UUID")))?;
        validate_timeout(timeout)?;

        let agent = ureq::AgentBuilder::new()
            .redirects(0)
            .try_proxy_from_env(false)
            .timeout(timeout)
            .timeout_connect(timeout)
            .timeout_read(timeout)
            .timeout_write(timeout)
            .build();
        Ok(Self {
            base_url,
            credential,
            tenant_id,
            timeout,
            agent,
        })
    }

    pub fn tenant_id(&self) -> &str {
        &self.tenant_id
    }

    pub fn timeout(&self) -> Duration {
        self.timeout
    }

    /// Requests a tenant-bound plan for source IDs without sending source bodies.
    pub fn context_plan<S: AsRef<str>>(
        &self,
        request: &InstancePlanningRequest,
        source_ids: &[S],
    ) -> SdkResult<InstanceSourcePlanResponse> {
        let (source_ids, requested_ids) = normalize_source_ids(source_ids).map_err(boxed)?;
        let mut payload = Map::new();
        payload.insert("planning".to_owned(), request.to_value());
        payload.insert(
            "source_ids".to_owned(),
            Value::Array(source_ids.iter().cloned().map(Value::String).collect()),
        );
        let payload = canonical_bytes(&Value::Object(payload)).map_err(boxed)?;
        if payload.len() > MAX_REQUEST_BYTES {
            return Err(boxed(crate::errors::ValidationError::new(
                "Enterprise instance request exceeds its byte bound",
            )));
        }
        let raw = self.post_json(
            CONTEXT_PLAN_PATH,
            &payload,
            MAX_RESPONSE_BYTES,
            "Enterprise instance context-plan",
            "planning request",
        )?;
        self.parse_plan_response(&raw, request, &requested_ids)
    }

    /// Materializes an authenticated source plan without claiming execution.
    pub fn context_materialize<S: AsRef<str>>(
        &self,
        request: &InstancePlanningRequest,
        source_ids: &[S],
        expected_governance_revision: u64,
        expected_binding_digest: &str,
        planning_evaluation_time: Option<&str>,
    ) -> SdkResult<InstanceSourceMaterializationResponse> {
        let (source_ids, requested_ids) = normalize_source_ids(source_ids).map_err(boxed)?;
        let binding_digest =
            validate_digest(expected_binding_digest, "expected_binding_digest").map_err(boxed)?;
        let planning_evaluation_time = planning_evaluation_time
            .map(|value| validate_timestamp(value, "planning_evaluation_time"))
            .transpose()
            .map_err(boxed)?;

        let mut payload = Map::new();
        payload.insert("planning".to_owned(), request.to_value());
        payload.insert(
            "source_ids".to_owned(),
            Value::Array(source_ids.iter().cloned().map(Value::String).collect()),
        );
        payload.insert(
            "expected_governance_revision".to_owned(),
            Value::from(expected_governance_revision),
        );
        payload.insert(
            "expected_binding_digest".to_owned(),
            Value::String(binding_digest.clone()),
        );
        if let Some(evaluation_time) = planning_evaluation_time {
            payload.insert(
                "planning_evaluation_time".to_owned(),
                Value::String(evaluation_time),
            );
        }
        let payload = canonical_bytes(&Value::Object(payload)).map_err(boxed)?;
        if payload.len() > MAX_REQUEST_BYTES {
            return Err(boxed(crate::errors::ValidationError::new(
                "Enterprise instance materialization request exceeds its byte bound",
            )));
        }
        let raw = self.post_json(
            CONTEXT_MATERIALIZE_PATH,
            &payload,
            MAX_MATERIALIZATION_RESPONSE_BYTES,
            "Enterprise instance materialization",
            "materialization request",
        )?;
        self.parse_materialization_response(
            &raw,
            request,
            &requested_ids,
            expected_governance_revision,
            &binding_digest,
        )
    }

    /// Materializes under a presenter capability.
    ///
    /// `PresenterUse::Issue` asks for a new capability, returned once in
    /// [`InstancePresenterGrant::capability`]; `PresenterUse::Renew` presents
    /// under a capability this host holds, which travels in the
    /// `x-leanctx-presenter` request header and is never part of the body.
    ///
    /// The capability proves the holder of the capability and nothing else:
    /// the suite checks the API key, its tenant and the current source rights
    /// first and at every use, and the capability is usable only with the API
    /// key it was issued to. An `UnsupportedInstanceError` means the suite or
    /// its instance does not serve the v2 contract.
    pub fn context_materialize_v2<S: AsRef<str>>(
        &self,
        request: &InstancePlanningRequest,
        source_ids: &[S],
        expected_governance_revision: u64,
        expected_binding_digest: &str,
        planning_evaluation_time: Option<&str>,
        presenter: PresenterUse<'_>,
    ) -> SdkResult<InstanceSourceMaterializationResponseV2> {
        let (source_ids, requested_ids) = normalize_source_ids(source_ids).map_err(boxed)?;
        let binding_digest =
            validate_digest(expected_binding_digest, "expected_binding_digest").map_err(boxed)?;
        let planning_evaluation_time = planning_evaluation_time
            .map(|value| validate_timestamp(value, "planning_evaluation_time"))
            .transpose()
            .map_err(boxed)?;
        let mut mode = Map::new();
        let held = match presenter {
            PresenterUse::Issue { ttl_seconds }
                if (1..=MAX_INSTANCE_PRESENTER_TTL_SECONDS).contains(&ttl_seconds) =>
            {
                mode.insert("mode".to_owned(), Value::from("issue"));
                mode.insert("ttl_seconds".to_owned(), Value::from(ttl_seconds));
                None
            }
            PresenterUse::Renew(capability) => {
                mode.insert("mode".to_owned(), Value::from("renew"));
                Some(capability)
            }
            PresenterUse::Issue { .. } => {
                return Err(boxed(crate::errors::ValidationError::new(
                    "presenter lifetime must be 1 to 86400 seconds",
                )));
            }
        };

        let mut payload = Map::new();
        payload.insert("planning".to_owned(), request.to_value());
        payload.insert(
            "source_ids".to_owned(),
            Value::Array(source_ids.iter().cloned().map(Value::String).collect()),
        );
        payload.insert(
            "expected_governance_revision".to_owned(),
            Value::from(expected_governance_revision),
        );
        payload.insert(
            "expected_binding_digest".to_owned(),
            Value::String(binding_digest.clone()),
        );
        payload.insert("presenter".to_owned(), Value::Object(mode));
        if let Some(evaluation_time) = planning_evaluation_time {
            payload.insert(
                "planning_evaluation_time".to_owned(),
                Value::String(evaluation_time),
            );
        }
        let payload = canonical_bytes(&Value::Object(payload)).map_err(boxed)?;
        if payload.len() > MAX_REQUEST_BYTES {
            return Err(boxed(crate::errors::ValidationError::new(
                "Enterprise instance materialization request exceeds its byte bound",
            )));
        }
        let raw = self.post_json_presented(
            CONTEXT_MATERIALIZE_V2_PATH,
            &payload,
            MAX_MATERIALIZATION_RESPONSE_BYTES + PRESENTER_ENVELOPE_BYTES,
            "Enterprise instance materialization",
            "materialization request",
            held,
            Some(&format!(
                "Enterprise suite does not serve context-materialize-v2: LeanCTX {INSTANCE_PRESENTER_CAPABILITY_MIN_INSTANCE_VERSION} or later is required"
            )),
        )?;

        const MISMATCH: &str = "Enterprise instance materialization response";
        let response = parse_object(
            &raw,
            MAX_MATERIALIZATION_RESPONSE_BYTES + PRESENTER_ENVELOPE_BYTES,
            MISMATCH,
        )?;
        exact_response_keys(
            &response,
            &[
                "schema_version",
                "tenant_id",
                "governance_revision",
                "materialization",
            ],
            MISMATCH,
        )?;
        let engine = response_object(&response["materialization"], MISMATCH)?;
        exact_response_keys(
            engine,
            &["schema_version", "materialization", "presenter"],
            MISMATCH,
        )?;
        if read_u64(&response, "schema_version", "response.schema_version")? != 2
            || read_u64(engine, "schema_version", "materialization.schema_version")? != 2
        {
            return Err(protocol_error(
                "Enterprise instance materialization response schema_version is unsupported",
            ));
        }
        // The inner exchange is the unchanged v1 one: validate it as such.
        let mut v1 = Map::new();
        v1.insert("schema_version".to_owned(), Value::from(1));
        for key in ["tenant_id", "governance_revision"] {
            v1.insert(key.to_owned(), response[key].clone());
        }
        v1.insert(
            "materialization".to_owned(),
            engine["materialization"].clone(),
        );
        let v1_raw = serde_json::to_vec(&Value::Object(v1))
            .map_err(|_| protocol_error("Enterprise instance materialization is not valid JSON"))?;
        let materialization = self.parse_materialization_response(
            &v1_raw,
            request,
            &requested_ids,
            expected_governance_revision,
            &binding_digest,
        )?;
        let presenter = parse_presenter_grant(
            response_object(&engine["presenter"], "materialization.presenter")?,
            materialization.content().unwrap_or_default(),
            held.is_none(),
        )?;
        Ok(InstanceSourceMaterializationResponseV2 {
            materialization,
            presenter,
        })
    }

    /// Use text that was materialized under `capability`: ask whether it may
    /// still be used, keep a recovery copy of it, or get a kept copy back.
    ///
    /// `source_ids` are the governed sources the text was materialized from —
    /// exactly those, or the use is refused. The capability travels in the
    /// `x-leanctx-presenter` request header, never in the body. The suite
    /// checks the API key, its tenant and its current rights to every source
    /// first and again before it answers; the instance checks the capability
    /// and the current policy. A refused capability is a
    /// `PolicyAdmissionError`; an `UnsupportedInstanceError` means the suite or
    /// its instance does not serve the operation.
    pub fn presented_use<S: AsRef<str>>(
        &self,
        capability: &PresenterCapability,
        source_ids: &[S],
        expected_governance_revision: u64,
        asked: PresentedUse<'_>,
    ) -> SdkResult<PresentedUseResponse> {
        let (source_ids, _) = normalize_source_ids(source_ids).map_err(boxed)?;
        let (mode, member, value) = match asked {
            PresentedUse::Check(content) => ("check", "content", content),
            PresentedUse::Retain(content) => ("retain", "content", content),
            PresentedUse::Expand(recovery_id) => ("expand", "recovery_id", recovery_id),
        };
        let well_formed = if mode == "expand" {
            lower_hex_with_prefix(value, "", 64)
        } else {
            !value.is_empty() && value.len() <= MAX_MATERIALIZED_CONTENT_BYTES
        };
        if source_ids.is_empty() || !well_formed {
            return Err(boxed(crate::errors::ValidationError::new(
                "a presented use requires source ids and a bounded text or a recovery id",
            )));
        }
        let mut use_member = Map::new();
        use_member.insert("mode".to_owned(), Value::from(mode));
        use_member.insert(member.to_owned(), Value::from(value));
        let mut payload = Map::new();
        payload.insert("schema_version".to_owned(), Value::from(1));
        payload.insert(
            "source_ids".to_owned(),
            Value::Array(source_ids.iter().cloned().map(Value::String).collect()),
        );
        payload.insert(
            "expected_governance_revision".to_owned(),
            Value::from(expected_governance_revision),
        );
        payload.insert("use".to_owned(), Value::Object(use_member));
        let payload = canonical_bytes(&Value::Object(payload)).map_err(boxed)?;
        let limit = MAX_MATERIALIZATION_RESPONSE_BYTES + PRESENTER_ENVELOPE_BYTES;
        let raw = self.post_json_presented(
            PRESENTED_USE_PATH,
            &payload,
            limit,
            "Enterprise presented use",
            "presented use request",
            Some(capability),
            Some(PRESENTER_UNSUPPORTED),
        )?;

        const MISMATCH: &str = "Enterprise presented use response";
        let response = parse_object(&raw, limit, MISMATCH)?;
        exact_response_keys(
            &response,
            &["schema_version", "tenant_id", "governance_revision", "use"],
            MISMATCH,
        )?;
        let answer = response_object(&response["use"], MISMATCH)?;
        let optional = ["reason", "recovery_id", "content"];
        let text = |key: &str| answer.get(key).and_then(Value::as_str);
        let listed: Option<Vec<&str>> = answer
            .get("source_ids")
            .and_then(Value::as_array)
            .and_then(|ids| ids.iter().map(Value::as_str).collect());
        let (Some(presenter_ref), Some(usable), Some(listed)) = (
            text("presenter_ref").filter(|value| lower_hex_with_prefix(value, "presenter:", 32)),
            answer.get("usable").and_then(Value::as_bool),
            listed,
        ) else {
            return Err(protocol_error(format!(
                "{MISMATCH} fields do not match the v1 contract"
            )));
        };
        let known = |key: &String| {
            ["schema_version", "presenter_ref", "usable", "source_ids"].contains(&key.as_str())
                || (optional.contains(&key.as_str()) && answer[key.as_str()].is_string())
        };
        let (reason, recovery_id, content) = (text("reason"), text("recovery_id"), text("content"));
        let expanding = mode == "expand";
        let consistent = if usable {
            reason.is_none()
                && recovery_id.is_some() == (mode != "check")
                && recovery_id.map_or(true, |id| lower_hex_with_prefix(id, "", 64))
                && (!expanding || recovery_id == Some(value))
                && content.is_some() == expanding
                && content.map_or(true, |content| {
                    content.len() <= MAX_MATERIALIZED_CONTENT_BYTES
                        && !content.contains(capability.reveal())
                })
                && listed.len() == source_ids.len()
                && source_ids.iter().all(|id| listed.contains(&id.as_str()))
        } else {
            reason.is_some_and(is_presented_use_reason)
                && recovery_id.is_none()
                && content.is_none()
                && listed.is_empty()
        };
        if !answer.keys().all(known)
            || read_u64(&response, "schema_version", "response.schema_version")? != 1
            || read_u64(answer, "schema_version", "use.schema_version")? != 1
            || read_uuid(&response, "tenant_id", "response.tenant_id")? != self.tenant_id
            || read_u64(
                &response,
                "governance_revision",
                "response.governance_revision",
            )? != expected_governance_revision
            || !consistent
        {
            return Err(protocol_error(format!(
                "{MISMATCH} does not match the request"
            )));
        }
        Ok(PresentedUseResponse {
            presenter_ref: presenter_ref.to_owned(),
            usable,
            reason: reason.map(str::to_owned),
            source_ids: listed.into_iter().map(str::to_owned).collect(),
            recovery_id: recovery_id.map(str::to_owned),
            content: content.map(str::to_owned),
        })
    }

    /// End `capability`: from now on it presents and reuses nothing. Only the
    /// API key it was issued to can revoke it. Returns the presenter reference
    /// and whether the instance had the presenter on record.
    pub fn presenter_revoke(&self, capability: &PresenterCapability) -> SdkResult<(String, bool)> {
        let raw = self.post_json_presented(
            PRESENTER_REVOKE_PATH,
            br#"{"schema_version":1}"#,
            PRESENTER_ENVELOPE_BYTES,
            "Enterprise presenter revocation",
            "presenter revocation request",
            Some(capability),
            Some(PRESENTER_UNSUPPORTED),
        )?;
        const MISMATCH: &str = "Enterprise presenter revocation response";
        let response = parse_object(&raw, PRESENTER_ENVELOPE_BYTES, MISMATCH)?;
        exact_response_keys(
            &response,
            &["schema_version", "tenant_id", "revocation"],
            MISMATCH,
        )?;
        let revocation = response_object(&response["revocation"], MISMATCH)?;
        exact_response_keys(
            revocation,
            &["schema_version", "presenter_ref", "revoked"],
            MISMATCH,
        )?;
        let presenter_ref = revocation["presenter_ref"]
            .as_str()
            .filter(|value| lower_hex_with_prefix(value, "presenter:", 32));
        let (Some(presenter_ref), Some(revoked)) = (presenter_ref, revocation["revoked"].as_bool())
        else {
            return Err(protocol_error(format!(
                "{MISMATCH} fields do not match the v1 contract"
            )));
        };
        if read_u64(&response, "schema_version", "response.schema_version")? != 1
            || read_u64(revocation, "schema_version", "revocation.schema_version")? != 1
            || read_uuid(&response, "tenant_id", "response.tenant_id")? != self.tenant_id
        {
            return Err(protocol_error(format!(
                "{MISMATCH} does not match the request"
            )));
        }
        Ok((presenter_ref.to_owned(), revoked))
    }

    fn endpoint(&self, path: &str) -> String {
        let mut endpoint = self.base_url.clone();
        endpoint.set_path(path);
        endpoint.to_string()
    }

    fn post_json(
        &self,
        path: &str,
        payload: &[u8],
        max_response_bytes: usize,
        operation: &str,
        rejection: &str,
    ) -> SdkResult<Vec<u8>> {
        self.post_json_presented(
            path,
            payload,
            max_response_bytes,
            operation,
            rejection,
            None,
            None,
        )
    }

    /// `post_json` for an operation that may show a presenter capability (a
    /// request header, never a body member) and that an older suite or instance
    /// may not serve: with `unsupported`, 404, 405 and 501 are an
    /// `UnsupportedInstanceError`.
    #[allow(clippy::too_many_arguments)]
    fn post_json_presented(
        &self,
        path: &str,
        payload: &[u8],
        max_response_bytes: usize,
        operation: &str,
        rejection: &str,
        presenter: Option<&PresenterCapability>,
        unsupported: Option<&str>,
    ) -> SdkResult<Vec<u8>> {
        let deadline = Instant::now() + self.timeout;
        let endpoint = self.endpoint(path);
        let mut call = self
            .agent
            .post(&endpoint)
            .set("Accept", "application/json")
            .set("Content-Type", "application/json")
            .set("Authorization", &format!("Bearer {}", self.credential))
            .set("Connection", "close");
        if let Some(presenter) = presenter {
            call = call.set(PRESENTER_CAPABILITY_HEADER, presenter.reveal());
        }
        let response = call
            .send_bytes(payload)
            .or_any_status()
            .map_err(|error| map_transport_error(error, operation))?;

        let status = response.status();
        if (300..400).contains(&status) {
            return Err(boxed(InstanceProtocolError::new(format!(
                "{operation} redirects are not followed"
            ))));
        }
        if let Some(message) = unsupported {
            if matches!(status, 404 | 405 | 501) {
                return Err(boxed(crate::errors::UnsupportedInstanceError::new(message)));
            }
        }
        if status == 401 {
            return Err(boxed(InstanceRejected::new(format!(
                "{operation} authentication was rejected"
            ))));
        }
        if status == 403 {
            return Err(boxed(PolicyAdmissionError::new(format!(
                "{operation} policy rejected the request"
            ))));
        }
        if status >= 500 {
            return Err(boxed(InstanceUnavailable::new(format!(
                "{operation} returned a server error"
            ))));
        }
        if !(200..300).contains(&status) {
            return Err(boxed(InstanceRejected::new(format!(
                "{operation} rejected the {rejection}"
            ))));
        }

        let declared_length = response.header("Content-Length");
        let declared_length = declared_length
            .map(|value| {
                value.parse::<u64>().map_err(|_| {
                    boxed(InstanceProtocolError::new(format!(
                        "{operation} response length is invalid"
                    )))
                })
            })
            .transpose()?;
        if declared_length.is_some_and(|length| length > max_response_bytes as u64) {
            return Err(boxed(InstanceProtocolError::new(format!(
                "{operation} response exceeds its byte bound"
            ))));
        }

        let capacity = declared_length
            .map(|length| length as usize)
            .unwrap_or_default()
            .min(max_response_bytes);
        let mut body = Vec::with_capacity(capacity);
        let mut reader = response.into_reader().take(max_response_bytes as u64 + 1);
        let mut chunk = [0_u8; 8192];
        loop {
            if Instant::now() >= deadline {
                return Err(boxed(InstanceTimeout::new(format!(
                    "{operation} request exceeded its deadline"
                ))));
            }
            let count = reader
                .read(&mut chunk)
                .map_err(|error| map_read_error(error, operation))?;
            if Instant::now() >= deadline {
                return Err(boxed(InstanceTimeout::new(format!(
                    "{operation} request exceeded its deadline"
                ))));
            }
            if count == 0 {
                break;
            }
            body.extend_from_slice(&chunk[..count]);
            if body.len() > max_response_bytes {
                return Err(boxed(InstanceProtocolError::new(format!(
                    "{operation} response exceeds its byte bound"
                ))));
            }
        }
        Ok(body)
    }

    fn parse_plan_response(
        &self,
        raw: &[u8],
        request: &InstancePlanningRequest,
        requested_ids: &std::collections::BTreeSet<String>,
    ) -> SdkResult<InstanceSourcePlanResponse> {
        let response = parse_object(raw, MAX_RESPONSE_BYTES, "Enterprise instance response")?;
        exact_response_keys(
            &response,
            &["schema_version", "tenant_id", "governance_revision", "plan"],
            "Enterprise instance response",
        )?;
        if read_u64(&response, "schema_version", "response.schema_version")? != 1 {
            return Err(protocol_error(
                "Enterprise instance response schema_version is unsupported",
            ));
        }
        let tenant_id = read_uuid(&response, "tenant_id", "response.tenant_id")?;
        if tenant_id != self.tenant_id {
            return Err(protocol_error(
                "Enterprise instance response tenant binding does not match",
            ));
        }
        let governance_revision =
            read_u64(&response, "governance_revision", "governance_revision")?;
        let plan_value = response
            .get("plan")
            .ok_or_else(|| boxed(InstanceProtocolError::new("response.plan is missing")))?;
        let plan = parse_source_plan(plan_value, request, requested_ids)
            .map_err(|error| boxed(InstanceProtocolError::new(error.to_string())))?;
        Ok(InstanceSourcePlanResponse {
            schema_version: 1,
            tenant_id,
            governance_revision,
            plan,
        })
    }

    fn parse_materialization_response(
        &self,
        raw: &[u8],
        request: &InstancePlanningRequest,
        requested_ids: &std::collections::BTreeSet<String>,
        expected_governance_revision: u64,
        expected_binding_digest: &str,
    ) -> SdkResult<InstanceSourceMaterializationResponse> {
        let response = parse_object(
            raw,
            MAX_MATERIALIZATION_RESPONSE_BYTES,
            "Enterprise instance materialization response",
        )?;
        exact_response_keys(
            &response,
            &[
                "schema_version",
                "tenant_id",
                "governance_revision",
                "materialization",
            ],
            "Enterprise instance materialization response",
        )?;
        if read_u64(&response, "schema_version", "response.schema_version")? != 1 {
            return Err(protocol_error(
                "Enterprise instance materialization response schema_version is unsupported",
            ));
        }
        let tenant_id = read_uuid(&response, "tenant_id", "response.tenant_id")?;
        if tenant_id != self.tenant_id {
            return Err(protocol_error(
                "Enterprise instance materialization response tenant binding does not match",
            ));
        }
        let governance_revision =
            read_u64(&response, "governance_revision", "governance_revision")?;
        if governance_revision != expected_governance_revision {
            return Err(protocol_error(
                "Enterprise instance materialization governance revision does not match",
            ));
        }
        let materialization_raw = response.get("materialization").ok_or_else(|| {
            boxed(InstanceProtocolError::new(
                "response.materialization is missing",
            ))
        })?;
        let materialization = response_object(materialization_raw, "response.materialization")?;
        exact_response_keys(
            materialization,
            &[
                "schema_version",
                "transport_version",
                "engine_interface_version",
                "plan",
                "materialized_digest",
                "materialized_token_count",
                "content",
            ],
            "Enterprise instance materialization",
        )?;
        validate_header(materialization, "Enterprise instance materialization")?;

        let plan_raw = materialization.get("plan").ok_or_else(|| {
            boxed(InstanceProtocolError::new(
                "materialization.plan is missing",
            ))
        })?;
        let plan = parse_source_plan(plan_raw, request, requested_ids)
            .map_err(|error| boxed(InstanceProtocolError::new(error.to_string())))?;
        let binding_digest = plan
            .get("binding_digest")
            .and_then(Value::as_str)
            .ok_or_else(|| protocol_error("materialization plan binding_digest is invalid"))?;
        if binding_digest != expected_binding_digest {
            return Err(protocol_error(
                "Enterprise instance materialization binding digest does not match",
            ));
        }
        let materialized_digest = read_digest(
            materialization,
            "materialized_digest",
            "materialized_digest",
        )?;
        let materialized_token_count = read_u64(
            materialization,
            "materialized_token_count",
            "materialized_token_count",
        )?;
        let budget_tokens = plan
            .get("result")
            .and_then(|result| result.get("plan"))
            .and_then(|plan| plan.get("budget_tokens"))
            .and_then(Value::as_u64)
            .ok_or_else(|| protocol_error("materialization plan budget_tokens is invalid"))?;
        if materialized_token_count > budget_tokens {
            return Err(protocol_error(
                "Enterprise instance materialized token metric exceeds the plan budget",
            ));
        }
        let content = materialization
            .get("content")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                protocol_error("Enterprise instance materialized content is not a string")
            })?;
        if content.len() > MAX_MATERIALIZED_CONTENT_BYTES {
            return Err(protocol_error(
                "Enterprise instance materialized content exceeds its byte bound",
            ));
        }
        if sha256_digest(content.as_bytes()) != materialized_digest {
            return Err(protocol_error(
                "Enterprise instance materialized content digest does not match",
            ));
        }

        let mut normalized_materialization = Map::new();
        normalized_materialization.insert("schema_version".to_owned(), Value::from(1));
        normalized_materialization.insert("transport_version".to_owned(), Value::from(1));
        normalized_materialization.insert(
            "engine_interface_version".to_owned(),
            Value::String(ENGINE_INTERFACE_VERSION.to_owned()),
        );
        normalized_materialization.insert("plan".to_owned(), plan);
        normalized_materialization.insert(
            "materialized_digest".to_owned(),
            Value::String(materialized_digest),
        );
        normalized_materialization.insert(
            "materialized_token_count".to_owned(),
            Value::from(materialized_token_count),
        );
        normalized_materialization.insert("content".to_owned(), Value::String(content.to_owned()));
        Ok(InstanceSourceMaterializationResponse {
            schema_version: 1,
            tenant_id,
            governance_revision,
            materialization: Value::Object(normalized_materialization),
        })
    }
}

/// A validated tenant-bound source plan; it is planning context, not execution proof.
#[derive(Clone, Debug, PartialEq)]
pub struct InstanceSourcePlanResponse {
    schema_version: u64,
    tenant_id: String,
    governance_revision: u64,
    plan: Value,
}

impl InstanceSourcePlanResponse {
    pub fn schema_version(&self) -> u64 {
        self.schema_version
    }

    pub fn tenant_id(&self) -> &str {
        &self.tenant_id
    }

    pub fn governance_revision(&self) -> u64 {
        self.governance_revision
    }

    /// Returns the validated source-plan envelope and digest-bound bindings.
    pub fn plan(&self) -> &Value {
        &self.plan
    }
}

/// A validated tenant-bound materialization; it does not prove model delivery.
#[derive(Clone, Debug, PartialEq)]
pub struct InstanceSourceMaterializationResponse {
    schema_version: u64,
    tenant_id: String,
    governance_revision: u64,
    materialization: Value,
}

impl InstanceSourceMaterializationResponse {
    pub fn schema_version(&self) -> u64 {
        self.schema_version
    }

    pub fn tenant_id(&self) -> &str {
        &self.tenant_id
    }

    pub fn governance_revision(&self) -> u64 {
        self.governance_revision
    }

    pub fn materialization(&self) -> &Value {
        &self.materialization
    }

    pub fn content(&self) -> Option<&str> {
        self.materialization.get("content").and_then(Value::as_str)
    }

    pub fn materialized_digest(&self) -> Option<&str> {
        self.materialization
            .get("materialized_digest")
            .and_then(Value::as_str)
    }

    pub fn materialized_token_count(&self) -> Option<u64> {
        self.materialization
            .get("materialized_token_count")
            .and_then(Value::as_u64)
    }
}

/// First instance release that serves the v2 materialization contract.
pub const INSTANCE_PRESENTER_CAPABILITY_MIN_INSTANCE_VERSION: &str = "3.11.0";
/// Longest presenter lifetime a host may ask for: one day.
pub const MAX_INSTANCE_PRESENTER_TTL_SECONDS: u32 = 24 * 60 * 60;
/// Request header that carries a capability to the proxy or the Enterprise
/// suite. A request header only, never a body member.
pub const PRESENTER_CAPABILITY_HEADER: &str = "x-leanctx-presenter";

/// A bearer secret the instance returns once to the host that presented sources.
///
/// Whoever later shows it is treated as THE HOLDER OF THAT CAPABILITY for
/// those presentations. It is not an agent name, not an attested identity and
/// not an authorization: current source rights are checked again at every use.
///
/// Host-internal: never put it in model context, a tool argument, a tool
/// result, a log or a receipt, and never send it to a provider. The type has
/// no `Display`, no `Serialize` and a redacting `Debug`; [`Self::reveal`] is
/// the one deliberate way to the secret.
#[derive(Clone)]
pub struct PresenterCapability(String);

impl PresenterCapability {
    /// Wraps a capability a host kept in its own secret store.
    pub fn new(secret: impl AsRef<str>) -> SdkResult<Self> {
        let secret = secret.as_ref();
        if !lower_hex_with_prefix(secret, "lcp1_", 64) {
            return Err(boxed(crate::errors::ValidationError::new(
                "presenter capability is malformed",
            )));
        }
        Ok(Self(secret.to_owned()))
    }

    /// Returns the secret. Never log, print or forward the result.
    pub fn reveal(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for PresenterCapability {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PresenterCapability(<redacted>)")
    }
}

/// How this call's sources are presented.
#[derive(Clone, Copy, Debug)]
pub enum PresenterUse<'a> {
    /// Ask for a new capability with this lifetime (1 to 86400 seconds).
    Issue { ttl_seconds: u32 },
    /// Present under a capability this host already holds.
    Renew(&'a PresenterCapability),
}

/// The presenter entry of a v2 materialization.
#[derive(Clone, Debug)]
pub struct InstancePresenterGrant {
    presenter_ref: String,
    expires_at_unix: u64,
    capability: Option<PresenterCapability>,
}

impl InstancePresenterGrant {
    /// Content-free reference of the presenter entry. Safe in records.
    pub fn presenter_ref(&self) -> &str {
        &self.presenter_ref
    }

    pub fn expires_at_unix(&self) -> u64 {
        self.expires_at_unix
    }

    /// The capability, when this call issued one.
    pub fn capability(&self) -> Option<&PresenterCapability> {
        self.capability.as_ref()
    }
}

/// A validated tenant-bound v2 materialization: the unchanged v1 exchange and
/// the presenter grant. It does not prove model delivery.
#[derive(Clone, Debug)]
pub struct InstanceSourceMaterializationResponseV2 {
    materialization: InstanceSourceMaterializationResponse,
    presenter: InstancePresenterGrant,
}

impl InstanceSourceMaterializationResponseV2 {
    pub fn schema_version(&self) -> u64 {
        2
    }

    pub fn materialization(&self) -> &InstanceSourceMaterializationResponse {
        &self.materialization
    }

    pub fn presenter(&self) -> &InstancePresenterGrant {
        &self.presenter
    }
}

/// What the holder of a capability asks about text that was delivered under
/// a presented lineage.
#[derive(Clone, Copy, Debug)]
pub enum PresentedUse<'a> {
    /// May this exact delivered text still be used by this holder now?
    Check(&'a str),
    /// `Check`, and keep a recovery copy; the answer carries its id.
    Retain(&'a str),
    /// Return the text of the recovery copy with this id.
    Expand(&'a str),
}

/// A validated, tenant-bound answer to a [`PresentedUse`]. When `usable` is
/// false, `reason` is a content-free code and every other member is empty.
#[derive(Clone, Debug)]
pub struct PresentedUseResponse {
    presenter_ref: String,
    usable: bool,
    reason: Option<String>,
    source_ids: Vec<String>,
    recovery_id: Option<String>,
    content: Option<String>,
}

impl PresentedUseResponse {
    /// Content-free reference of the presenter entry. Safe in records.
    pub fn presenter_ref(&self) -> &str {
        &self.presenter_ref
    }

    pub fn usable(&self) -> bool {
        self.usable
    }

    pub fn reason(&self) -> Option<&str> {
        self.reason.as_deref()
    }

    pub fn source_ids(&self) -> &[String] {
        &self.source_ids
    }

    pub fn recovery_id(&self) -> Option<&str> {
        self.recovery_id.as_deref()
    }

    pub fn content(&self) -> Option<&str> {
        self.content.as_deref()
    }
}

/// The instance's fixed, content-free refusal codes: `lineage.*`, `recovery.*`.
fn is_presented_use_reason(reason: &str) -> bool {
    ["lineage.", "recovery."].iter().any(|prefix| {
        reason.strip_prefix(prefix).is_some_and(|code| {
            !code.is_empty()
                && code.len() <= 48
                && code
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
        })
    })
}

fn lower_hex_with_prefix(text: &str, prefix: &str, digits: usize) -> bool {
    text.strip_prefix(prefix).is_some_and(|hex| {
        hex.len() == digits
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

/// `issued` says whether this call asked for a new capability: then exactly
/// one is present, otherwise none.
fn parse_presenter_grant(
    value: &Map<String, Value>,
    content: &str,
    issued: bool,
) -> SdkResult<InstancePresenterGrant> {
    if value
        .keys()
        .any(|key| !["presenter_ref", "expires_at_unix", "capability"].contains(&key.as_str()))
    {
        return Err(protocol_error(
            "presenter grant fields do not match the v2 contract",
        ));
    }
    let presenter_ref = value
        .get("presenter_ref")
        .and_then(Value::as_str)
        .filter(|reference| lower_hex_with_prefix(reference, "presenter:", 32))
        .ok_or_else(|| protocol_error("presenter_ref is malformed"))?;
    let expires_at_unix = read_u64(value, "expires_at_unix", "presenter.expires_at_unix")?;
    if expires_at_unix == 0 || value.contains_key("capability") != issued {
        return Err(protocol_error("presenter grant does not match the request"));
    }
    let capability = match value.get("capability") {
        None => None,
        Some(secret) => {
            let secret = secret
                .as_str()
                .filter(|secret| lower_hex_with_prefix(secret, "lcp1_", 64))
                .ok_or_else(|| protocol_error("presenter capability is malformed"))?;
            if content.contains(secret) {
                return Err(protocol_error(
                    "presenter capability appears in materialized content",
                ));
            }
            Some(PresenterCapability(secret.to_owned()))
        }
    };
    Ok(InstancePresenterGrant {
        presenter_ref: presenter_ref.to_owned(),
        expires_at_unix,
        capability,
    })
}

fn validate_base_url(value: &str, allow_loopback_http: bool) -> SdkResult<Url> {
    if value.is_empty() || value.len() > MAX_URL_BYTES {
        return Err(boxed(ConfigurationError::new(
            "base_url must be a bounded absolute URL",
        )));
    }
    if value
        .chars()
        .any(|character| character.is_control() || character.is_whitespace())
    {
        return Err(boxed(ConfigurationError::new(
            "base_url must not contain whitespace or controls",
        )));
    }
    let Some((_, remainder)) = value.split_once("://") else {
        return Err(boxed(ConfigurationError::new(
            "base_url must be a bounded absolute URL",
        )));
    };
    let authority = remainder.split(['/', '?', '#']).next().unwrap_or_default();
    if authority.contains('@') {
        return Err(boxed(ConfigurationError::new(
            "base_url must not contain userinfo",
        )));
    }
    let parsed = Url::parse(value)
        .map_err(|_| boxed(ConfigurationError::new("base_url is not a valid URL")))?;
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(boxed(ConfigurationError::new(
            "base_url must not contain userinfo",
        )));
    }
    if parsed.host().is_none() {
        return Err(boxed(ConfigurationError::new(
            "base_url must contain a host",
        )));
    }
    if parsed.query().is_some()
        || parsed.fragment().is_some()
        || parsed.path() != "/"
        || value.contains('?')
        || value.contains('#')
    {
        return Err(boxed(ConfigurationError::new(
            "base_url may contain only an optional root path",
        )));
    }
    let scheme = parsed.scheme();
    if scheme != "https" && scheme != "http" {
        return Err(boxed(ConfigurationError::new("base_url must use HTTPS")));
    }
    if scheme == "http" {
        if !allow_loopback_http {
            return Err(boxed(ConfigurationError::new(
                "HTTP is only allowed for explicit loopback testing",
            )));
        }
        let host = raw_host(value).unwrap_or_default();
        let is_literal_loopback = host
            .parse::<IpAddr>()
            .is_ok_and(|address| address.is_loopback());
        if !is_literal_loopback {
            return Err(boxed(ConfigurationError::new(
                "loopback HTTP requires a literal loopback IP",
            )));
        }
    }
    let port = parsed
        .port()
        .unwrap_or(if scheme == "https" { 443 } else { 80 });
    if !(1..=65535).contains(&port) {
        return Err(boxed(ConfigurationError::new(
            "base_url port is outside its bounds",
        )));
    }
    Ok(parsed)
}

fn raw_host(value: &str) -> Option<&str> {
    let remainder = value.split_once("://")?.1;
    let authority = remainder.split(['/', '?', '#']).next()?;
    if let Some(ipv6) = authority.strip_prefix('[') {
        return Some(ipv6.split_once(']')?.0);
    }
    Some(authority.split(':').next().unwrap_or_default())
}

fn validate_credential(value: &str) -> SdkResult<String> {
    if value.is_empty()
        || value.len() > MAX_CREDENTIAL_BYTES
        || !value.bytes().all(|byte| (0x21..=0x7e).contains(&byte))
    {
        return Err(boxed(ConfigurationError::new(
            "credential must be a non-empty visible ASCII string",
        )));
    }
    Ok(value.to_owned())
}

fn validate_timeout(timeout: Duration) -> SdkResult<()> {
    if timeout < Duration::from_millis(100) || timeout > MAX_TIMEOUT {
        return Err(boxed(ConfigurationError::new(
            "timeout must be between 0.1 and 120 seconds",
        )));
    }
    Ok(())
}

fn parse_object(raw: &[u8], limit: usize, field: &str) -> SdkResult<Map<String, Value>> {
    if raw.len() > limit {
        return Err(protocol_error(format!("{field} exceeds its byte bound")));
    }
    let value = strict_json_loads(raw, field)
        .map_err(|error| boxed(InstanceProtocolError::new(error.to_string())))?;
    response_object(&value, field).cloned()
}

fn response_object<'a>(value: &'a Value, field: &str) -> SdkResult<&'a Map<String, Value>> {
    value.as_object().ok_or_else(|| {
        boxed(InstanceProtocolError::new(format!(
            "{field} is not an object"
        )))
    })
}

fn exact_response_keys(
    value: &Map<String, Value>,
    expected: &[&str],
    field: &str,
) -> SdkResult<()> {
    if value.len() != expected.len() || value.keys().any(|key| !expected.contains(&key.as_str())) {
        return Err(protocol_error(format!(
            "{field} fields do not match the v1 contract"
        )));
    }
    Ok(())
}

fn read_u64(value: &Map<String, Value>, key: &str, field: &str) -> SdkResult<u64> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| protocol_error(format!("{field} must be an unsigned 64-bit integer")))
}

fn read_uuid(value: &Map<String, Value>, key: &str, field: &str) -> SdkResult<String> {
    let raw = value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| protocol_error(format!("{field} must be a canonical UUID")))?;
    canonical_uuid(raw, field)
        .map_err(|_| boxed(InstanceProtocolError::new(format!("{field} is invalid"))))
}

fn read_digest(value: &Map<String, Value>, key: &str, field: &str) -> SdkResult<String> {
    let raw = value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| protocol_error(format!("{field} must be a SHA-256 digest")))?;
    validate_digest(raw, field)
        .map_err(|_| boxed(InstanceProtocolError::new(format!("{field} is invalid"))))
}

fn validate_header(value: &Map<String, Value>, field: &str) -> SdkResult<()> {
    if read_u64(value, "schema_version", &format!("{field}.schema_version"))? != 1
        || read_u64(
            value,
            "transport_version",
            &format!("{field}.transport_version"),
        )? != 1
        || value
            .get("engine_interface_version")
            .and_then(Value::as_str)
            != Some(ENGINE_INTERFACE_VERSION)
    {
        return Err(protocol_error(format!(
            "{field} has an unsupported version"
        )));
    }
    Ok(())
}

fn map_transport_error(
    transport: ureq::Transport,
    operation: &str,
) -> Box<dyn StdError + Send + Sync> {
    if is_timeout(&transport) {
        boxed(InstanceTimeout::new(format!(
            "{operation} request exceeded its deadline"
        )))
    } else {
        boxed(InstanceUnavailable::new(format!(
            "{operation} connection failed"
        )))
    }
}

fn is_timeout(transport: &ureq::Transport) -> bool {
    let mut source = transport.source();
    while let Some(cause) = source {
        if let Some(io_error) = cause.downcast_ref::<io::Error>() {
            if matches!(
                io_error.kind(),
                io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
            ) {
                return true;
            }
        }
        source = cause.source();
    }
    false
}

fn map_read_error(error: io::Error, operation: &str) -> Box<dyn StdError + Send + Sync> {
    if matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    ) {
        boxed(InstanceTimeout::new(format!(
            "{operation} response exceeded its deadline"
        )))
    } else {
        boxed(InstanceUnavailable::new(format!(
            "{operation} response could not be read"
        )))
    }
}

fn protocol_error(message: impl Into<String>) -> Box<dyn StdError + Send + Sync> {
    boxed(InstanceProtocolError::new(message))
}

#[cfg(test)]
#[path = "enterprise_tests.rs"]
mod tests;
