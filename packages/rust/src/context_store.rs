// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
//! Preview: read-only access to the local Engine's Context Store.
//!
//! [`SubprocessEngineClient::read_policy_evidence`] returns the scope's
//! content-free read-strategy evidence (`lean-ctx engine
//! context-policy-evidence`); [`SubprocessEngineClient::read_task_lineage`]
//! one task's decision lineage (`lean-ctx engine context-lineage`) with every
//! missing link named as a gap. Both reads are confined to one tenant/project
//! scope. Parsing mirrors the Engine's validation: unknown fields or values
//! and inconsistent counts are rejected, and "unmeasured" never reads as a
//! passing measurement. Contract `leanctx-context-store-preview` 0.1 — may
//! change in minor releases.

use std::collections::BTreeMap;
use std::error::Error as StdError;
use std::fs;
use std::path::Path;

use serde_json::{Map, Value};

use crate::engine::{create_request_file, SubprocessEngineClient};
use crate::errors::{boxed, EngineProtocolError, ValidationError};
use crate::protocol::strict_json_loads;

pub const CONTEXT_STORE_PREVIEW_CONTRACT: &str = "leanctx-context-store-preview";
pub const CONTEXT_STORE_PREVIEW_VERSION: &str = "0.1.0";
pub const MAX_STORE_REQUEST_BYTES: usize = 16 * 1024;
pub const MAX_STORE_RESPONSE_BYTES: usize = 8 * 1024 * 1024;

const MAX_RECORDS: usize = 4096;
const MAX_LINEAGE_ITEMS: usize = 4096;
const MAX_REF_BYTES: usize = 512;
const MAX_TEXT_BYTES: usize = 4096;
const MAX_SAFE: i64 = (1 << 53) - 1;

const TASK_CLASSES: &[&str] = &[
    "bug_fix",
    "refactor",
    "test_addition",
    "documentation",
    "investigation",
];
const LANGUAGES: &[&str] = &[
    "rust",
    "python",
    "type_script",
    "java_script",
    "go",
    "java",
    "c",
    "cpp",
    "c_sharp",
    "swift",
    "kotlin",
    "ruby",
    "php",
    "shell",
    "other",
    "none",
];
const SIZES: &[&str] = &["tiny", "small", "medium", "large", "very_large"];
const STRATEGIES: &[&str] = &[
    "full",
    "map",
    "signatures",
    "aggressive",
    "entropy",
    "task",
    "reference",
    "diff",
    "lines",
    "auto",
    "other",
];
const EVIDENCE_TIERS: &[&str] = &[
    "mechanism",
    "deterministic_quality",
    "recorded_regression",
    "live_task_evaluation",
    "production_outcome",
];
const VERDICTS: &[&str] = &["improved", "non_inferior", "regressed", "underpowered"];
const STEP_KINDS: &[&str] = &[
    "task_started",
    "plan_created",
    "context_delivered",
    "model_invoked",
    "engine_invoked",
    "receipt_signed",
    "canonical_receipt_recorded",
    "outcome_recorded",
    "decision_recorded",
];
const OUTCOMES: &[&str] = &["accepted", "rejected", "unknown"];
const DELIVERY_OUTCOMES: &[&str] = &["delivered", "withheld", "failed"];
const DELIVERY_ERRORS: &[&str] = &["missing", "unreadable", "tampered"];
const GAPS: &[&str] = &[
    "ledger_unverified",
    "no_plan_recorded",
    "no_delivery_recorded",
    "delivery_unverified",
    "deliveries_incomplete",
    "no_outcome_recorded",
];

type Result<T> = std::result::Result<T, Box<dyn StdError + Send + Sync>>;

/// A plan's task class, dominant language and budget bucket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Workload {
    pub task_class: String,
    pub language: String,
    pub size: String,
}

/// Critical retention and recovery; `None` counts mean "not measured".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QualityEvidence {
    pub measured: bool,
    pub retained: Option<u64>,
    pub recoverable: Option<u64>,
    pub lost: Option<u64>,
    pub handles_emitted: Option<u64>,
    pub handles_verified: Option<u64>,
    pub failures: Option<u64>,
    pub critical_failures: Option<u64>,
}

