use std::error::Error as StdError;
use std::fmt;
use std::io::{self, Read};
use std::net::IpAddr;
use std::time::{Duration, Instant};

use serde_json::{Map, Value};
use ureq::OrAnyStatus;
use url::Url;

use crate::errors::{
    boxed, ConfigurationError, EngineProtocolError, EngineRejected, EngineTimeout,
    EngineUnavailable, PolicyAdmissionError, SdkResult,
};
use crate::planning::{
    canonical_bytes, canonical_uuid, normalize_source_ids, parse_source_plan, validate_timestamp,
    EnginePlanningRequest, MAX_REQUEST_BYTES,
};
use crate::protocol::{
    sha256_digest, strict_json_loads, validate_digest, ENGINE_INTERFACE_VERSION,
};

const CONTEXT_PLAN_PATH: &str = "/v1/engine/context-plan";
const CONTEXT_MATERIALIZE_PATH: &str = "/v1/engine/context-materialize";
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
/// The client sends source identifiers to the Enterprise Engine and validates
/// its tenant-bound response. Materialized context is prepared data; it is not
/// evidence that context reached a model or that any model execution occurred.
#[derive(Clone)]
pub struct EnterpriseEngineClient {
    base_url: Url,
    credential: String,
    tenant_id: String,
    timeout: Duration,
    agent: ureq::Agent,
}

impl fmt::Debug for EnterpriseEngineClient {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EnterpriseEngineClient")
            .field("base_url", &self.base_url.origin().ascii_serialization())
            .field("credential", &"[redacted]")
            .field("tenant_id", &self.tenant_id)
            .field("timeout", &self.timeout)
            .finish()
    }
}

