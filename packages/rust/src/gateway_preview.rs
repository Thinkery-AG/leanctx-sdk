// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
//! Preview: the information gateway's egress admission and decision receipts.
//!
//! [`SubprocessEngineClient::admit_egress`] runs one model request through the
//! local Engine's egress admission (`lean-ctx engine egress-admit`) before the
//! caller sends it: secrets are masked, restricted content is withheld, and
//! the request is classified. Parsing mirrors the Engine's validation; unknown
//! fields and values are rejected. Contract `leanctx-gateway-preview` 0.1 —
//! may change in minor releases.

use std::collections::BTreeMap;
use std::error::Error as StdError;
use std::fs;
use std::path::Path;

use serde_json::{Map, Value};

use crate::engine::{create_request_file, SubprocessEngineClient};
use crate::errors::{boxed, EngineProtocolError, ValidationError};
use crate::protocol::strict_json_loads;

pub const GATEWAY_PREVIEW_CONTRACT: &str = "leanctx-gateway-preview";
pub const GATEWAY_PREVIEW_VERSION: &str = "0.1.0";
pub const EGRESS_SCHEMA_VERSION: u64 = 1;
pub const MAX_EGRESS_REQUEST_BYTES: usize = 8 * 1024 * 1024;

const MAX_REF_BYTES: usize = 512;
const MAX_DECISIONS: usize = 4096;
const MAX_REASON_CODES: usize = 32;
const MAX_SIGNALS: usize = 32;
const U32: u64 = u32::MAX as u64;
const MAX_SAFE: u64 = (1 << 53) - 1;

const CLASSIFICATIONS: &[&str] = &["public", "internal", "confidential", "restricted"];
const DISPOSITIONS: &[&str] = &["forward", "rewritten", "refused"];
const MODES: &[&str] = &["developer", "governed", "sovereign"];
const PRINCIPAL_KINDS: &[&str] = &[
    "person",
    "team",
    "organization",
    "project",
    "agent",
    "session",
    "workload",
    "unknown",
];
const LOCALITIES: &[&str] = &["local", "remote", "unknown"];
const OUTCOMES: &[&str] = &["delivered", "withheld", "failed"];
const CONTEXT_DISPOSITIONS: &[&str] = &[
    "allow",
    "allow_minimized",
    "allow_redacted",
    "allow_summary_only",
    "allow_local_model_only",
    "allow_with_approval",
    "quarantine",
    "deny",
];
const TRANSFORMATIONS: &[&str] = &[
    "redaction",
    "classification",
    "selection",
    "deduplication",
    "structural_extraction",
    "compression",
    "summarization",
    "recovery",
    "reranking",
];
const CATEGORIES: &[&str] = &[
    "secret",
    "pii",
    "prompt_injection",
    "classification",
    "policy",
    "custom",
];
const SEVERITIES: &[&str] = &["info", "low", "medium", "high", "critical"];
const COVERAGE_KINDS: &[&str] = &[
    "complete",
    "partial",
    "unsupported",
    "failed",
    "not_required",
];
const DETECTOR_STATUSES: &[&str] = &["completed", "failed", "timed_out", "skipped"];

type Result<T> = std::result::Result<T, Box<dyn StdError + Send + Sync>>;

/// Who requested the context; `unknown` is explicit and never authorizes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextPrincipal {
    pub kind: String,
    pub id: Option<String>,
}

impl ContextPrincipal {
    pub fn is_known(&self) -> bool {
        self.kind != "unknown"
    }
}

/// Where the context goes; organisation management is only ever attested.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextDestination {
    pub provider: String,
    pub locality: String,
    pub model: Option<String>,
    pub organization_managed: bool,
    pub account_ref: Option<String>,
    pub region: Option<String>,
}

/// What a detector actually inspected; never more than recorded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DetectorCoverage {
    pub kind: String,
    pub bytes_total: u64,
    pub bytes_inspected: u64,
    pub chunks_total: u64,
    pub chunks_inspected: u64,
    pub reason: Option<String>,
}

