use std::collections::BTreeSet;

use serde_json::{Map, Value};

use crate::errors::ValidationError;
use crate::protocol::{sha256_digest, validate_digest, ENGINE_INTERFACE_VERSION};

pub(crate) const MAX_REQUEST_BYTES: usize = 64 * 1024;
const MAX_QUERY_BYTES: usize = 16 * 1024;
const MAX_PLAN_TOKENS: u64 = 1_048_576;
const MAX_PLAN_CANDIDATES: u64 = 256;
const MAX_PLAN_ITEMS: usize = 256;
const MAX_IDENTIFIER_BYTES: usize = 256;
const MAX_REFERENCE_BYTES: usize = 1024;
const MAX_EXTENSION_VALUE_BYTES: usize = 64 * 1024;
const MAX_EXTENSION_DEPTH: usize = 8;
const MAX_SOURCE_IDS: usize = 64;

const SOURCE_TYPES: &[&str] = &[
    "filesystem",
    "issue_tracker",
    "relational_database",
    "other",
];
const SOURCE_PERMISSIONS: &[&str] = &["permitted", "denied", "unknown"];
const CLASSIFICATIONS: &[&str] = &["Public", "Internal", "Confidential", "Restricted"];
const DISPOSITIONS: &[&str] = &["selected", "excluded", "deferred"];
const REASON_CODES: &[&str] = &[
    "relevant",
    "required",
    "cache_hit",
    "budget_exceeded",
    "lower_utility",
    "policy_excluded",
    "deferred_for_later",
    "other",
];
const EVIDENCE_KINDS: &[&str] = &[
    "ProviderReceipt",
    "RuntimeLog",
    "SignedBatch",
    "QualityMeasurement",
    "ExperimentOutcome",
];
const SIGNATURE_STATUSES: &[&str] = &["Verified", "Unverified", "NotSigned"];

const PLAN_KEYS: &[&str] = &[
    "schema_version",
    "context_plan_id",
    "task_id",
    "projection_digest",
    "budget_tokens",
    "selections",
    "provider_stats",
    "policy_decision_refs",
    "evidence",
];
const DESCRIPTOR_KEYS: &[&str] = &[
    "object_ref",
    "source_id",
    "source_type",
    "content_digest",
    "revision",
    "owner",
    "observed_at",
    "valid_until",
    "classification",
    "permission",
];
const SELECTION_KEYS: &[&str] = &[
    "source_ref",
    "provider",
    "disposition",
    "token_count",
    "sha256_digest",
    "reason_codes",
    "reason_detail",
];
const EVIDENCE_KEYS: &[&str] = &[
    "schema_version",
    "kind",
    "uri",
    "digest",
    "signature_status",
    "media_type",
];
const PROVIDER_STATS_KEYS: &[&str] = &["candidates_offered", "candidates_selected", "tokens_used"];

/// The canonical, non-executing Engine source-planning request.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EnginePlanningRequest {
    task_id: String,
    query: String,
    budget_tokens: u64,
    max_candidates: u64,
}

impl EnginePlanningRequest {
    /// Creates a planning request with the protocol default of 64 candidates.
    pub fn new(
        task_id: impl AsRef<str>,
        query: impl AsRef<str>,
        budget_tokens: u64,
    ) -> Result<Self, ValidationError> {
        Self::with_max_candidates(task_id, query, budget_tokens, 64)
    }

    /// Creates a planning request with an explicit candidate bound.
    pub fn with_max_candidates(
        task_id: impl AsRef<str>,
        query: impl AsRef<str>,
        budget_tokens: u64,
        max_candidates: u64,
    ) -> Result<Self, ValidationError> {
        let task_id = text(
            task_id.as_ref(),
            "task_id",
            MAX_IDENTIFIER_BYTES,
            true,
            true,
        )?;
        let query = text(query.as_ref(), "query", MAX_QUERY_BYTES, false, true)?;
        if query.trim().is_empty() {
            return Err(ValidationError::new("query must not be blank"));
        }
        if !(1..=MAX_PLAN_TOKENS).contains(&budget_tokens) {
            return Err(ValidationError::new(
                "budget_tokens is outside its protocol bounds",
            ));
        }
        if !(1..=MAX_PLAN_CANDIDATES).contains(&max_candidates) {
            return Err(ValidationError::new(
                "max_candidates is outside its protocol bounds",
            ));
        }
        let request = Self {
            task_id,
            query,
            budget_tokens,
            max_candidates,
        };
        if canonical_bytes(&request.to_value())?.len() > MAX_REQUEST_BYTES {
            return Err(ValidationError::new(
                "Engine context-plan request exceeds its byte bound",
            ));
        }
        Ok(request)
    }

    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn query(&self) -> &str {
        &self.query
    }