/// Deliveries that left without full inspection; unmeasured is not zero.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecurityEvidence {
    pub measured: bool,
    pub regressions: Option<u64>,
}

/// One read strategy on one workload, on one UTC day.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StrategyOutcomeRecord {
    pub workload: Workload,
    pub strategy: String,
    pub observed_day: u64,
    pub samples: u64,
    pub accepted: u64,
    pub rejected: u64,
    pub explicit_overrides: u64,
    pub token_samples: u64,
    pub signal_samples: u64,
    pub bounce_tasks: u64,
    pub expand_tasks: u64,
    pub edit_failure_tasks: u64,
    pub tokens_original: u64,
    pub tokens_delivered: u64,
    pub quality: QualityEvidence,
    pub security: SecurityEvidence,
}

/// A paired task evaluation (`lean-ctx eval frontier`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StrategyEvaluation {
    pub strategy: String,
    pub evidence_tier: String,
    pub verdict: String,
    pub pairs: u64,
    pub powered: bool,
    pub delta_milli: i64,
    pub ci_low_milli: i64,
    pub ci_high_milli: i64,
    pub margin_milli: i64,
}

/// One scope's `ContextPolicyEvidenceV1`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContextPolicyEvidence {
    pub records: Vec<StrategyOutcomeRecord>,
    pub evaluations: Vec<StrategyEvaluation>,
}

/// One ledger observation: identifiers and counts, no content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LineageStep {
    pub sequence: u64,
    pub kind: String,
    pub timestamp: String,
    pub fields: BTreeMap<String, String>,
}

/// A verified Decision Receipt, summarized.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliverySummary {
    pub outcome: String,
    pub destination: String,
    pub policy_digest: Option<String>,
    pub inspected: u64,
    pub delivered: u64,
    pub withheld: u64,
    pub redactions: u64,
    pub tokens_original: u64,
    pub tokens_delivered: u64,
    pub final_context: Option<String>,
}

/// One governed delivery; unverified ones name their error.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LineageDelivery {
    pub digest: String,
    pub verified: bool,
    pub error: Option<String>,
    pub summary: Option<DeliverySummary>,
}

/// One task's lineage; `gaps` lists every missing link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskLineage {
    pub task_id: String,
    pub scope: Option<String>,
    pub steps: Vec<LineageStep>,
    pub deliveries: Vec<LineageDelivery>,
    pub ledger_error: Option<String>,
    pub outcome: String,
    pub gaps: Vec<String>,
}

impl TaskLineage {
    /// No link of plan → delivery → outcome is missing.
    pub fn is_complete(&self) -> bool {
        self.gaps.is_empty()
    }
}

/// The tenant/project scope of a read; without a project ID the Engine uses
/// the project root.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ContextStoreScope {
    pub project_id: Option<String>,
    pub tenant_id: Option<String>,
}