/// One detector's result: counts only, never the matched value.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SecuritySignal {
    pub detector_id: String,
    pub detector_version: String,
    pub category: String,
    pub severity: String,
    pub evidence_count: u64,
    pub coverage: DetectorCoverage,
    pub status: String,
    pub latency_us: u64,
    pub calibrated: bool,
    pub confidence_milli: Option<u64>,
}

/// The gateway's decision about one object (by digest, never content).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextDecision {
    pub object: String,
    pub disposition: String,
    pub reason_codes: Vec<String>,
    pub signals: Vec<SecuritySignal>,
    pub required_transformations: Vec<String>,
}

impl ContextDecision {
    /// Whether (possibly transformed) content reaches the destination now.
    pub fn delivers_content(&self) -> bool {
        let rank = |value: &str| CONTEXT_DISPOSITIONS.iter().position(|item| *item == value);
        rank(&self.disposition) <= rank("allow_local_model_only")
    }
}

/// One governed delivery: who, where, under which policy, what was inspected,
/// withheld or transformed, and the delivered context's digest.
#[derive(Clone, Debug, PartialEq)]
pub struct ContextDecisionReceipt {
    pub receipt_id: String,
    pub mode: String,
    pub principal: ContextPrincipal,
    pub destination: ContextDestination,
    pub sources: BTreeMap<String, u64>,
    pub security: BTreeMap<String, u64>,
    pub tokens: BTreeMap<String, u64>,
    pub outcome: String,
    pub duration_us: u64,
    pub decisions: Vec<ContextDecision>,
    pub policy: Option<BTreeMap<String, String>>,
    pub task: Option<String>,
    pub final_context: Option<String>,
    pub quality: Option<Map<String, Value>>,
}

impl ContextDecisionReceipt {
    pub fn signals(&self) -> impl Iterator<Item = &SecuritySignal> {
        self.decisions
            .iter()
            .flat_map(|decision| decision.signals.iter())
    }
}

/// What may leave for the model, how sensitive it is, and why.
#[derive(Clone, Debug, PartialEq)]
pub struct EgressAdmission {
    pub disposition: String,
    pub body: Option<Map<String, Value>>,
    pub refusal: Option<String>,
    pub classification: Option<String>,
    pub receipt: Option<ContextDecisionReceipt>,
}

impl EgressAdmission {
    /// True when `body` may be sent; a refused request must not be.
    pub fn may_send(&self) -> bool {
        self.disposition != "refused"
    }
}

/// One model request about to leave for a provider.
#[derive(Clone, Debug)]
pub struct EgressRequest {
    pub provider: String,
    pub upstream_base: String,
    pub body: Map<String, Value>,
}

fn bad(message: impl AsRef<str>) -> Box<dyn StdError + Send + Sync> {
    boxed(EngineProtocolError::new(format!(
        "egress admission: {}",
        message.as_ref()
    )))
}

fn object<'a>(value: &'a Value, label: &str) -> Result<&'a Map<String, Value>> {
    value
        .as_object()
        .ok_or_else(|| bad(format!("{label} must be an object")))
}

fn keys(
    value: &Map<String, Value>,
    required: &[&str],
    optional: &[&str],
    label: &str,
) -> Result<()> {
    if let Some(missing) = required.iter().find(|key| !value.contains_key(**key)) {
        return Err(bad(format!("{label} lacks {missing}")));
    }
    if let Some(unknown) = value
        .keys()
        .find(|key| !required.contains(&key.as_str()) && !optional.contains(&key.as_str()))
    {
        return Err(bad(format!("{label} has unknown field {unknown}")));
    }
    Ok(())
}

fn int(value: &Value, label: &str, maximum: u64) -> Result<u64> {
    match value.as_u64() {
        Some(number) if value.is_u64() && number <= maximum => Ok(number),
        _ => Err(bad(format!("{label} must be an integer in 0..{maximum}"))),
    }
}