    pub fn budget_tokens(&self) -> u64 {
        self.budget_tokens
    }

    pub fn max_candidates(&self) -> u64 {
        self.max_candidates
    }

    /// Returns the detached strict-versioned request projection.
    pub fn to_value(&self) -> Value {
        let mut result = Map::new();
        result.insert("schema_version".to_owned(), Value::from(1));
        result.insert("transport_version".to_owned(), Value::from(1));
        result.insert(
            "engine_interface_version".to_owned(),
            Value::String(ENGINE_INTERFACE_VERSION.to_owned()),
        );
        result.insert("task_id".to_owned(), Value::String(self.task_id.clone()));
        result.insert("query".to_owned(), Value::String(self.query.clone()));
        result.insert("budget_tokens".to_owned(), Value::from(self.budget_tokens));
        result.insert(
            "max_candidates".to_owned(),
            Value::from(self.max_candidates),
        );
        Value::Object(result)
    }
}

pub(crate) fn normalize_source_ids<S: AsRef<str>>(
    source_ids: &[S],
) -> Result<(Vec<String>, BTreeSet<String>), ValidationError> {
    if source_ids.len() > MAX_SOURCE_IDS {
        return Err(ValidationError::new(
            "source_ids exceeds the Engine source bound",
        ));
    }
    let mut normalized = Vec::with_capacity(source_ids.len());
    let mut unique = BTreeSet::new();
    for source_id in source_ids {
        let source_id = canonical_uuid(source_id.as_ref(), "source_id")?;
        if !unique.insert(source_id.clone()) {
            return Err(ValidationError::new(
                "source_ids must not contain duplicates",
            ));
        }
        normalized.push(source_id);
    }
    Ok((normalized, unique))
}

pub(crate) fn canonical_uuid(value: &str, field: &str) -> Result<String, ValidationError> {
    let bytes = value.as_bytes();
    if bytes.len() != 36
        || ![8, 13, 18, 23]
            .iter()
            .all(|position| bytes[*position] == b'-')
        || bytes.iter().enumerate().any(|(position, byte)| {
            ![8, 13, 18, 23].contains(&position) && !byte.is_ascii_hexdigit()
        })
        || value.trim() != value
    {
        return Err(ValidationError::new(format!(
            "{field} must be a canonical UUID"
        )));
    }
    let normalized = value.to_ascii_lowercase();
    if normalized == "00000000-0000-0000-0000-000000000000" {
        return Err(ValidationError::new(format!(
            "{field} must be a non-nil canonical UUID"
        )));
    }
    Ok(normalized)
}