fn bad(message: impl AsRef<str>) -> Box<dyn StdError + Send + Sync> {
    boxed(EngineProtocolError::new(format!(
        "context store: {}",
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

fn field<'a>(value: &'a Map<String, Value>, key: &str) -> &'a Value {
    value.get(key).unwrap_or(&Value::Null)
}

fn int(value: &Value, label: &str) -> Result<u64> {
    match value.as_u64() {
        Some(number) if value.is_u64() && number <= MAX_SAFE as u64 => Ok(number),
        _ => Err(bad(format!("{label} must be a non-negative integer"))),
    }
}

fn signed(value: &Value, label: &str) -> Result<i64> {
    match value.as_i64() {
        Some(number) if (value.is_i64() || value.is_u64()) && number.abs() <= MAX_SAFE => {
            Ok(number)
        }
        _ => Err(bad(format!("{label} must be a safe integer"))),
    }
}

fn boolean(value: &Value, label: &str) -> Result<bool> {
    value
        .as_bool()
        .ok_or_else(|| bad(format!("{label} must be a boolean")))
}

fn text(value: &Value, label: &str, maximum: usize) -> Result<String> {
    match value.as_str() {
        Some(text)
            if text.len() <= maximum && !text.chars().any(|ch| ch < '\u{20}' || ch == '\u{7f}') =>
        {
            Ok(text.to_owned())
        }
        _ => Err(bad(format!("{label} must be bounded text"))),
    }
}

fn one_of(value: &Value, allowed: &[&str], label: &str) -> Result<String> {
    match value.as_str() {
        Some(text) if allowed.contains(&text) => Ok(text.to_owned()),
        _ => Err(bad(format!("{label} has unknown value {value}"))),
    }
}

fn digest(value: &Value, label: &str) -> Result<String> {
    let valid = value.as_str().map_or(false, |text| {
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

fn optional_digest(value: &Value, label: &str) -> Result<Option<String>> {
    if value.is_null() {
        Ok(None)
    } else {
        digest(value, label).map(Some)
    }
}

fn list<'a>(value: &'a Value, label: &str, maximum: usize) -> Result<&'a Vec<Value>> {
    match value.as_array() {
        Some(items) if items.len() <= maximum => Ok(items),
        _ => Err(bad(format!("{label} must be a list of at most {maximum}"))),
    }
}

fn index(allowed: &[&str], value: &str) -> usize {
    allowed
        .iter()
        .position(|item| *item == value)
        .unwrap_or(usize::MAX)
}

fn workload(raw: &Value, label: &str) -> Result<Workload> {
    let value = object(raw, label)?;
    keys(value, &["task_class", "language", "size"], &[], label)?;
    Ok(Workload {
        task_class: one_of(
            field(value, "task_class"),
            TASK_CLASSES,
            &format!("{label}.task_class"),
        )?,
        language: one_of(
            field(value, "language"),
            LANGUAGES,
            &format!("{label}.language"),
        )?,
        size: one_of(field(value, "size"), SIZES, &format!("{label}.size"))?,
    })
}

fn quality(raw: &Value, label: &str) -> Result<QualityEvidence> {
    let value = object(raw, label)?;
    match value.get("state").and_then(Value::as_str) {
        Some("unmeasured") => {
            keys(value, &["state"], &[], label)?;
            return Ok(QualityEvidence {
                measured: false,
                retained: None,
                recoverable: None,
                lost: None,
                handles_emitted: None,
                handles_verified: None,
                failures: None,
                critical_failures: None,
            });
        }
        Some("measured") => {}
        _ => return Err(bad(format!("{label}.state has an unknown value"))),
    }
    keys(value, &["state", "retention", "recovery"], &[], label)?;
    let retention = object(field(value, "retention"), &format!("{label}.retention"))?;
    keys(
        retention,
        &["retained", "recoverable", "lost"],
        &[],
        &format!("{label}.retention"),
    )?;
    let recovery = object(field(value, "recovery"), &format!("{label}.recovery"))?;
    keys(
        recovery,
        &[
            "handles_emitted",
            "handles_verified",
            "failures",
            "critical_failures",
        ],
        &[],
        &format!("{label}.recovery"),
    )?;
    let count =
        |map: &Map<String, Value>, key: &str| int(field(map, key), &format!("{label}.{key}"));
    let (emitted, verified, failures, critical) = (
        count(recovery, "handles_emitted")?,
        count(recovery, "handles_verified")?,
        count(recovery, "failures")?,
        count(recovery, "critical_failures")?,
    );
    if verified > emitted || failures > emitted || critical > failures {
        return Err(bad(format!("{label} has impossible recovery counts")));
    }
    Ok(QualityEvidence {
        measured: true,
        retained: Some(count(retention, "retained")?),
        recoverable: Some(count(retention, "recoverable")?),
        lost: Some(count(retention, "lost")?),
        handles_emitted: Some(emitted),
        handles_verified: Some(verified),
        failures: Some(failures),
        critical_failures: Some(critical),
    })
}

fn security(raw: &Value, label: &str) -> Result<SecurityEvidence> {
    let value = object(raw, label)?;
    match value.get("state").and_then(Value::as_str) {
        Some("unmeasured") => {
            keys(value, &["state"], &[], label)?;
            Ok(SecurityEvidence {
                measured: false,
                regressions: None,
            })
        }
        Some("measured") => {
            keys(value, &["state", "regressions"], &[], label)?;
            Ok(SecurityEvidence {
                measured: true,
                regressions: Some(int(field(value, "regressions"), label)?),
            })
        }
        _ => Err(bad(format!("{label}.state has an unknown value"))),
    }
}

const RECORD_COUNTS: &[&str] = &[
    "observed_day",
    "samples",
    "accepted",
    "rejected",
    "explicit_overrides",
    "token_samples",
    "tokens_original",
    "tokens_delivered",
];
const SIGNAL_COUNTS: &[&str] = &[
    "signal_samples",
    "bounce_tasks",
    "expand_tasks",
    "edit_failure_tasks",
];

fn outcome_record(raw: &Value, label: &str) -> Result<StrategyOutcomeRecord> {
    let value = object(raw, label)?;
    let mut required = vec!["workload", "strategy", "quality", "security"];
    required.extend_from_slice(RECORD_COUNTS);
    keys(value, &required, SIGNAL_COUNTS, label)?;
    let count = |key: &str| int(field(value, key), &format!("{label}.{key}"));
    // Signal counts default to zero, like the Engine's own deserializer.
    let signal = |key: &str| match value.get(key) {
        None => Ok(0),
        Some(raw) => int(raw, &format!("{label}.{key}")),
    };
    let record = StrategyOutcomeRecord {
        workload: workload(field(value, "workload"), &format!("{label}.workload"))?,
        strategy: one_of(
            field(value, "strategy"),
            STRATEGIES,
            &format!("{label}.strategy"),
        )?,
        observed_day: count("observed_day")?,
        samples: count("samples")?,
        accepted: count("accepted")?,
        rejected: count("rejected")?,
        explicit_overrides: count("explicit_overrides")?,
        token_samples: count("token_samples")?,
        signal_samples: signal("signal_samples")?,
        bounce_tasks: signal("bounce_tasks")?,
        expand_tasks: signal("expand_tasks")?,
        edit_failure_tasks: signal("edit_failure_tasks")?,
        tokens_original: count("tokens_original")?,
        tokens_delivered: count("tokens_delivered")?,
        quality: quality(field(value, "quality"), &format!("{label}.quality"))?,
        security: security(field(value, "security"), &format!("{label}.security"))?,
    };
    if record.samples == 0 {
        return Err(bad(format!("{label} has no samples")));
    }
    if record.accepted + record.rejected != record.samples {
        return Err(bad(format!(
            "{label}: accepted + rejected must equal samples"
        )));
    }
    if record.explicit_overrides > record.samples || record.token_samples > record.samples {
        return Err(bad(format!("{label} counts more tasks than samples")));
    }
    if record.signal_samples > record.samples
        || [
            record.bounce_tasks,
            record.expand_tasks,
            record.edit_failure_tasks,
        ]
        .iter()
        .any(|count| *count > record.signal_samples)
    {
        return Err(bad(format!(
            "{label} signal counts exceed their attributed tasks"
        )));
    }
    if record.token_samples == 0 && (record.tokens_original > 0 || record.tokens_delivered > 0) {
        return Err(bad(format!("{label} has tokens without token samples")));
    }
    if record.tokens_delivered > record.tokens_original {
        return Err(bad(format!("{label} delivered more tokens than original")));
    }
    Ok(record)
}

fn evaluation(raw: &Value, label: &str) -> Result<StrategyEvaluation> {
    let value = object(raw, label)?;
    keys(
        value,
        &[
            "strategy",
            "evidence_tier",
            "verdict",
            "pairs",
            "powered",
            "delta_milli",
            "ci_low_milli",
            "ci_high_milli",
            "margin_milli",
        ],
        &[],
        label,
    )?;
    let milli = |key: &str| signed(field(value, key), &format!("{label}.{key}"));
    let parsed = StrategyEvaluation {
        strategy: one_of(
            field(value, "strategy"),
            STRATEGIES,
            &format!("{label}.strategy"),
        )?,
        evidence_tier: one_of(
            field(value, "evidence_tier"),
            EVIDENCE_TIERS,
            &format!("{label}.evidence_tier"),
        )?,
        verdict: one_of(
            field(value, "verdict"),
            VERDICTS,
            &format!("{label}.verdict"),
        )?,
        pairs: int(field(value, "pairs"), &format!("{label}.pairs"))?,
        powered: boolean(field(value, "powered"), &format!("{label}.powered"))?,
        delta_milli: milli("delta_milli")?,
        ci_low_milli: milli("ci_low_milli")?,
        ci_high_milli: milli("ci_high_milli")?,
        margin_milli: milli("margin_milli")?,
    };
    if parsed.strategy == "other" {
        return Err(bad(format!("{label} must name a known strategy")));
    }
    if parsed.ci_low_milli > parsed.ci_high_milli || parsed.margin_milli < 0 {
        return Err(bad(format!("{label} has an impossible interval")));
    }
    if parsed.powered && parsed.pairs == 0 {
        return Err(bad(format!("{label} is powered without pairs")));
    }
    Ok(parsed)
}

fn record_key(record: &StrategyOutcomeRecord) -> (usize, usize, usize, usize, u64) {
    (
        index(TASK_CLASSES, &record.workload.task_class),
        index(LANGUAGES, &record.workload.language),
        index(SIZES, &record.workload.size),
        index(STRATEGIES, &record.strategy),
        record.observed_day,
    )
}

/// Parse and validate a `ContextPolicyEvidenceV1` document.
pub fn parse_policy_evidence(raw: &Value) -> Result<ContextPolicyEvidence> {
    let value = object(raw, "evidence")?;
    keys(
        value,
        &["schema_version", "records"],
        &["evaluations"],
        "evidence",
    )?;
    if field(value, "schema_version").as_u64() != Some(1) {
        return Err(bad("unsupported evidence schema_version"));
    }
    let records = list(field(value, "records"), "records", MAX_RECORDS)?
        .iter()
        .enumerate()
        .map(|(position, item)| outcome_record(item, &format!("records[{position}]")))
        .collect::<Result<Vec<_>>>()?;
    if records
        .windows(2)
        .any(|pair| record_key(&pair[0]) >= record_key(&pair[1]))
    {
        return Err(bad(
            "records must be strictly sorted by workload, strategy and day",
        ));
    }
    let evaluations = match value.get("evaluations") {
        None => Vec::new(),
        Some(raw) => list(raw, "evaluations", STRATEGIES.len())?
            .iter()
            .enumerate()
            .map(|(position, item)| evaluation(item, &format!("evaluations[{position}]")))
            .collect::<Result<Vec<_>>>()?,
    };
    if evaluations
        .windows(2)
        .any(|pair| index(STRATEGIES, &pair[0].strategy) >= index(STRATEGIES, &pair[1].strategy))
    {
        return Err(bad("evaluations must be one per strategy, sorted"));
    }
    Ok(ContextPolicyEvidence {
        records,
        evaluations,
    })
}

fn is_field_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && bytes[0].is_ascii_lowercase()
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
}

fn step(raw: &Value, label: &str) -> Result<LineageStep> {
    let value = object(raw, label)?;
    keys(
        value,
        &["sequence", "kind", "timestamp", "fields"],
        &[],
        label,
    )?;
    let mut fields = BTreeMap::new();
    for (key, item) in object(field(value, "fields"), &format!("{label}.fields"))? {
        if !is_field_name(key) {
            return Err(bad(format!("{label}.fields has an invalid name")));
        }
        fields.insert(
            key.clone(),
            text(item, &format!("{label}.fields.{key}"), MAX_REF_BYTES)?,
        );
    }
    Ok(LineageStep {
        sequence: int(field(value, "sequence"), &format!("{label}.sequence"))?,
        kind: one_of(field(value, "kind"), STEP_KINDS, &format!("{label}.kind"))?,
        timestamp: text(
            field(value, "timestamp"),
            &format!("{label}.timestamp"),
            MAX_REF_BYTES,
        )?,
        fields,
    })
}

fn summary(raw: &Value, label: &str) -> Result<DeliverySummary> {
    let value = object(raw, label)?;
    keys(
        value,
        &[
            "outcome",
            "destination",
            "policy_digest",
            "final_context",
            "inspected",
            "delivered",
            "withheld",
            "redactions",
            "tokens_original",
            "tokens_delivered",
        ],
        &[],
        label,
    )?;
    let count = |key: &str| int(field(value, key), &format!("{label}.{key}"));
    let parsed = DeliverySummary {
        outcome: one_of(
            field(value, "outcome"),
            DELIVERY_OUTCOMES,
            &format!("{label}.outcome"),
        )?,
        destination: text(
            field(value, "destination"),
            &format!("{label}.destination"),
            MAX_REF_BYTES,
        )?,
        policy_digest: optional_digest(
            field(value, "policy_digest"),
            &format!("{label}.policy_digest"),
        )?,
        inspected: count("inspected")?,
        delivered: count("delivered")?,
        withheld: count("withheld")?,
        redactions: count("redactions")?,
        tokens_original: count("tokens_original")?,
        tokens_delivered: count("tokens_delivered")?,
        final_context: optional_digest(
            field(value, "final_context"),
            &format!("{label}.final_context"),
        )?,
    };
    if parsed.tokens_delivered > parsed.tokens_original {
        return Err(bad(format!("{label} delivered more tokens than original")));
    }
    Ok(parsed)
}

fn delivery(raw: &Value, label: &str) -> Result<LineageDelivery> {
    let value = object(raw, label)?;
    keys(value, &["digest", "verified"], &["error", "summary"], label)?;
    let verified = boolean(field(value, "verified"), &format!("{label}.verified"))?;
    if verified != value.contains_key("summary") || verified == value.contains_key("error") {
        return Err(bad(format!(
            "{label}: a verified delivery has a summary, an unverified one an error"
        )));
    }
    Ok(LineageDelivery {
        digest: digest(field(value, "digest"), &format!("{label}.digest"))?,
        verified,
        error: value
            .get("error")
            .map(|raw| one_of(raw, DELIVERY_ERRORS, &format!("{label}.error")))
            .transpose()?,
        summary: if verified {
            Some(summary(
                field(value, "summary"),
                &format!("{label}.summary"),
            )?)
        } else {
            None
        },
    })
}

/// Parse and validate a `TaskLineageV1` document.
pub fn parse_task_lineage(raw: &Value) -> Result<TaskLineage> {
    let value = object(raw, "lineage")?;
    keys(
        value,
        &[
            "schema_version",
            "task_id",
            "steps",
            "deliveries",
            "outcome",
            "gaps",
        ],
        &["scope", "ledger_error"],
        "lineage",
    )?;
    if field(value, "schema_version").as_u64() != Some(1) {
        return Err(bad("unsupported lineage schema_version"));
    }
    let mut gaps: Vec<String> = Vec::new();
    for item in list(field(value, "gaps"), "gaps", GAPS.len())? {
        let gap = one_of(item, GAPS, "gaps")?;
        if gaps.contains(&gap) {
            return Err(bad("gaps must not repeat"));
        }
        gaps.push(gap);
    }
    let ledger_error = value
        .get("ledger_error")
        .map(|raw| text(raw, "ledger_error", MAX_TEXT_BYTES))
        .transpose()?;
    if ledger_error.is_some() != gaps.iter().any(|gap| gap == "ledger_unverified") {
        return Err(bad(
            "a ledger error and the ledger_unverified gap go together",
        ));
    }
    let steps = list(field(value, "steps"), "steps", MAX_LINEAGE_ITEMS)?
        .iter()
        .enumerate()
        .map(|(position, item)| step(item, &format!("steps[{position}]")))
        .collect::<Result<Vec<_>>>()?;
    let deliveries = list(field(value, "deliveries"), "deliveries", MAX_LINEAGE_ITEMS)?
        .iter()
        .enumerate()
        .map(|(position, item)| delivery(item, &format!("deliveries[{position}]")))
        .collect::<Result<Vec<_>>>()?;
    if deliveries.iter().any(|item| !item.verified)
        != gaps.iter().any(|gap| gap == "delivery_unverified")
    {
        return Err(bad(
            "an unverified delivery and the delivery_unverified gap go together",
        ));
    }
    Ok(TaskLineage {
        task_id: text(field(value, "task_id"), "task_id", MAX_REF_BYTES)?,
        scope: value
            .get("scope")
            .map(|raw| text(raw, "scope", MAX_TEXT_BYTES))
            .transpose()?,
        steps,
        deliveries,
        ledger_error,
        outcome: one_of(field(value, "outcome"), OUTCOMES, "outcome")?,
        gaps,
    })
}

fn store_request(scope: &ContextStoreScope, task_id: Option<&str>) -> Result<Vec<u8>> {
    let mut request = serde_json::json!({
        "schema_version": 1,
        "transport_version": 1,
        "engine_interface_version": "1.0.0",
    });
    for (key, item) in [
        ("project_id", scope.project_id.as_deref()),
        ("tenant_id", scope.tenant_id.as_deref()),
        ("task_id", task_id),
    ] {
        let Some(item) = item else { continue };
        if item.trim().is_empty() || item.len() > MAX_REF_BYTES {
            return Err(boxed(ValidationError::new(format!(
                "{key} must be a non-empty bounded string"
            ))));
        }
        request[key] = Value::String(item.to_owned());
    }
    serde_json::to_vec(&request).map_err(|_| boxed(ValidationError::new("request is not JSON")))
}

impl SubprocessEngineClient {
    fn store_read(
        &self,
        operation: &str,
        project_root: &Path,
        payload: &[u8],
        body: &str,
    ) -> Result<Value> {
        if payload.len() > MAX_STORE_REQUEST_BYTES {
            return Err(boxed(ValidationError::new(
                "context store request exceeds its bound",
            )));
        }
        let root = self.validate_root(project_root)?;
        let request_path = create_request_file(&root, payload)?;
        let result = self.run(operation, &root, &request_path);
        let _ = fs::remove_file(&request_path);
        let raw = result?;
        if raw.len() > MAX_STORE_RESPONSE_BYTES {
            return Err(bad("response exceeds its bound"));
        }
        let document = strict_json_loads(&raw, "context store")
            .map_err(|_| bad("response is not strict JSON"))?;
        let value = object(&document, "response")?;
        keys(
            value,
            &[
                "schema_version",
                "transport_version",
                "engine_interface_version",
                body,
            ],
            &[],
            "response",
        )?;
        if field(value, "schema_version").as_u64() != Some(1)
            || field(value, "transport_version").as_u64() != Some(1)
            || field(value, "engine_interface_version").as_str() != Some("1.0.0")
        {
            return Err(bad("unsupported response versions"));
        }
        Ok(field(value, body).clone())
    }

    /// Preview: the scope's read-strategy evidence.
    pub fn read_policy_evidence(
        &self,
        project_root: &Path,
        scope: &ContextStoreScope,
    ) -> Result<ContextPolicyEvidence> {
        let payload = store_request(scope, None)?;
        parse_policy_evidence(&self.store_read(
            "context-policy-evidence",
            project_root,
            &payload,
            "evidence",
        )?)
    }

    /// Preview: one task's lineage within the scope.
    pub fn read_task_lineage(
        &self,
        project_root: &Path,
        task_id: &str,
        scope: &ContextStoreScope,
    ) -> Result<TaskLineage> {
        if task_id.trim().is_empty() {
            return Err(boxed(ValidationError::new(
                "task_id must be a non-empty string",
            )));
        }
        let payload = store_request(scope, Some(task_id))?;
        parse_task_lineage(&self.store_read(
            "context-lineage",
            project_root,
            &payload,
            "lineage",
        )?)
    }
}