fn boolean(value: Option<&Value>, label: &str) -> Result<bool> {
    match value {
        None => Ok(false),
        Some(Value::Bool(flag)) => Ok(*flag),
        Some(_) => Err(bad(format!("{label} must be a boolean"))),
    }
}

fn reference(value: &Value, label: &str) -> Result<String> {
    match value.as_str() {
        Some(text)
            if !text.trim().is_empty()
                && text.len() <= MAX_REF_BYTES
                && !text.chars().any(|ch| ch < '\u{20}' || ch == '\u{7f}') =>
        {
            Ok(text.to_owned())
        }
        _ => Err(bad(format!("{label} must be a bounded reference"))),
    }
}

fn optional_reference(
    value: &Map<String, Value>,
    key: &str,
    label: &str,
) -> Result<Option<String>> {
    value.get(key).map(|raw| reference(raw, label)).transpose()
}

fn one_of(value: &Value, allowed: &[&str], label: &str) -> Result<String> {
    match value.as_str() {
        Some(text) if allowed.contains(&text) => Ok(text.to_owned()),
        _ => Err(bad(format!("{label} has unknown value {value}"))),
    }
}

fn digest(value: &Value, label: &str) -> Result<String> {
    let valid = value.as_str().is_some_and(|text| {
        text.len() == 71
            && text.starts_with("sha256:")
            && text[7..]
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    });
    if valid {
        Ok(value.as_str().unwrap_or_default().to_owned())
    } else {
        Err(bad(format!("{label} must be a sha256 digest")))
    }
}

fn is_reason_code(code: &str) -> bool {
    let bytes = code.as_bytes();
    (3..=64).contains(&bytes.len())
        && bytes[0].is_ascii_lowercase()
        && bytes[1..].iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'_' | b'.')
        })
}

fn reasons(value: Option<&Value>, label: &str) -> Result<Vec<String>> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    let list = value
        .as_array()
        .filter(|list| list.len() <= MAX_REASON_CODES)
        .ok_or_else(|| bad(format!("{label} must be a bounded list")))?;
    list.iter()
        .map(|code| match code.as_str() {
            Some(text) if is_reason_code(text) => Ok(text.to_owned()),
            _ => Err(bad(format!("{label} holds an invalid reason code"))),
        })
        .collect()
}

fn principal(raw: &Value) -> Result<ContextPrincipal> {
    let value = object(raw, "principal")?;
    keys(value, &["kind"], &["id"], "principal")?;
    let kind = one_of(&value["kind"], PRINCIPAL_KINDS, "principal.kind")?;
    match (kind.as_str(), value.get("id")) {
        ("unknown", Some(_)) => Err(bad("an unknown principal must not carry an identity")),
        ("unknown", None) => Ok(ContextPrincipal { kind, id: None }),
        (_, None) => Err(bad("a known principal requires an id")),
        (_, Some(id)) => Ok(ContextPrincipal {
            id: Some(reference(id, "principal.id")?),
            kind,
        }),
    }
}

fn destination(raw: &Value) -> Result<ContextDestination> {
    let value = object(raw, "destination")?;
    keys(
        value,
        &["provider", "locality"],
        &["model", "organization_managed", "account_ref", "region"],
        "destination",
    )?;
    Ok(ContextDestination {
        provider: reference(&value["provider"], "destination.provider")?,
        locality: one_of(&value["locality"], LOCALITIES, "destination.locality")?,
        model: optional_reference(value, "model", "destination.model")?,
        organization_managed: boolean(
            value.get("organization_managed"),
            "destination.organization_managed",
        )?,
        account_ref: optional_reference(value, "account_ref", "destination.account_ref")?,
        region: optional_reference(value, "region", "destination.region")?,
    })
}