pub(crate) fn parse_source_plan(
    value: &Value,
    request: &EnginePlanningRequest,
    requested_ids: &BTreeSet<String>,
) -> Result<Value, ValidationError> {
    let source_plan = object(value, "Engine source-plan response")?;
    exact_keys(
        source_plan,
        &["result", "source_bindings", "binding_digest"],
        "Engine source-plan response",
    )?;

    let result_raw = source_plan
        .get("result")
        .ok_or_else(|| invalid("Engine source-plan result is missing"))?;
    let result_object = object(result_raw, "Engine source-plan result")?;
    exact_keys(
        result_object,
        &[
            "schema_version",
            "transport_version",
            "engine_interface_version",
            "plan",
        ],
        "Engine source-plan result",
    )?;
    validate_header(result_object, "Engine source-plan result")?;

    let plan_raw = result_object
        .get("plan")
        .ok_or_else(|| invalid("Engine source-plan result is missing plan"))?;
    let plan = parse_plan(plan_raw)?;
    let plan_object = object(&plan, "plan")?;
    if !plan_object.contains_key("projection_digest") {
        return Err(invalid(
            "Engine source-plan response requires projection_digest",
        ));
    }
    if string_field(&plan, "task_id", "plan.task_id")? != request.task_id {
        return Err(invalid(
            "Engine context-plan response task_id does not bind the request",
        ));
    }
    if unsigned_field(&plan, "budget_tokens", "plan.budget_tokens")? > request.budget_tokens {
        return Err(invalid(
            "Engine context-plan response budget exceeds the request",
        ));
    }

    let mut normalized_result = Map::new();
    normalized_result.insert("schema_version".to_owned(), Value::from(1));
    normalized_result.insert("transport_version".to_owned(), Value::from(1));
    normalized_result.insert(
        "engine_interface_version".to_owned(),
        Value::String(ENGINE_INTERFACE_VERSION.to_owned()),
    );
    normalized_result.insert("plan".to_owned(), plan.clone());
    let normalized_result = Value::Object(normalized_result);

    let bindings_raw = source_plan
        .get("source_bindings")
        .and_then(Value::as_array)
        .filter(|bindings| bindings.len() <= MAX_PLAN_ITEMS)
        .ok_or_else(|| invalid("source_bindings has an invalid shape"))?;
    let mut bindings = Vec::with_capacity(bindings_raw.len());
    let mut previous_object_ref: Option<String> = None;
    for (index, raw_binding) in bindings_raw.iter().enumerate() {
        let binding = parse_descriptor(raw_binding, &format!("source_bindings[{index}]"))?;
        let object_ref = string_field(&binding, "object_ref", "source_bindings.object_ref")?;
        if previous_object_ref
            .as_deref()
            .is_some_and(|previous| previous >= object_ref.as_str())
        {
            return Err(invalid(
                "source_bindings must be strictly sorted by object_ref",
            ));
        }
        previous_object_ref = Some(object_ref);
        bindings.push(binding);
    }

    let selections = plan_object
        .get("selections")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("plan.selections has an invalid shape"))?;
    let selected: Vec<&Value> = selections
        .iter()
        .filter(|selection| {
            selection.get("disposition").and_then(Value::as_str) == Some("selected")
        })
        .collect();
    if selected.len() != bindings.len() {
        return Err(invalid(
            "source_bindings do not match selected plan entries",
        ));
    }
    for binding in &bindings {
        let object_ref = string_field(binding, "object_ref", "source_bindings.object_ref")?;
        let source_id = string_field(binding, "source_id", "source_bindings.source_id")?;
        let content_digest =
            string_field(binding, "content_digest", "source_bindings.content_digest")?;
        if !selected.iter().any(|selection| {
            selection.get("source_ref").and_then(Value::as_str) == Some(object_ref.as_str())
                && selection.get("provider").and_then(Value::as_str) == Some(source_id.as_str())
                && selection.get("sha256_digest").and_then(Value::as_str)
                    == Some(content_digest.as_str())
        }) {
            return Err(invalid(
                "source binding does not match a selected plan entry",
            ));
        }
    }

    let normalized_bindings = Value::Array(bindings);
    let binding_digest = source_plan
        .get("binding_digest")
        .and_then(Value::as_str)
        .ok_or_else(|| invalid("binding_digest must be a string"))?;
    let binding_digest = validate_digest(binding_digest, "binding_digest")?;
    let expected_digest = sha256_digest(&canonical_bytes(&Value::Array(vec![
        normalized_result.clone(),
        normalized_bindings.clone(),
    ]))?);
    if binding_digest != expected_digest {
        return Err(invalid(
            "binding_digest does not match canonical source bindings",
        ));
    }

    let mut normalized = Map::new();
    normalized.insert("result".to_owned(), normalized_result);
    normalized.insert("source_bindings".to_owned(), normalized_bindings);
    normalized.insert("binding_digest".to_owned(), Value::String(binding_digest));
    let normalized = Value::Object(normalized);
    validate_source_scope(&normalized, requested_ids)?;
    Ok(normalized)
}

pub(crate) fn validate_source_scope(
    source_plan: &Value,
    requested_ids: &BTreeSet<String>,
) -> Result<(), ValidationError> {
    let result = source_plan
        .get("result")
        .ok_or_else(|| invalid("Engine source-plan result is missing"))?;
    let plan = result
        .get("plan")
        .ok_or_else(|| invalid("Engine source-plan projection is missing"))?;
    let selections = plan
        .get("selections")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("Engine source-plan selections are invalid"))?;
    for selection in selections {
        let source_ref = string_field(selection, "source_ref", "plan.selection.source_ref")?;
        let provider = string_field(selection, "provider", "plan.selection.provider")?;
        if !requested_ids.contains(&source_ref) || !requested_ids.contains(&provider) {
            return Err(invalid(
                "Enterprise Engine selection is outside requested sources",
            ));
        }
    }
    let bindings = source_plan
        .get("source_bindings")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("Engine source-plan bindings are invalid"))?;
    for binding in bindings {
        let object_ref = string_field(binding, "object_ref", "source_bindings.object_ref")?;
        let source_id = string_field(binding, "source_id", "source_bindings.source_id")?;
        let permission = string_field(binding, "permission", "source_bindings.permission")?;
        if !requested_ids.contains(&object_ref) || !requested_ids.contains(&source_id) {
            return Err(invalid(
                "Enterprise Engine source binding is outside requested sources",
            ));
        }
        if permission != "permitted" {
            return Err(invalid(
                "Enterprise Engine selected source is not permitted",
            ));
        }
    }
    Ok(())
}