impl EnterpriseEngineClient {
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
        request: &EnginePlanningRequest,
        source_ids: &[S],
    ) -> SdkResult<EngineSourcePlanResponse> {
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
                "Enterprise Engine request exceeds its byte bound",
            )));
        }
        let raw = self.post_json(
            CONTEXT_PLAN_PATH,
            &payload,
            MAX_RESPONSE_BYTES,
            "Enterprise Engine context-plan",
            "planning request",
        )?;
        self.parse_plan_response(&raw, request, &requested_ids)
    }

    /// Materializes an authenticated source plan without claiming execution.
    pub fn context_materialize<S: AsRef<str>>(
        &self,
        request: &EnginePlanningRequest,
        source_ids: &[S],
        expected_governance_revision: u64,
        expected_binding_digest: &str,
        planning_evaluation_time: Option<&str>,
    ) -> SdkResult<EngineSourceMaterializationResponse> {
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
                "Enterprise Engine materialization request exceeds its byte bound",
            )));
        }
        let raw = self.post_json(
            CONTEXT_MATERIALIZE_PATH,
            &payload,
            MAX_MATERIALIZATION_RESPONSE_BYTES,
            "Enterprise Engine materialization",
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
        let deadline = Instant::now() + self.timeout;
        let endpoint = self.endpoint(path);
        let response = self
            .agent
            .post(&endpoint)
            .set("Accept", "application/json")
            .set("Content-Type", "application/json")
            .set("Authorization", &format!("Bearer {}", self.credential))
            .set("Connection", "close")
            .send_bytes(payload)
            .or_any_status()
            .map_err(|error| map_transport_error(error, operation))?;

        let status = response.status();
        if (300..400).contains(&status) {
            return Err(boxed(EngineProtocolError::new(format!(
                "{operation} redirects are not followed"
            ))));
        }
        if status == 401 {
            return Err(boxed(EngineRejected::new(format!(
                "{operation} authentication was rejected"
            ))));
        }
        if status == 403 {
            return Err(boxed(PolicyAdmissionError::new(format!(
                "{operation} policy rejected the request"
            ))));
        }
        if status >= 500 {
            return Err(boxed(EngineUnavailable::new(format!(
                "{operation} returned a server error"
            ))));
        }
        if !(200..300).contains(&status) {
            return Err(boxed(EngineRejected::new(format!(
                "{operation} rejected the {rejection}"
            ))));
        }

        let declared_length = response.header("Content-Length");
        let declared_length = declared_length
            .map(|value| {
                value.parse::<u64>().map_err(|_| {
                    boxed(EngineProtocolError::new(format!(
                        "{operation} response length is invalid"
                    )))
                })
            })
            .transpose()?;
        if declared_length.is_some_and(|length| length > max_response_bytes as u64) {
            return Err(boxed(EngineProtocolError::new(format!(
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
                return Err(boxed(EngineTimeout::new(format!(
                    "{operation} request exceeded its deadline"
                ))));
            }
            let count = reader
                .read(&mut chunk)
                .map_err(|error| map_read_error(error, operation))?;
            if Instant::now() >= deadline {
                return Err(boxed(EngineTimeout::new(format!(
                    "{operation} request exceeded its deadline"
                ))));
            }
            if count == 0 {
                break;
            }
            body.extend_from_slice(&chunk[..count]);
            if body.len() > max_response_bytes {
                return Err(boxed(EngineProtocolError::new(format!(
                    "{operation} response exceeds its byte bound"
                ))));
            }
        }
        Ok(body)
    }

    fn parse_plan_response(
        &self,
        raw: &[u8],
        request: &EnginePlanningRequest,
        requested_ids: &std::collections::BTreeSet<String>,
    ) -> SdkResult<EngineSourcePlanResponse> {
        let response = parse_object(raw, MAX_RESPONSE_BYTES, "Enterprise Engine response")?;
        exact_response_keys(
            &response,
            &["schema_version", "tenant_id", "governance_revision", "plan"],
            "Enterprise Engine response",
        )?;
        if read_u64(&response, "schema_version", "response.schema_version")? != 1 {
            return Err(protocol_error(
                "Enterprise Engine response schema_version is unsupported",
            ));
        }
        let tenant_id = read_uuid(&response, "tenant_id", "response.tenant_id")?;
        if tenant_id != self.tenant_id {
            return Err(protocol_error(
                "Enterprise Engine response tenant binding does not match",
            ));
        }
        let governance_revision =
            read_u64(&response, "governance_revision", "governance_revision")?;
        let plan_value = response
            .get("plan")
            .ok_or_else(|| boxed(EngineProtocolError::new("response.plan is missing")))?;
        let plan = parse_source_plan(plan_value, request, requested_ids)
            .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))?;
        Ok(EngineSourcePlanResponse {
            schema_version: 1,
            tenant_id,
            governance_revision,
            plan,
        })
    }

    fn parse_materialization_response(
        &self,
        raw: &[u8],
        request: &EnginePlanningRequest,
        requested_ids: &std::collections::BTreeSet<String>,
        expected_governance_revision: u64,
        expected_binding_digest: &str,
    ) -> SdkResult<EngineSourceMaterializationResponse> {
        let response = parse_object(
            raw,
            MAX_MATERIALIZATION_RESPONSE_BYTES,
            "Enterprise Engine materialization response",
        )?;
        exact_response_keys(
            &response,
            &[
                "schema_version",
                "tenant_id",
                "governance_revision",
                "materialization",
            ],
            "Enterprise Engine materialization response",
        )?;
        if read_u64(&response, "schema_version", "response.schema_version")? != 1 {
            return Err(protocol_error(
                "Enterprise Engine materialization response schema_version is unsupported",
            ));
        }
        let tenant_id = read_uuid(&response, "tenant_id", "response.tenant_id")?;
        if tenant_id != self.tenant_id {
            return Err(protocol_error(
                "Enterprise Engine materialization response tenant binding does not match",
            ));
        }
        let governance_revision =
            read_u64(&response, "governance_revision", "governance_revision")?;
        if governance_revision != expected_governance_revision {
            return Err(protocol_error(
                "Enterprise Engine materialization governance revision does not match",
            ));
        }
        let materialization_raw = response.get("materialization").ok_or_else(|| {
            boxed(EngineProtocolError::new(
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
            "Enterprise Engine materialization",
        )?;
        validate_header(materialization, "Enterprise Engine materialization")?;

        let plan_raw = materialization
            .get("plan")
            .ok_or_else(|| boxed(EngineProtocolError::new("materialization.plan is missing")))?;
        let plan = parse_source_plan(plan_raw, request, requested_ids)
            .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))?;
        let binding_digest = plan
            .get("binding_digest")
            .and_then(Value::as_str)
            .ok_or_else(|| protocol_error("materialization plan binding_digest is invalid"))?;
        if binding_digest != expected_binding_digest {
            return Err(protocol_error(
                "Enterprise Engine materialization binding digest does not match",
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
                "Enterprise Engine materialized token metric exceeds the plan budget",
            ));
        }
        let content = materialization
            .get("content")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                protocol_error("Enterprise Engine materialized content is not a string")
            })?;
        if content.len() > MAX_MATERIALIZED_CONTENT_BYTES {
            return Err(protocol_error(
                "Enterprise Engine materialized content exceeds its byte bound",
            ));
        }
        if sha256_digest(content.as_bytes()) != materialized_digest {
            return Err(protocol_error(
                "Enterprise Engine materialized content digest does not match",
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
        Ok(EngineSourceMaterializationResponse {
            schema_version: 1,
            tenant_id,
            governance_revision,
            materialization: Value::Object(normalized_materialization),
        })
    }
}

/// A validated tenant-bound source plan; it is planning context, not execution proof.
#[derive(Clone, Debug, PartialEq)]
pub struct EngineSourcePlanResponse {
    schema_version: u64,
    tenant_id: String,
    governance_revision: u64,
    plan: Value,
}

impl EngineSourcePlanResponse {
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
pub struct EngineSourceMaterializationResponse {
    schema_version: u64,
    tenant_id: String,
    governance_revision: u64,
    materialization: Value,
}

impl EngineSourceMaterializationResponse {
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
        .map_err(|error| boxed(EngineProtocolError::new(error.to_string())))?;
    response_object(&value, field).cloned()
}

fn response_object<'a>(value: &'a Value, field: &str) -> SdkResult<&'a Map<String, Value>> {
    value.as_object().ok_or_else(|| {
        boxed(EngineProtocolError::new(format!(
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
        .map_err(|_| boxed(EngineProtocolError::new(format!("{field} is invalid"))))
}

fn read_digest(value: &Map<String, Value>, key: &str, field: &str) -> SdkResult<String> {
    let raw = value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| protocol_error(format!("{field} must be a SHA-256 digest")))?;
    validate_digest(raw, field)
        .map_err(|_| boxed(EngineProtocolError::new(format!("{field} is invalid"))))
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
        boxed(EngineTimeout::new(format!(
            "{operation} request exceeded its deadline"
        )))
    } else {
        boxed(EngineUnavailable::new(format!(
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
        boxed(EngineTimeout::new(format!(
            "{operation} response exceeded its deadline"
        )))
    } else {
        boxed(EngineUnavailable::new(format!(
            "{operation} response could not be read"
        )))
    }
}

fn protocol_error(message: impl Into<String>) -> Box<dyn StdError + Send + Sync> {
    boxed(EngineProtocolError::new(message))
}

#[cfg(test)]
#[path = "enterprise_tests.rs"]
mod tests;