fn coverage(raw: &Value) -> Result<DetectorCoverage> {
    let value = object(raw, "coverage")?;
    keys(
        value,
        &[
            "kind",
            "bytes_total",
            "bytes_inspected",
            "chunks_total",
            "chunks_inspected",
        ],
        &["reason"],
        "coverage",
    )?;
    let parsed = DetectorCoverage {
        kind: one_of(&value["kind"], COVERAGE_KINDS, "coverage.kind")?,
        bytes_total: int(&value["bytes_total"], "coverage.bytes_total", MAX_SAFE)?,
        bytes_inspected: int(
            &value["bytes_inspected"],
            "coverage.bytes_inspected",
            MAX_SAFE,
        )?,
        chunks_total: int(&value["chunks_total"], "coverage.chunks_total", U32)?,
        chunks_inspected: int(&value["chunks_inspected"], "coverage.chunks_inspected", U32)?,
        reason: match value.get("reason") {
            Some(reason) => {
                reasons(Some(&Value::Array(vec![reason.clone()])), "coverage.reason")?.pop()
            }
            None => None,
        },
    };
    if parsed.bytes_inspected > parsed.bytes_total || parsed.chunks_inspected > parsed.chunks_total
    {
        return Err(bad("coverage must not inspect more than the object holds"));
    }
    let all_bytes = parsed.bytes_inspected == parsed.bytes_total;
    if parsed.kind == "complete" && !all_bytes {
        return Err(bad("complete coverage must inspect every byte"));
    }
    if parsed.kind == "partial" && all_bytes {
        return Err(bad("partial coverage must leave bytes uninspected"));
    }
    Ok(parsed)
}

fn signal(raw: &Value) -> Result<SecuritySignal> {
    let value = object(raw, "signal")?;
    keys(
        value,
        &[
            "detector",
            "category",
            "severity",
            "evidence_count",
            "coverage",
            "status",
            "latency_us",
        ],
        &["calibrated", "confidence_milli"],
        "signal",
    )?;
    let detector = object(&value["detector"], "signal.detector")?;
    keys(detector, &["id", "version"], &[], "signal.detector")?;
    let parsed = SecuritySignal {
        detector_id: reference(&detector["id"], "signal.detector.id")?,
        detector_version: reference(&detector["version"], "signal.detector.version")?,
        category: one_of(&value["category"], CATEGORIES, "signal.category")?,
        severity: one_of(&value["severity"], SEVERITIES, "signal.severity")?,
        evidence_count: int(&value["evidence_count"], "signal.evidence_count", U32)?,
        coverage: coverage(&value["coverage"])?,
        status: one_of(&value["status"], DETECTOR_STATUSES, "signal.status")?,
        latency_us: int(&value["latency_us"], "signal.latency_us", MAX_SAFE)?,
        calibrated: boolean(value.get("calibrated"), "signal.calibrated")?,
        confidence_milli: value
            .get("confidence_milli")
            .map(|raw| int(raw, "signal.confidence_milli", 1000))
            .transpose()?,
    };
    if matches!(parsed.status.as_str(), "failed" | "timed_out")
        && parsed.coverage.kind == "complete"
    {
        return Err(bad(
            "a failed or timed-out detector cannot claim complete coverage",
        ));
    }
    Ok(parsed)
}

fn decision(raw: &Value) -> Result<ContextDecision> {
    let value = object(raw, "decision")?;
    keys(
        value,
        &["object", "disposition"],
        &["reason_codes", "signals", "required_transformations"],
        "decision",
    )?;
    let empty = Vec::new();
    let signals = match value.get("signals") {
        None => &empty,
        Some(raw) => raw
            .as_array()
            .filter(|list| list.len() <= MAX_SIGNALS)
            .ok_or_else(|| bad("decision.signals must be a bounded list"))?,
    };
    let transformations = match value.get("required_transformations") {
        None => &empty,
        Some(raw) => raw
            .as_array()
            .ok_or_else(|| bad("decision.required_transformations must be a list"))?,
    };
    let parsed = ContextDecision {
        object: digest(&value["object"], "decision.object")?,
        disposition: one_of(
            &value["disposition"],
            CONTEXT_DISPOSITIONS,
            "decision.disposition",
        )?,
        reason_codes: reasons(value.get("reason_codes"), "decision.reason_codes")?,
        signals: signals.iter().map(signal).collect::<Result<_>>()?,
        required_transformations: transformations
            .iter()
            .map(|kind| one_of(kind, TRANSFORMATIONS, "decision.required_transformations"))
            .collect::<Result<_>>()?,
    };
    if parsed.disposition != "allow" && parsed.reason_codes.is_empty() {
        return Err(bad(
            "every non-allow decision requires at least one reason code",
        ));
    }
    Ok(parsed)
}