fn parse_plan(value: &Value) -> Result<Value, ValidationError> {
    let raw = object(value, "plan")?;
    for key in [
        "schema_version",
        "context_plan_id",
        "task_id",
        "budget_tokens",
        "selections",
    ] {
        if !raw.contains_key(key) {
            return Err(invalid("plan is missing a required field"));
        }
    }
    let extensions = parse_extensions(raw, PLAN_KEYS)?;
    if unsigned_field(value, "schema_version", "plan.schema_version")? != 1 {
        return Err(invalid("plan.schema_version is unsupported"));
    }
    let selection_values = raw
        .get("selections")
        .and_then(Value::as_array)
        .filter(|selections| selections.len() <= MAX_PLAN_ITEMS)
        .ok_or_else(|| invalid("plan.selections has an invalid shape"))?;
    let mut selections = Vec::with_capacity(selection_values.len());
    let mut source_refs = BTreeSet::new();
    let mut selected_tokens = 0u64;
    for (index, selection_value) in selection_values.iter().enumerate() {
        let selection = parse_selection(selection_value, &format!("plan.selections[{index}]"))?;
        let source_ref = string_field(&selection, "source_ref", "plan.selection.source_ref")?;
        if !source_refs.insert(source_ref) {
            return Err(invalid(
                "plan.selections contains duplicate source_ref values",
            ));
        }
        if string_field(&selection, "disposition", "plan.selection.disposition")? == "selected" {
            let token_count =
                unsigned_field(&selection, "token_count", "plan.selection.token_count")?;
            selected_tokens = selected_tokens
                .checked_add(token_count)
                .ok_or_else(|| invalid("plan selected context exceeds budget_tokens"))?;
        }
        selections.push(selection);
    }
    let budget = unsigned_field(value, "budget_tokens", "plan.budget_tokens")?;
    if selected_tokens > budget {
        return Err(invalid("plan selected context exceeds budget_tokens"));
    }
    let mut result = Map::new();
    result.insert("schema_version".to_owned(), Value::from(1));
    result.insert(
        "context_plan_id".to_owned(),
        Value::String(identifier_field(
            value,
            "context_plan_id",
            "plan.context_plan_id",
        )?),
    );
    result.insert(
        "task_id".to_owned(),
        Value::String(identifier_field(value, "task_id", "plan.task_id")?),
    );
    result.insert("budget_tokens".to_owned(), Value::from(budget));
    result.insert("selections".to_owned(), Value::Array(selections));

    if let Some(projection) = raw
        .get("projection_digest")
        .filter(|value| !value.is_null())
    {
        let digest = projection_digest(projection, "plan.projection_digest")?;
        result.insert("projection_digest".to_owned(), Value::String(digest));
    }
    if raw.contains_key("provider_stats") {
        let stats = parse_provider_stats(&raw["provider_stats"])?;
        if !stats.is_empty() {
            result.insert("provider_stats".to_owned(), Value::Object(stats));
        }
    }
    if let Some(ref_values) = raw.get("policy_decision_refs") {
        let references = ref_values
            .as_array()
            .filter(|references| references.len() <= MAX_PLAN_ITEMS)
            .ok_or_else(|| invalid("plan.policy_decision_refs has an invalid shape"))?;
        let mut seen = BTreeSet::new();
        let mut normalized = Vec::with_capacity(references.len());
        for reference in references {
            let reference = identifier(reference, "plan.policy_decision_refs")?;
            if !seen.insert(reference.clone()) {
                return Err(invalid("plan.policy_decision_refs contains duplicates"));
            }
            normalized.push(Value::String(reference));
        }
        if !normalized.is_empty() {
            result.insert("policy_decision_refs".to_owned(), Value::Array(normalized));
        }
    }
    if let Some(evidence_values) = raw.get("evidence") {
        let evidence_values = evidence_values
            .as_array()
            .filter(|evidence| evidence.len() <= MAX_PLAN_ITEMS)
            .ok_or_else(|| invalid("plan.evidence has an invalid shape"))?;
        let mut evidence = Vec::with_capacity(evidence_values.len());
        for (index, value) in evidence_values.iter().enumerate() {
            evidence.push(parse_evidence(value, &format!("plan.evidence[{index}]"))?);
        }
        if !evidence.is_empty() {
            result.insert("evidence".to_owned(), Value::Array(evidence));
        }
    }
    for (key, value) in extensions {
        result.insert(key, value);
    }
    let mut result = Value::Object(result);
    if let Some(projection_digest) = result.get("projection_digest").and_then(Value::as_str) {
        let mut unsigned = result
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("plan is not an object"))?;
        unsigned.remove("projection_digest");
        let expected = sha256_digest(&canonical_bytes(&Value::Object(unsigned))?);
        if projection_digest != expected {
            return Err(invalid(
                "plan.projection_digest does not match canonical projection content",
            ));
        }
    }
    Ok(std::mem::take(&mut result))
}

fn parse_selection(value: &Value, field: &str) -> Result<Value, ValidationError> {
    let raw = object(value, field)?;
    if raw
        .keys()
        .any(|key| !SELECTION_KEYS.contains(&key.as_str()))
    {
        return Err(invalid(format!(
            "{field} fields do not match the v1 contract"
        )));
    }
    for key in [
        "source_ref",
        "provider",
        "disposition",
        "token_count",
        "reason_codes",
    ] {
        if !raw.contains_key(key) {
            return Err(invalid(format!("{field} is missing a required field")));
        }
    }
    let reasons = raw
        .get("reason_codes")
        .and_then(Value::as_array)
        .filter(|reasons| !reasons.is_empty() && reasons.len() <= MAX_PLAN_ITEMS)
        .ok_or_else(|| invalid(format!("{field}.reason_codes has an invalid shape")))?;
    let mut normalized_reasons = Vec::with_capacity(reasons.len());
    let mut reason_set = BTreeSet::new();
    for reason in reasons {
        let reason = enum_value(reason, &format!("{field}.reason_codes"), REASON_CODES)?;
        if !reason_set.insert(reason.clone()) {
            return Err(invalid(format!("{field}.reason_codes contains duplicates")));
        }
        normalized_reasons.push(Value::String(reason));
    }
    let disposition = enum_value(
        raw.get("disposition").unwrap(),
        &format!("{field}.disposition"),
        DISPOSITIONS,
    )?;
    let mut result = Map::new();
    result.insert(
        "source_ref".to_owned(),
        Value::String(reference_field(
            value,
            "source_ref",
            &format!("{field}.source_ref"),
        )?),
    );
    result.insert(
        "provider".to_owned(),
        Value::String(reference_field(
            value,
            "provider",
            &format!("{field}.provider"),
        )?),
    );
    result.insert("disposition".to_owned(), Value::String(disposition));
    result.insert(
        "token_count".to_owned(),
        Value::from(unsigned_field(
            value,
            "token_count",
            &format!("{field}.token_count"),
        )?),
    );
    result.insert("reason_codes".to_owned(), Value::Array(normalized_reasons));
    if let Some(digest) = raw.get("sha256_digest").filter(|value| !value.is_null()) {
        result.insert(
            "sha256_digest".to_owned(),
            Value::String(projection_digest(
                digest,
                &format!("{field}.sha256_digest"),
            )?),
        );
    }
    if let Some(detail) = raw.get("reason_detail").filter(|value| !value.is_null()) {
        result.insert(
            "reason_detail".to_owned(),
            Value::String(identifier(detail, &format!("{field}.reason_detail"))?),
        );
    }
    Ok(Value::Object(result))
}

fn parse_descriptor(value: &Value, field: &str) -> Result<Value, ValidationError> {
    let raw = object(value, field)?;
    if raw
        .keys()
        .any(|key| !DESCRIPTOR_KEYS.contains(&key.as_str()))
    {
        return Err(invalid(format!(
            "{field} fields do not match the v1 contract"
        )));
    }
    for key in ["object_ref", "source_id", "source_type", "content_digest"] {
        if !raw.contains_key(key) {
            return Err(invalid(format!("{field} is missing a required field")));
        }
    }
    let observed_at = optional_timestamp(raw.get("observed_at"), &format!("{field}.observed_at"))?;
    let valid_until = optional_timestamp(raw.get("valid_until"), &format!("{field}.valid_until"))?;
    if observed_at
        .as_deref()
        .zip(valid_until.as_deref())
        .is_some_and(|(observed, valid)| valid <= observed)
    {
        return Err(invalid(format!("{field} validity window is inverted")));
    }
    let mut result = Map::new();
    result.insert(
        "object_ref".to_owned(),
        Value::String(reference_field(
            value,
            "object_ref",
            &format!("{field}.object_ref"),
        )?),
    );
    result.insert(
        "source_id".to_owned(),
        Value::String(identifier_field(
            value,
            "source_id",
            &format!("{field}.source_id"),
        )?),
    );
    result.insert(
        "source_type".to_owned(),
        Value::String(enum_field(
            value,
            "source_type",
            &format!("{field}.source_type"),
            SOURCE_TYPES,
        )?),
    );
    result.insert(
        "content_digest".to_owned(),
        Value::String(digest_field(
            value,
            "content_digest",
            &format!("{field}.content_digest"),
        )?),
    );
    result.insert(
        "revision".to_owned(),
        optional_reference(raw.get("revision"), &format!("{field}.revision"))?
            .map_or(Value::Null, Value::String),
    );
    result.insert(
        "owner".to_owned(),
        optional_reference(raw.get("owner"), &format!("{field}.owner"))?
            .map_or(Value::Null, Value::String),
    );
    result.insert(
        "observed_at".to_owned(),
        observed_at.map_or(Value::Null, Value::String),
    );
    result.insert(
        "valid_until".to_owned(),
        valid_until.map_or(Value::Null, Value::String),
    );
    let classification = match raw.get("classification") {
        None | Some(Value::Null) => None,
        Some(value) => Some(enum_value(
            value,
            &format!("{field}.classification"),
            CLASSIFICATIONS,
        )?),
    };
    result.insert(
        "classification".to_owned(),
        classification.map_or(Value::Null, Value::String),
    );
    let permission = match raw.get("permission") {
        None => "unknown".to_owned(),
        Some(value) => enum_value(value, &format!("{field}.permission"), SOURCE_PERMISSIONS)?,
    };
    result.insert("permission".to_owned(), Value::String(permission));
    Ok(Value::Object(result))
}