fn counts(raw: &Value, names: &[&str], label: &str, maximum: u64) -> Result<BTreeMap<String, u64>> {
    let value = object(raw, label)?;
    keys(value, names, &[], label)?;
    names
        .iter()
        .map(|name| {
            Ok((
                (*name).to_owned(),
                int(&value[*name], &format!("{label}.{name}"), maximum)?,
            ))
        })
        .collect()
}

/// Parse and validate a `ContextDecisionReceiptV1` document.
pub fn parse_decision_receipt(raw: &Value) -> Result<ContextDecisionReceipt> {
    let value = object(raw, "receipt")?;
    keys(
        value,
        &[
            "schema_version",
            "receipt_id",
            "mode",
            "principal",
            "destination",
            "sources",
            "security",
            "tokens",
            "outcome",
            "duration_us",
        ],
        &["task", "policy", "decisions", "final_context", "quality"],
        "receipt",
    )?;
    if !value["schema_version"].is_u64() || value["schema_version"].as_u64() != Some(1) {
        return Err(bad("unsupported receipt schema_version"));
    }
    let decisions: Vec<ContextDecision> = match value.get("decisions") {
        None => Vec::new(),
        Some(raw) => raw
            .as_array()
            .filter(|list| list.len() <= MAX_DECISIONS)
            .ok_or_else(|| bad("receipt.decisions must be a bounded list"))?
            .iter()
            .map(decision)
            .collect::<Result<_>>()?,
    };
    let sources = counts(
        &value["sources"],
        &["inspected", "permitted", "selected", "blocked"],
        "receipt.sources",
        U32,
    )?;
    if sources["selected"] > sources["permitted"]
        || sources["permitted"] + sources["blocked"] > sources["inspected"]
    {
        return Err(bad(
            "source counts must satisfy selected <= permitted and permitted + blocked <= inspected",
        ));
    }
    let security = counts(
        &value["security"],
        &[
            "redactions",
            "blocked_objects",
            "quarantined_objects",
            "injection_signals",
            "incomplete_coverage",
        ],
        "receipt.security",
        U32,
    )?;
    let count = |disposition: &str| {
        decisions
            .iter()
            .filter(|decision| decision.disposition == disposition)
            .count() as u64
    };
    if security["blocked_objects"] != count("deny")
        || security["quarantined_objects"] != count("quarantine")
    {
        return Err(bad(
            "security counts must equal the recorded deny/quarantine decisions",
        ));
    }
    let outcome = one_of(&value["outcome"], OUTCOMES, "receipt.outcome")?;
    let final_context = value
        .get("final_context")
        .map(|raw| digest(raw, "receipt.final_context"))
        .transpose()?;
    if (outcome == "delivered") != final_context.is_some() {
        return Err(bad(
            "exactly a delivered receipt names the delivered context digest",
        ));
    }
    let policy = match value.get("policy") {
        None => None,
        Some(raw) => {
            let raw = object(raw, "receipt.policy")?;
            keys(raw, &["id", "digest"], &["version"], "receipt.policy")?;
            let mut policy = BTreeMap::new();
            policy.insert("id".to_owned(), reference(&raw["id"], "receipt.policy.id")?);
            policy.insert(
                "digest".to_owned(),
                digest(&raw["digest"], "receipt.policy.digest")?,
            );
            if let Some(version) = raw.get("version") {
                policy.insert(
                    "version".to_owned(),
                    reference(version, "receipt.policy.version")?,
                );
            }
            Some(policy)
        }
    };
    Ok(ContextDecisionReceipt {
        receipt_id: reference(&value["receipt_id"], "receipt.receipt_id")?,
        mode: one_of(&value["mode"], MODES, "receipt.mode")?,
        principal: principal(&value["principal"])?,
        destination: destination(&value["destination"])?,
        sources,
        security,
        tokens: counts(
            &value["tokens"],
            &["original", "delivered"],
            "receipt.tokens",
            MAX_SAFE,
        )?,
        outcome,
        duration_us: int(&value["duration_us"], "receipt.duration_us", MAX_SAFE)?,
        decisions,
        policy,
        task: optional_reference(value, "task", "receipt.task")?,
        final_context,
        quality: value
            .get("quality")
            .map(|raw| object(raw, "receipt.quality").cloned())
            .transpose()?,
    })
}