fn parse_provider_stats(value: &Value) -> Result<Map<String, Value>, ValidationError> {
    let raw = object(value, "plan.provider_stats")?;
    if raw.len() > MAX_PLAN_ITEMS {
        return Err(invalid("plan.provider_stats exceeds its item bound"));
    }
    let mut result = Map::new();
    for (provider, stats_value) in raw {
        let provider = text(
            provider,
            "plan.provider_stats key",
            MAX_IDENTIFIER_BYTES,
            true,
            true,
        )?;
        let stats = object(stats_value, "plan.provider_stats entry")?;
        exact_keys(stats, PROVIDER_STATS_KEYS, "plan.provider_stats entry")?;
        let offered = unsigned_field(
            stats_value,
            "candidates_offered",
            "plan.provider_stats.candidates_offered",
        )?;
        let selected = unsigned_field(
            stats_value,
            "candidates_selected",
            "plan.provider_stats.candidates_selected",
        )?;
        if selected > offered {
            return Err(invalid("plan.provider_stats selected exceeds offered"));
        }
        let tokens = unsigned_field(
            stats_value,
            "tokens_used",
            "plan.provider_stats.tokens_used",
        )?;
        let mut entry = Map::new();
        entry.insert("candidates_offered".to_owned(), Value::from(offered));
        entry.insert("candidates_selected".to_owned(), Value::from(selected));
        entry.insert("tokens_used".to_owned(), Value::from(tokens));
        result.insert(provider, Value::Object(entry));
    }
    Ok(result)
}

fn parse_evidence(value: &Value, field: &str) -> Result<Value, ValidationError> {
    let raw = object(value, field)?;
    let extensions = parse_extensions(raw, EVIDENCE_KEYS)?;
    for key in ["kind", "uri", "digest", "signature_status"] {
        if !raw.contains_key(key) {
            return Err(invalid(format!("{field} is missing a required field")));
        }
    }
    let mut result = Map::new();
    if let Some(schema) = raw.get("schema_version").filter(|value| !value.is_null()) {
        if unsigned(schema, &format!("{field}.schema_version"))? != 1 {
            return Err(invalid(format!("{field}.schema_version is unsupported")));
        }
        result.insert("schema_version".to_owned(), Value::from(1));
    }
    result.insert(
        "kind".to_owned(),
        Value::String(enum_field(
            value,
            "kind",
            &format!("{field}.kind"),
            EVIDENCE_KINDS,
        )?),
    );
    result.insert(
        "uri".to_owned(),
        Value::String(identifier_field(value, "uri", &format!("{field}.uri"))?),
    );
    let digest = identifier_field(value, "digest", &format!("{field}.digest"))?;
    if raw
        .get("schema_version")
        .is_some_and(|schema| !schema.is_null())
    {
        let candidate = digest
            .strip_prefix("sha256:")
            .or_else(|| digest.strip_prefix("blake3:"))
            .unwrap_or(&digest);
        if candidate.len() != 64 || !candidate.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(invalid(format!(
                "{field}.digest is not a supported versioned digest"
            )));
        }
    }
    result.insert("digest".to_owned(), Value::String(digest));
    result.insert(
        "signature_status".to_owned(),
        Value::String(enum_field(
            value,
            "signature_status",
            &format!("{field}.signature_status"),
            SIGNATURE_STATUSES,
        )?),
    );
    if let Some(media_type) = raw.get("media_type").filter(|value| !value.is_null()) {
        result.insert(
            "media_type".to_owned(),
            Value::String(identifier(media_type, &format!("{field}.media_type"))?),
        );
    }
    for (key, extension) in extensions {
        result.insert(key, extension);
    }
    Ok(Value::Object(result))
}

fn parse_extensions(
    raw: &Map<String, Value>,
    reserved: &[&str],
) -> Result<Map<String, Value>, ValidationError> {
    let mut result = Map::new();
    for (key, value) in raw {
        if reserved.contains(&key.as_str()) {
            continue;
        }
        text(key, "extension key", MAX_IDENTIFIER_BYTES, true, true)?;
        validate_extension(value, 0)?;
        result.insert(key.clone(), value.clone());
    }
    if result.len() > MAX_PLAN_ITEMS {
        return Err(invalid("extensions exceed their field bound"));
    }
    Ok(result)
}

fn validate_extension(value: &Value, depth: usize) -> Result<(), ValidationError> {
    if depth > MAX_EXTENSION_DEPTH {
        return Err(invalid("extension value exceeds its nesting bound"));
    }
    match value {
        Value::Array(values) => {
            if values.len() > MAX_PLAN_ITEMS {
                return Err(invalid("extension array exceeds its item bound"));
            }
            for nested in values {
                validate_extension(nested, depth + 1)?;
            }
        }
        Value::Object(values) => {
            if values.len() > MAX_PLAN_ITEMS {
                return Err(invalid("extension object exceeds its item bound"));
            }
            for (key, nested) in values {
                text(
                    key,
                    "extension object key",
                    MAX_IDENTIFIER_BYTES,
                    true,
                    true,
                )?;
                validate_extension(nested, depth + 1)?;
            }
        }
        Value::String(value) if value.len() > MAX_EXTENSION_VALUE_BYTES => {
            return Err(invalid("extension string exceeds its byte bound"));
        }
        _ => {}
    }
    if canonical_bytes(value)?.len() > MAX_EXTENSION_VALUE_BYTES {
        return Err(invalid("extension value exceeds its serialized byte bound"));
    }
    Ok(())
}

fn validate_header(value: &Map<String, Value>, field: &str) -> Result<(), ValidationError> {
    if unsigned_field_map(value, "schema_version", &format!("{field}.schema_version"))? != 1
        || unsigned_field_map(
            value,
            "transport_version",
            &format!("{field}.transport_version"),
        )? != 1
        || value
            .get("engine_interface_version")
            .and_then(Value::as_str)
            != Some(ENGINE_INTERFACE_VERSION)
    {
        return Err(invalid(format!("{field} has an unsupported version")));
    }
    Ok(())
}

fn exact_keys(
    value: &Map<String, Value>,
    expected: &[&str],
    field: &str,
) -> Result<(), ValidationError> {
    if value.len() != expected.len() || value.keys().any(|key| !expected.contains(&key.as_str())) {
        return Err(invalid(format!(
            "{field} fields do not match the v1 contract"
        )));
    }
    Ok(())
}

fn object<'a>(value: &'a Value, field: &str) -> Result<&'a Map<String, Value>, ValidationError> {
    value
        .as_object()
        .ok_or_else(|| invalid(format!("{field} must be an object")))
}

fn text(
    value: &str,
    field: &str,
    maximum: usize,
    controls: bool,
    nonblank: bool,
) -> Result<String, ValidationError> {
    if value.is_empty() {
        return Err(invalid(format!("{field} must not be empty")));
    }
    if value.len() > maximum {
        return Err(invalid(format!("{field} exceeds {maximum} UTF-8 bytes")));
    }
    if value.contains('\0') {
        return Err(invalid(format!("{field} contains NUL")));
    }
    if controls && value.chars().any(char::is_control) {
        return Err(invalid(format!("{field} contains a control character")));
    }
    if nonblank && value.trim().is_empty() {
        return Err(invalid(format!("{field} must not be blank")));
    }
    Ok(value.to_owned())
}

fn identifier(value: &Value, field: &str) -> Result<String, ValidationError> {
    let value = value
        .as_str()
        .ok_or_else(|| invalid(format!("{field} must be a string")))?;
    text(value, field, MAX_IDENTIFIER_BYTES, true, true)
}

fn identifier_field(value: &Value, key: &str, field: &str) -> Result<String, ValidationError> {
    identifier(
        value
            .get(key)
            .ok_or_else(|| invalid(format!("{field} is missing")))?,
        field,
    )
}

fn reference_field(value: &Value, key: &str, field: &str) -> Result<String, ValidationError> {
    let value = value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid(format!("{field} must be a string")))?;
    text(value, field, MAX_REFERENCE_BYTES, true, true)
}