/// Parse and validate an `EngineEgressAdmissionResponseV1` document.
pub fn parse_egress_admission(raw: &Value) -> Result<EgressAdmission> {
    let value = object(raw, "response")?;
    keys(
        value,
        &["schema_version", "disposition"],
        &["body", "refusal", "classification", "receipt"],
        "response",
    )?;
    if !value["schema_version"].is_u64()
        || value["schema_version"].as_u64() != Some(EGRESS_SCHEMA_VERSION)
    {
        return Err(bad("unsupported egress schema_version"));
    }
    let disposition = one_of(&value["disposition"], DISPOSITIONS, "disposition")?;
    let (body, refusal) = match (
        disposition.as_str(),
        value.get("body"),
        value.get("refusal"),
    ) {
        ("refused", None, Some(Value::String(reason))) if !reason.trim().is_empty() => {
            (None, Some(reason.clone()))
        }
        ("refused", _, _) => return Err(bad("a refused request carries a refusal and no body")),
        (_, Some(Value::Object(body)), None) => (Some(body.clone()), None),
        _ => {
            return Err(bad(
                "an admitted request carries a body object and no refusal",
            ))
        }
    };
    Ok(EgressAdmission {
        disposition,
        body,
        refusal,
        classification: value
            .get("classification")
            .map(|raw| one_of(raw, CLASSIFICATIONS, "classification"))
            .transpose()?,
        receipt: value
            .get("receipt")
            .map(parse_decision_receipt)
            .transpose()?,
    })
}

impl SubprocessEngineClient {
    /// Preview: admit one model request through the local Engine before it is
    /// sent. Send `admission.body`, never the original body, and only when
    /// `admission.may_send()`.
    pub fn admit_egress(
        &self,
        project_root: &Path,
        request: &EgressRequest,
    ) -> Result<EgressAdmission> {
        if request.provider.trim().is_empty() {
            return Err(boxed(ValidationError::new(
                "provider must be a non-empty string",
            )));
        }
        if !request.upstream_base.starts_with("https://")
            && !request.upstream_base.starts_with("http://")
        {
            return Err(boxed(ValidationError::new(
                "upstream_base must be an http(s) URL",
            )));
        }
        let root = self.validate_root(project_root)?;
        // Model requests carry fractional numbers (temperature, top_p): plain JSON.
        let payload = serde_json::to_vec(&serde_json::json!({
            "schema_version": EGRESS_SCHEMA_VERSION,
            "provider": request.provider,
            "upstream_base": request.upstream_base,
            "body": request.body,
        }))
        .map_err(|_| boxed(ValidationError::new("body is not JSON data")))?;
        if payload.len() > MAX_EGRESS_REQUEST_BYTES {
            return Err(boxed(ValidationError::new(
                "egress request exceeds its bound",
            )));
        }
        let request_path = create_request_file(&root, &payload)?;
        let result = self.run("egress-admit", &root, &request_path);
        let _ = fs::remove_file(&request_path);
        let document = strict_json_loads(&result?, "egress admission")
            .map_err(|_| bad("response is not strict JSON"))?;
        parse_egress_admission(&document)
    }
}