fn optional_reference(
    value: Option<&Value>,
    field: &str,
) -> Result<Option<String>, ValidationError> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(value) => {
            let value = value
                .as_str()
                .ok_or_else(|| invalid(format!("{field} must be a string")))?;
            text(value, field, MAX_REFERENCE_BYTES, true, true).map(Some)
        }
    }
}

fn optional_timestamp(
    value: Option<&Value>,
    field: &str,
) -> Result<Option<String>, ValidationError> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(value) => {
            let value = value
                .as_str()
                .ok_or_else(|| invalid(format!("{field} must be a string")))?;
            validate_timestamp(value, field).map(Some)
        }
    }
}

pub(crate) fn validate_timestamp(value: &str, field: &str) -> Result<String, ValidationError> {
    text(value, field, MAX_IDENTIFIER_BYTES, true, true)?;
    let bytes = value.as_bytes();
    if bytes.len() != 20
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || bytes[19] != b'Z'
        || ![0..4, 5..7, 8..10, 11..13, 14..16, 17..19]
            .iter()
            .all(|range| bytes[range.clone()].iter().all(u8::is_ascii_digit))
    {
        return Err(invalid(format!(
            "{field} must use canonical UTC timestamp syntax"
        )));
    }
    let number =
        |start: usize, end: usize| -> u32 { value[start..end].parse::<u32>().unwrap_or_default() };
    let year = number(0, 4);
    let month = number(5, 7);
    let day = number(8, 10);
    let hour = number(11, 13);
    let minute = number(14, 16);
    let second = number(17, 19);
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if year == 0
        || !(1..=12).contains(&month)
        || day == 0
        || day > days[(month - 1) as usize]
        || hour > 23
        || minute > 59
        || second > 59
    {
        return Err(invalid(format!("{field} contains an invalid date or time")));
    }
    Ok(value.to_owned())
}

fn enum_field(
    value: &Value,
    key: &str,
    field: &str,
    allowed: &[&str],
) -> Result<String, ValidationError> {
    enum_value(
        value
            .get(key)
            .ok_or_else(|| invalid(format!("{field} is missing")))?,
        field,
        allowed,
    )
}

fn enum_value(value: &Value, field: &str, allowed: &[&str]) -> Result<String, ValidationError> {
    let value = value
        .as_str()
        .filter(|value| allowed.contains(value))
        .ok_or_else(|| invalid(format!("{field} has an unsupported value")))?;
    Ok(value.to_owned())
}

fn projection_digest(value: &Value, field: &str) -> Result<String, ValidationError> {
    let value = value
        .as_str()
        .ok_or_else(|| invalid(format!("{field} must be a string")))?;
    let candidate = value.strip_prefix("sha256:").unwrap_or(value);
    if candidate.len() != 64 || !candidate.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(invalid(format!(
            "{field} must contain a 64-digit hexadecimal digest"
        )));
    }
    Ok(value.to_owned())
}

fn digest_field(value: &Value, key: &str, field: &str) -> Result<String, ValidationError> {
    let value = value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid(format!("{field} must be a string")))?;
    validate_digest(value, field)
}

fn unsigned_field(value: &Value, key: &str, field: &str) -> Result<u64, ValidationError> {
    unsigned(
        value
            .get(key)
            .ok_or_else(|| invalid(format!("{field} is missing")))?,
        field,
    )
}

fn unsigned_field_map(
    value: &Map<String, Value>,
    key: &str,
    field: &str,
) -> Result<u64, ValidationError> {
    unsigned(
        value
            .get(key)
            .ok_or_else(|| invalid(format!("{field} is missing")))?,
        field,
    )
}

fn unsigned(value: &Value, field: &str) -> Result<u64, ValidationError> {
    value
        .as_u64()
        .ok_or_else(|| invalid(format!("{field} must be an unsigned 64-bit integer")))
}

pub(crate) fn canonical_bytes(value: &Value) -> Result<Vec<u8>, ValidationError> {
    serde_json::to_vec(&canonical_value(value))
        .map_err(|_| ValidationError::new("value is not canonical JSON data"))
}

fn canonical_value(value: &Value) -> Value {
    match value {
        Value::Array(values) => Value::Array(values.iter().map(canonical_value).collect()),
        Value::Object(values) => {
            let mut entries: Vec<_> = values.iter().collect();
            entries.sort_by(|(left, _), (right, _)| left.cmp(right));
            let mut result = Map::new();
            for (key, value) in entries {
                result.insert(key.to_owned(), canonical_value(value));
            }
            Value::Object(result)
        }
        value => value.clone(),
    }
}

fn string_field(value: &Value, key: &str, field: &str) -> Result<String, ValidationError> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| invalid(format!("{field} must be a string")))
}

fn invalid(message: impl Into<String>) -> ValidationError {
    ValidationError::new(message)
}
