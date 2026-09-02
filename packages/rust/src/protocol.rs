use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::fs;
use std::path::{Component, Path, PathBuf};

use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};

use crate::errors::{SdkResult, ValidationError};

pub(crate) const MAX_REQUEST_BYTES: usize = 64 * 1024;
pub(crate) const MAX_PATH_BYTES: usize = 4096;
pub(crate) const MAX_REF_BYTES: usize = 512;
pub(crate) const MAX_TASK_BYTES: usize = 16 * 1024;
pub(crate) const MAX_TEXT_BYTES: usize = 8 * 1024 * 1024;
pub(crate) const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const MAX_STDERR_BYTES: usize = 64 * 1024;
pub(crate) const MAX_REFS: usize = 32;
pub(crate) const MAX_MEASUREMENTS: usize = 32;
pub(crate) const SAFE_INTEGER_MAX: u64 = 9_007_199_254_740_991;

pub const SCHEMA_VERSION: u64 = 1;
pub const TRANSPORT_VERSION: u64 = 1;
pub const ENGINE_INTERFACE_VERSION: &str = "1.0.0";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FailureCode {
    PolicyRejected,
    SourceUnavailable,
    SourceIntegrityMismatch,
    ResourceLimit,
    UnsupportedOperation,
    Internal,
}

impl FailureCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::PolicyRejected => "policy_rejected",
            Self::SourceUnavailable => "source_unavailable",
            Self::SourceIntegrityMismatch => "source_integrity_mismatch",
            Self::ResourceLimit => "resource_limit",
            Self::UnsupportedOperation => "unsupported_operation",
            Self::Internal => "internal",
        }
    }
}

impl fmt::Display for FailureCode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl std::str::FromStr for FailureCode {
    type Err = ValidationError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "policy_rejected" => Ok(Self::PolicyRejected),
            "source_unavailable" => Ok(Self::SourceUnavailable),
            "source_integrity_mismatch" => Ok(Self::SourceIntegrityMismatch),
            "resource_limit" => Ok(Self::ResourceLimit),
            "unsupported_operation" => Ok(Self::UnsupportedOperation),
            "internal" => Ok(Self::Internal),
            _ => Err(ValidationError::new("invalid failure code")),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SessionState {
    Created,
    Planned,
    Executing,
    Completed,
    Aborted,
    Closed,
}

impl SessionState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Planned => "planned",
            Self::Executing => "executing",
            Self::Completed => "completed",
            Self::Aborted => "aborted",
            Self::Closed => "closed",
        }
    }
}

impl fmt::Display for SessionState {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EngineStatus {
    Succeeded,
    Degraded,
    Rejected,
    Failed,
}

impl EngineStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Succeeded => "succeeded",
            Self::Degraded => "degraded",
            Self::Rejected => "rejected",
            Self::Failed => "failed",
        }
    }
}

impl fmt::Display for EngineStatus {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl std::str::FromStr for EngineStatus {
    type Err = ValidationError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "succeeded" => Ok(Self::Succeeded),
            "degraded" => Ok(Self::Degraded),
            "rejected" => Ok(Self::Rejected),
            "failed" => Ok(Self::Failed),
            _ => Err(ValidationError::new("invalid Engine observation status")),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HostOutcome {
    Unknown,
    Accepted,
    Rejected,
    Completed,
    Failed,
    Aborted,
}

impl HostOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Accepted => "accepted",
            Self::Rejected => "rejected",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Aborted => "aborted",
        }
    }
}

impl fmt::Display for HostOutcome {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Integrity {
    Sealed,
    Unsealed,
}

impl Integrity {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Sealed => "sealed",
            Self::Unsealed => "unsealed",
        }
    }
}

impl fmt::Display for Integrity {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Freshness {
    Reuse,
    Refresh,
}

impl Freshness {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Reuse => "reuse",
            Self::Refresh => "refresh",
        }
    }
}

impl fmt::Display for Freshness {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl std::str::FromStr for Freshness {
    type Err = ValidationError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "reuse" => Ok(Self::Reuse),
            "refresh" => Ok(Self::Refresh),
            _ => Err(ValidationError::new("freshness must be reuse or refresh")),
        }
    }
}

pub(crate) fn validate_text(
    value: &str,
    field_name: &str,
    maximum: usize,
    controls: bool,
) -> Result<String, ValidationError> {
    if value.is_empty() {
        return Err(ValidationError::new(format!(
            "{field_name} must not be empty"
        )));
    }
    if value.len() > maximum {
        return Err(ValidationError::new(format!(
            "{field_name} exceeds {maximum} UTF-8 bytes"
        )));
    }
    if value.contains('\0') {
        return Err(ValidationError::new(format!("{field_name} contains NUL")));
    }
    if controls && value.chars().any(|character| character < '\u{20}') {
        return Err(ValidationError::new(format!(
            "{field_name} contains a control character"
        )));
    }
    Ok(value.to_owned())
}

pub(crate) fn validate_ref(value: &str, field_name: &str) -> Result<String, ValidationError> {
    if value.is_empty()
        || value.len() > MAX_REF_BYTES
        || !value.bytes().all(|byte| (0x20..=0x7e).contains(&byte))
    {
        return Err(ValidationError::new(format!(
            "{field_name} must be 1..{MAX_REF_BYTES} printable ASCII bytes"
        )));
    }
    Ok(value.to_owned())
}

pub(crate) fn validate_digest(value: &str, field_name: &str) -> Result<String, ValidationError> {
    let value = validate_ref(value, field_name)?;
    if value.len() != 71
        || !value.starts_with("sha256:")
        || !value[7..]
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(ValidationError::new(format!(
            "{field_name} must be sha256:<64 lowercase hex>"
        )));
    }
    Ok(value)
}

pub(crate) fn validate_output_ref(
    value: &str,
    field_name: &str,
) -> Result<String, ValidationError> {
    let value = validate_ref(value, field_name)?;
    if value.len() != 71
        || !value.starts_with("output:")
        || !value[7..]
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(ValidationError::new(format!(
            "{field_name} must be output:<64 lowercase hex>"
        )));
    }
    Ok(value)
}

pub(crate) fn normalize_path(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Prefix(prefix) => result.push(prefix.as_os_str()),
            Component::RootDir => result.push(Path::new(std::path::MAIN_SEPARATOR_STR)),
            Component::CurDir => {}
            Component::ParentDir => {
                if !result.pop() {
                    result.push(component.as_os_str());
                }
            }
            Component::Normal(value) => result.push(value),
        }
    }
    result
}

pub(crate) fn contained(candidate: &Path, root: &Path) -> bool {
    candidate == root || candidate.strip_prefix(root).is_ok()
}

fn path_string(path: &Path, field_name: &str) -> Result<String, ValidationError> {
    let value = path
        .to_str()
        .ok_or_else(|| ValidationError::new(format!("{field_name} is not valid UTF-8")))?;
    validate_text(value, field_name, MAX_PATH_BYTES, false)
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextSource {
    path: String,
    project_root: PathBuf,
    media_type: String,
    source_ref: Option<String>,
    source_digest: Option<String>,
}

impl ContextSource {
    pub fn new(path: impl AsRef<str>, project_root: impl AsRef<Path>) -> SdkResult<Self> {
        Self::with_metadata(path, project_root, "text/plain", None::<&str>, None::<&str>)
    }

    pub fn with_metadata(
        path: impl AsRef<str>,
        project_root: impl AsRef<Path>,
        media_type: impl AsRef<str>,
        source_ref: Option<impl AsRef<str>>,
        source_digest: Option<impl AsRef<str>>,
    ) -> SdkResult<Self> {
        let supplied_path = validate_text(path.as_ref(), "path", MAX_PATH_BYTES, true)?;
        let root_text = path_string(project_root.as_ref(), "project_root")?;
        let root = normalize_path(Path::new(&root_text));
        let root_text = path_string(&root, "project_root")?;
        let supplied = Path::new(&supplied_path);
        let (stored_path, absolute_path) = if supplied.is_absolute() {
            let absolute = normalize_path(supplied);
            (path_string(&absolute, "path")?, absolute)
        } else {
            let stored = normalize_path(supplied);
            let absolute = normalize_path(&root.join(supplied));
            (stored.to_string_lossy().replace('\\', "/"), absolute)
        };
        if !contained(&absolute_path, &root) {
            return Err(boxed_validation("source path escapes project_root"));
        }
        if absolute_path.as_os_str().to_string_lossy().len() > MAX_PATH_BYTES {
            return Err(boxed_validation("path exceeds the path bound"));
        }
        let media_type = validate_text(media_type.as_ref(), "media_type", MAX_REF_BYTES, true)?;
        let source_ref = source_ref
            .map(|value| validate_ref(value.as_ref(), "source_ref"))
            .transpose()?;
        let source_digest = source_digest
            .map(|value| validate_digest(value.as_ref(), "source_digest"))
            .transpose()?;
        Ok(Self {
            path: stored_path,
            project_root: PathBuf::from(root_text),
            media_type,
            source_ref,
            source_digest,
        })
    }

    pub fn path(&self) -> &str {
        &self.path
    }

    pub fn project_root(&self) -> &Path {
        &self.project_root
    }

    pub fn media_type(&self) -> &str {
        &self.media_type
    }

    pub fn source_ref(&self) -> Option<&str> {
        self.source_ref.as_deref()
    }

    pub fn source_digest(&self) -> Option<&str> {
        self.source_digest.as_deref()
    }

    pub fn relative_path(&self) -> SdkResult<String> {
        let absolute = normalize_path(&self.project_root.join(&self.path));
        if !contained(&absolute, &self.project_root) {
            return Err(boxed_validation("source containment cannot be proven"));
        }
        let relative = absolute
            .strip_prefix(&self.project_root)
            .map_err(|_| boxed_validation("source containment cannot be proven"))?;
        let value = relative.to_string_lossy().replace('\\', "/");
        if value.is_empty()
            || value == "."
            || value == ".."
            || value.starts_with("../")
            || value.chars().any(|character| character < '\u{20}')
        {
            return Err(boxed_validation(
                "source path must be a rooted relative file path",
            ));
        }
        Ok(value)
    }

    pub fn descriptor(&self) -> SdkResult<Value> {
        let mut object = Map::new();
        object.insert("path".to_owned(), Value::String(self.relative_path()?));
        object.insert(
            "media_type".to_owned(),
            Value::String(self.media_type.clone()),
        );
        if let Some(value) = &self.source_ref {
            object.insert("source_ref".to_owned(), Value::String(value.clone()));
        }
        if let Some(value) = &self.source_digest {
            object.insert("source_digest".to_owned(), Value::String(value.clone()));
        }
        Ok(Value::Object(object))
    }

    pub fn to_dict(&self) -> SdkResult<Value> {
        let mut object = as_object(self.descriptor()?)?;
        object.insert(
            "project_root".to_owned(),
            Value::String(self.project_root.to_string_lossy().into_owned()),
        );
        Ok(Value::Object(object))
    }
}

fn boxed_validation(message: impl Into<String>) -> Box<dyn std::error::Error + Send + Sync> {
    Box::new(ValidationError::new(message))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextPlan {
    session_id: String,
    task_id: String,
    task: String,
    source: ContextSource,
    mode: String,
    freshness: String,
    plan_id: String,
}

impl ContextPlan {
    pub fn new(
        session_id: impl AsRef<str>,
        task_id: impl AsRef<str>,
        task: impl AsRef<str>,
        source: ContextSource,
    ) -> SdkResult<Self> {
        Self::with_options(
            session_id,
            task_id,
            task,
            source,
            "aggressive",
            Freshness::Reuse,
        )
    }

    pub fn with_options(
        session_id: impl AsRef<str>,
        task_id: impl AsRef<str>,
        task: impl AsRef<str>,
        source: ContextSource,
        mode: impl AsRef<str>,
        freshness: Freshness,
    ) -> SdkResult<Self> {
        let session_id = validate_text(session_id.as_ref(), "session_id", MAX_REF_BYTES, true)?;
        let task_id = validate_text(task_id.as_ref(), "task_id", MAX_REF_BYTES, true)?;
        let task = validate_text(task.as_ref(), "task", MAX_TASK_BYTES, false)?;
        let mode = validate_text(mode.as_ref(), "mode", MAX_REF_BYTES, true)?;
        if mode != "aggressive" {
            return Err(boxed_validation(
                "mode must be aggressive in Engine Interface v1",
            ));
        }
        let freshness = freshness.as_str().to_owned();
        let mut plan = Self {
            session_id,
            task_id,
            task,
            source,
            mode,
            freshness,
            plan_id: String::new(),
        };
        let digest = sha256_hex(&canonical_bytes(&plan.to_intent()?)?);
        plan.plan_id = format!("plan:sha256:{digest}");
        Ok(plan)
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn task(&self) -> &str {
        &self.task
    }

    pub fn source(&self) -> &ContextSource {
        &self.source
    }

    pub fn mode(&self) -> &str {
        &self.mode
    }

    pub fn freshness(&self) -> &str {
        &self.freshness
    }

    pub fn plan_id(&self) -> &str {
        &self.plan_id
    }

    pub fn to_intent(&self) -> SdkResult<Value> {
        let mut object = Map::new();
        object.insert("intent_version".to_owned(), Value::from(1_u64));
        object.insert(
            "session_id".to_owned(),
            Value::String(self.session_id.clone()),
        );
        object.insert("task_id".to_owned(), Value::String(self.task_id.clone()));
        object.insert("task".to_owned(), Value::String(self.task.clone()));
        object.insert("source".to_owned(), self.source.descriptor()?);
        object.insert("mode".to_owned(), Value::String(self.mode.clone()));
        object.insert(
            "freshness".to_owned(),
            Value::String(self.freshness.clone()),
        );
        Ok(Value::Object(object))
    }

    pub fn to_dict(&self) -> SdkResult<Value> {
        let mut object = as_object(self.to_intent()?)?;
        object.insert("plan_id".to_owned(), Value::String(self.plan_id.clone()));
        Ok(Value::Object(object))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextMeasurement {
    name: String,
    unit: String,
    classification: String,
    value: Option<u64>,
}

impl ContextMeasurement {
    pub fn new(
        name: impl AsRef<str>,
        unit: impl AsRef<str>,
        classification: impl AsRef<str>,
        value: Option<u64>,
    ) -> SdkResult<Self> {
        let name = name.as_ref();
        let unit = unit.as_ref();
        let classification = classification.as_ref();
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte == b'_' || byte.is_ascii_digit())
        {
            return Err(boxed_validation("measurement name must be lowercase ASCII"));
        }
        if unit.is_empty()
            || !unit
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte == b'_' || byte.is_ascii_digit())
        {
            return Err(boxed_validation("measurement unit must be lowercase ASCII"));
        }
        if value.is_some_and(|value| value > SAFE_INTEGER_MAX) {
            return Err(boxed_validation(
                "measurement value must be a safe non-negative integer",
            ));
        }
        match classification {
            "unavailable" if value.is_some() => {
                return Err(boxed_validation(
                    "unavailable measurement value must be null",
                ));
            }
            "measured" | "estimated" if value.is_none() => {
                return Err(boxed_validation(
                    "measurement value must be a non-negative integer",
                ));
            }
            "measured" | "estimated" | "unavailable" => {}
            _ => return Err(boxed_validation("invalid measurement classification")),
        }
        Ok(Self {
            name: name.to_owned(),
            unit: unit.to_owned(),
            classification: classification.to_owned(),
            value,
        })
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn unit(&self) -> &str {
        &self.unit
    }

    pub fn classification(&self) -> &str {
        &self.classification
    }

    pub fn value(&self) -> Option<u64> {
        self.value
    }

    pub(crate) fn to_value(&self) -> Value {
        let mut object = Map::new();
        object.insert("name".to_owned(), Value::String(self.name.clone()));
        object.insert("unit".to_owned(), Value::String(self.unit.clone()));
        object.insert(
            "classification".to_owned(),
            Value::String(self.classification.clone()),
        );
        object.insert(
            "value".to_owned(),
            self.value.map_or(Value::Null, Value::from),
        );
        Value::Object(object)
    }

    pub fn to_dict(&self) -> Value {
        self.to_value()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextFailure {
    code: FailureCode,
    retryable_by_host: bool,
    recovery_ref: Option<String>,
}

impl ContextFailure {
    pub fn new(
        code: FailureCode,
        retryable_by_host: bool,
        recovery_ref: Option<impl AsRef<str>>,
    ) -> SdkResult<Self> {
        let recovery_ref = recovery_ref
            .map(|value| validate_ref(value.as_ref(), "recovery_ref"))
            .transpose()?;
        Ok(Self {
            code,
            retryable_by_host,
            recovery_ref,
        })
    }

    pub fn code(&self) -> FailureCode {
        self.code
    }

    pub fn retryable_by_host(&self) -> bool {
        self.retryable_by_host
    }

    pub fn recovery_ref(&self) -> Option<&str> {
        self.recovery_ref.as_deref()
    }

    pub(crate) fn to_value(&self) -> Value {
        let mut object = Map::new();
        object.insert(
            "code".to_owned(),
            Value::String(self.code.as_str().to_owned()),
        );
        object.insert(
            "retryable_by_host".to_owned(),
            Value::Bool(self.retryable_by_host),
        );
        object.insert(
            "recovery_ref".to_owned(),
            self.recovery_ref
                .as_ref()
                .map_or(Value::Null, |value| Value::String(value.clone())),
        );
        Value::Object(object)
    }

    pub fn to_dict(&self) -> Value {
        self.to_value()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextReceiptLink {
    schema_version: u64,
    receipt_id: String,
    receipt_ref: String,
    receipt_digest: String,
    invocation_id: String,
}

impl ContextReceiptLink {
    pub fn new(
        schema_version: u64,
        receipt_id: impl AsRef<str>,
        receipt_ref: impl AsRef<str>,
        receipt_digest: impl AsRef<str>,
        invocation_id: impl AsRef<str>,
    ) -> SdkResult<Self> {
        if schema_version != SCHEMA_VERSION {
            return Err(boxed_validation("receipt link schema_version must be 1"));
        }
        let receipt_id = validate_ref(receipt_id.as_ref(), "receipt_id")?;
        let receipt_ref = validate_ref(receipt_ref.as_ref(), "receipt_ref")?;
        let receipt_digest = validate_digest(receipt_digest.as_ref(), "receipt_digest")?;
        let invocation_id =
            validate_text(invocation_id.as_ref(), "invocation_id", MAX_REF_BYTES, true)?;
        if receipt_ref != format!("receipt:{receipt_digest}") {
            return Err(boxed_validation(
                "receipt_ref does not match receipt_digest",
            ));
        }
        Ok(Self {
            schema_version,
            receipt_id,
            receipt_ref,
            receipt_digest,
            invocation_id,
        })
    }

    pub fn schema_version(&self) -> u64 {
        self.schema_version
    }

    pub fn receipt_id(&self) -> &str {
        &self.receipt_id
    }

    pub fn receipt_ref(&self) -> &str {
        &self.receipt_ref
    }

    pub fn receipt_digest(&self) -> &str {
        &self.receipt_digest
    }

    pub fn invocation_id(&self) -> &str {
        &self.invocation_id
    }

    pub(crate) fn to_value(&self) -> Value {
        let mut object = Map::new();
        object.insert(
            "schema_version".to_owned(),
            Value::from(self.schema_version),
        );
        object.insert(
            "receipt_id".to_owned(),
            Value::String(self.receipt_id.clone()),
        );
        object.insert(
            "receipt_ref".to_owned(),
            Value::String(self.receipt_ref.clone()),
        );
        object.insert(
            "receipt_digest".to_owned(),
            Value::String(self.receipt_digest.clone()),
        );
        object.insert(
            "invocation_id".to_owned(),
            Value::String(self.invocation_id.clone()),
        );
        Value::Object(object)
    }

    pub fn to_dict(&self) -> Value {
        self.to_value()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RecoveredSource {
    text: String,
    source_ref: String,
    source_digest: String,
    recovery_ref: String,
}

impl RecoveredSource {
    pub fn new(
        text: impl AsRef<str>,
        source_ref: impl AsRef<str>,
        source_digest: impl AsRef<str>,
        recovery_ref: impl AsRef<str>,
    ) -> SdkResult<Self> {
        let text = validate_text(text.as_ref(), "recovered text", MAX_TEXT_BYTES, false)?;
        let source_ref = validate_ref(source_ref.as_ref(), "source_ref")?;
        let source_digest = validate_digest(source_digest.as_ref(), "source_digest")?;
        let recovery_ref = validate_ref(recovery_ref.as_ref(), "recovery_ref")?;
        if sha256_digest(text.as_bytes()) != source_digest {
            return Err(boxed_validation(
                "recovered text digest does not match source_digest",
            ));
        }
        Ok(Self {
            text,
            source_ref,
            source_digest,
            recovery_ref,
        })
    }

    pub fn text(&self) -> &str {
        &self.text
    }

    pub fn source_ref(&self) -> &str {
        &self.source_ref
    }

    pub fn source_digest(&self) -> &str {
        &self.source_digest
    }

    pub fn recovery_ref(&self) -> &str {
        &self.recovery_ref
    }

    pub(crate) fn to_value(&self) -> Value {
        let mut object = Map::new();
        object.insert("text".to_owned(), Value::String(self.text.clone()));
        object.insert(
            "source_ref".to_owned(),
            Value::String(self.source_ref.clone()),
        );
        object.insert(
            "source_digest".to_owned(),
            Value::String(self.source_digest.clone()),
        );
        object.insert(
            "recovery_ref".to_owned(),
            Value::String(self.recovery_ref.clone()),
        );
        Value::Object(object)
    }

    pub fn to_dict(&self) -> Value {
        self.to_value()
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ContextView {
    source: ContextSource,
    text: Option<String>,
    output_ref: Option<String>,
    output_digest: Option<String>,
    source_ref: String,
    source_digest: String,
    recovery_ref: Option<String>,
    status: EngineStatus,
    measurements: Vec<ContextMeasurement>,
    failure: Option<ContextFailure>,
    receipt_link: Option<ContextReceiptLink>,
    invocation: Value,
    observation: Value,
    schema_version: u64,
    transport_version: u64,
    engine_interface_version: String,
}

impl ContextView {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        source: ContextSource,
        text: Option<String>,
        output_ref: Option<String>,
        output_digest: Option<String>,
        source_ref: String,
        source_digest: String,
        recovery_ref: Option<String>,
        status: EngineStatus,
        measurements: Vec<ContextMeasurement>,
        failure: Option<ContextFailure>,
        receipt_link: Option<ContextReceiptLink>,
        invocation: Value,
        observation: Value,
    ) -> SdkResult<Self> {
        Self::with_versions(
            source,
            text,
            output_ref,
            output_digest,
            source_ref,
            source_digest,
            recovery_ref,
            status,
            measurements,
            failure,
            receipt_link,
            invocation,
            observation,
            SCHEMA_VERSION,
            TRANSPORT_VERSION,
            ENGINE_INTERFACE_VERSION,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn with_versions(
        source: ContextSource,
        text: Option<String>,
        output_ref: Option<String>,
        output_digest: Option<String>,
        source_ref: String,
        source_digest: String,
        recovery_ref: Option<String>,
        status: EngineStatus,
        measurements: Vec<ContextMeasurement>,
        failure: Option<ContextFailure>,
        receipt_link: Option<ContextReceiptLink>,
        invocation: Value,
        observation: Value,
        schema_version: u64,
        transport_version: u64,
        engine_interface_version: &str,
    ) -> SdkResult<Self> {
        if let Some(value) = &text {
            validate_text(value, "view text", MAX_TEXT_BYTES, false)?;
        }
        if let Some(value) = &output_ref {
            validate_output_ref(value, "output_ref")?;
        }
        if let Some(value) = &output_digest {
            validate_digest(value, "output_digest")?;
        }
        if output_ref.is_some() != output_digest.is_some() {
            return Err(boxed_validation(
                "output_ref and output_digest must be paired",
            ));
        }
        if let (Some(digest), Some(value)) = (&output_digest, &text) {
            if sha256_digest(value.as_bytes()) != *digest {
                return Err(boxed_validation("view output digest mismatch"));
            }
            if output_ref.as_deref() != Some(&format!("output:{}", &digest[7..])) {
                return Err(boxed_validation("view output reference mismatch"));
            }
        }
        let source_ref = validate_ref(&source_ref, "source_ref")?;
        let source_digest = validate_digest(&source_digest, "source_digest")?;
        let recovery_ref = recovery_ref
            .as_deref()
            .map(|value| validate_ref(value, "recovery_ref"))
            .transpose()?;
        if measurements.len() > MAX_MEASUREMENTS {
            return Err(boxed_validation("too many measurements"));
        }
        if !is_object(&invocation) || !is_object(&observation) {
            return Err(boxed_validation(
                "invocation and observation must be objects",
            ));
        }
        if schema_version != SCHEMA_VERSION {
            return Err(boxed_validation("view schema_version must be 1"));
        }
        if transport_version != TRANSPORT_VERSION {
            return Err(boxed_validation("view transport_version must be integer 1"));
        }
        if engine_interface_version != ENGINE_INTERFACE_VERSION {
            return Err(boxed_validation("unsupported Engine Interface version"));
        }
        Ok(Self {
            source,
            text,
            output_ref,
            output_digest,
            source_ref,
            source_digest,
            recovery_ref,
            status,
            measurements,
            failure,
            receipt_link,
            invocation,
            observation,
            schema_version,
            transport_version,
            engine_interface_version: engine_interface_version.to_owned(),
        })
    }

    pub fn source(&self) -> &ContextSource {
        &self.source
    }

    pub fn text(&self) -> Option<&str> {
        self.text.as_deref()
    }

    pub fn output_ref(&self) -> Option<&str> {
        self.output_ref.as_deref()
    }

    pub fn output_digest(&self) -> Option<&str> {
        self.output_digest.as_deref()
    }

    pub fn source_ref(&self) -> &str {
        &self.source_ref
    }

    pub fn source_digest(&self) -> &str {
        &self.source_digest
    }

    pub fn recovery_ref(&self) -> Option<&str> {
        self.recovery_ref.as_deref()
    }

    pub fn status(&self) -> EngineStatus {
        self.status
    }

    pub fn measurements(&self) -> &[ContextMeasurement] {
        &self.measurements
    }

    pub fn failure(&self) -> Option<&ContextFailure> {
        self.failure.as_ref()
    }

    pub fn receipt_link(&self) -> Option<&ContextReceiptLink> {
        self.receipt_link.as_ref()
    }

    pub fn invocation(&self) -> &Value {
        &self.invocation
    }

    pub fn observation(&self) -> &Value {
        &self.observation
    }

    pub fn schema_version(&self) -> u64 {
        self.schema_version
    }

    pub fn transport_version(&self) -> u64 {
        self.transport_version
    }

    pub fn engine_interface_version(&self) -> &str {
        &self.engine_interface_version
    }

    pub fn integrity_status(&self) -> Integrity {
        if self.verify() {
            Integrity::Sealed
        } else {
            Integrity::Unsealed
        }
    }

    pub fn input_ref(&self) -> Option<&str> {
        self.invocation
            .as_object()
            .and_then(|value| value.get("input_ref"))
            .and_then(Value::as_str)
    }

    pub fn invocation_id(&self) -> Option<&str> {
        self.invocation
            .as_object()
            .and_then(|value| value.get("invocation_id"))
            .and_then(Value::as_str)
    }

    pub fn engine_version(&self) -> Option<&str> {
        self.invocation
            .as_object()
            .and_then(|value| value.get("engine"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("engine_version"))
            .and_then(Value::as_str)
    }

    pub fn capability_version(&self) -> Option<&str> {
        self.invocation
            .as_object()
            .and_then(|value| value.get("operation"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("capability_version"))
            .and_then(Value::as_str)
    }

    pub fn require_text(&self) -> SdkResult<&str> {
        self.text.as_deref().ok_or_else(|| {
            crate::errors::boxed(crate::errors::EngineExecutionError::new(
                "Engine view has no text",
            ))
        })
    }

    pub fn recovery_binding(&self) -> SdkResult<Value> {
        let recovery_ref = self
            .recovery_ref
            .clone()
            .ok_or_else(|| boxed_validation("view has no recovery binding"))?;
        let mut object = Map::new();
        object.insert("recovery_ref".to_owned(), Value::String(recovery_ref));
        object.insert(
            "source_ref".to_owned(),
            Value::String(self.source_ref.clone()),
        );
        object.insert(
            "source_digest".to_owned(),
            Value::String(self.source_digest.clone()),
        );
        Ok(Value::Object(object))
    }

    pub fn verify(&self) -> bool {
        if !matches!(
            self.status,
            EngineStatus::Succeeded | EngineStatus::Degraded
        ) {
            return false;
        }
        let (Some(recovery_ref), Some(output_ref), Some(output_digest), Some(text)) = (
            self.recovery_ref.as_deref(),
            self.output_ref.as_deref(),
            self.output_digest.as_deref(),
            self.text.as_deref(),
        ) else {
            return false;
        };
        if sha256_digest(text.as_bytes()) != output_digest
            || output_ref != format!("output:{}", &output_digest[7..])
        {
            return false;
        }
        let Some(invocation) = self.invocation.as_object() else {
            return false;
        };
        let Some(source_refs) = invocation.get("source_refs").and_then(Value::as_array) else {
            return false;
        };
        if !source_refs
            .iter()
            .any(|value| value.as_str() == Some(self.source_ref.as_str()))
        {
            return false;
        }
        let Some(observation) = self.observation.as_object() else {
            return false;
        };
        if observation.get("invocation_id") != invocation.get("invocation_id")
            || observation.get("output_ref").and_then(Value::as_str) != Some(output_ref)
            || observation.get("output_digest").and_then(Value::as_str) != Some(output_digest)
        {
            return false;
        }
        self.receipt_link
            .as_ref()
            .is_some_and(|link| Some(link.invocation_id()) == self.invocation_id())
            && !recovery_ref.is_empty()
    }

    pub fn to_dict(&self) -> Value {
        let mut object = Map::new();
        object.insert(
            "schema_version".to_owned(),
            Value::from(self.schema_version),
        );
        object.insert(
            "transport_version".to_owned(),
            Value::from(self.transport_version),
        );
        object.insert(
            "engine_interface_version".to_owned(),
            Value::String(self.engine_interface_version.clone()),
        );
        object.insert(
            "source".to_owned(),
            self.source.to_dict().unwrap_or(Value::Null),
        );
        object.insert(
            "text".to_owned(),
            self.text.clone().map_or(Value::Null, Value::String),
        );
        object.insert(
            "output_ref".to_owned(),
            self.output_ref.clone().map_or(Value::Null, Value::String),
        );
        object.insert(
            "output_digest".to_owned(),
            self.output_digest
                .clone()
                .map_or(Value::Null, Value::String),
        );
        object.insert(
            "source_ref".to_owned(),
            Value::String(self.source_ref.clone()),
        );
        object.insert(
            "source_digest".to_owned(),
            Value::String(self.source_digest.clone()),
        );
        object.insert(
            "recovery_ref".to_owned(),
            self.recovery_ref.clone().map_or(Value::Null, Value::String),
        );
        object.insert("status".to_owned(), Value::String(self.status.to_string()));
        object.insert(
            "measurements".to_owned(),
            Value::Array(
                self.measurements
                    .iter()
                    .map(ContextMeasurement::to_value)
                    .collect(),
            ),
        );
        object.insert(
            "failure".to_owned(),
            self.failure
                .as_ref()
                .map_or(Value::Null, ContextFailure::to_value),
        );
        object.insert(
            "receipt_link".to_owned(),
            self.receipt_link
                .as_ref()
                .map_or(Value::Null, ContextReceiptLink::to_value),
        );
        object.insert("invocation".to_owned(), self.invocation.clone());
        object.insert("observation".to_owned(), self.observation.clone());
        Value::Object(object)
    }
}

fn is_object(value: &Value) -> bool {
    matches!(value, Value::Object(_))
}

fn as_object(value: Value) -> Result<Map<String, Value>, Box<dyn std::error::Error + Send + Sync>> {
    match value {
        Value::Object(value) => Ok(value),
        _ => Err(boxed_validation("value must be a JSON object")),
    }
}

pub(crate) fn sha256_digest(data: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(data))
}

pub(crate) fn sha256_hex(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

fn canonical_value(value: &Value) -> Result<Value, ValidationError> {
    match value {
        Value::Null | Value::Bool(_) | Value::String(_) => Ok(value.clone()),
        Value::Number(number) => {
            if number.is_i64() {
                let value = number.as_i64().unwrap_or_default();
                if value.unsigned_abs() > SAFE_INTEGER_MAX {
                    return Err(ValidationError::new(
                        "canonical JSON numbers must be safe integers",
                    ));
                }
                Ok(Value::Number(Number::from(value)))
            } else if number.is_u64() {
                let value = number.as_u64().unwrap_or_default();
                if value > SAFE_INTEGER_MAX {
                    return Err(ValidationError::new(
                        "canonical JSON numbers must be safe integers",
                    ));
                }
                Ok(Value::Number(Number::from(value)))
            } else {
                Err(ValidationError::new(
                    "canonical JSON numbers must be safe integers",
                ))
            }
        }
        Value::Array(values) => values
            .iter()
            .map(canonical_value)
            .collect::<Result<Vec<_>, _>>()
            .map(Value::Array),
        Value::Object(values) => {
            let mut sorted = BTreeMap::new();
            for (key, value) in values {
                sorted.insert(key, canonical_value(value)?);
            }
            let mut object = Map::new();
            for (key, value) in sorted {
                object.insert(key.clone(), value);
            }
            Ok(Value::Object(object))
        }
    }
}

pub(crate) fn canonical_json(value: &Value) -> Result<String, ValidationError> {
    let normalized = canonical_value(value)?;
    serde_json::to_string(&normalized)
        .map_err(|_| ValidationError::new("value is not canonical JSON data"))
}

pub(crate) fn canonical_bytes(value: &Value) -> Result<Vec<u8>, ValidationError> {
    Ok(canonical_json(value)?.into_bytes())
}

struct StrictValue(Value);

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct StrictVisitor;

        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = Value;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("strict JSON value")
            }

            fn visit_unit<E>(self) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Value::Null)
            }

            fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Value::Bool(value))
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Value::Number(Number::from(value)))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Value::Number(Number::from(value)))
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Number::from_f64(value)
                    .map(Value::Number)
                    .ok_or_else(|| E::custom("non-finite JSON number"))
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Value::String(value.to_owned()))
            }

            fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Value::String(value))
            }

            fn visit_seq<A>(self, mut access: A) -> Result<Self::Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut values = Vec::new();
                while let Some(value) = access.next_element::<StrictValue>()? {
                    values.push(value.0);
                }
                Ok(Value::Array(values))
            }

            fn visit_map<A>(self, mut access: A) -> Result<Self::Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut values = Map::new();
                let mut keys = BTreeSet::new();
                while let Some(key) = access.next_key::<String>()? {
                    if !keys.insert(key.clone()) {
                        return Err(serde::de::Error::custom(format!("duplicate key: {key}")));
                    }
                    let value = access.next_value::<StrictValue>()?;
                    values.insert(key, value.0);
                }
                Ok(Value::Object(values))
            }
        }

        deserializer.deserialize_any(StrictVisitor).map(StrictValue)
    }
}

fn reject_negative_zero(input: &[u8]) -> Result<(), ValidationError> {
    let mut index = 0;
    let mut in_string = false;
    let mut escaped = false;
    while index < input.len() {
        let byte = input[index];
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
            index += 1;
            continue;
        }
        if byte == b'"' {
            in_string = true;
            index += 1;
            continue;
        }
        if byte == b'-' && input.get(index + 1) == Some(&b'0') {
            let start = index;
            index += 2;
            while index < input.len()
                && !matches!(
                    input[index],
                    b',' | b']' | b'}' | b' ' | b'\t' | b'\r' | b'\n'
                )
            {
                index += 1;
            }
            if let Ok(token) = std::str::from_utf8(&input[start..index]) {
                if token
                    .parse::<f64>()
                    .is_ok_and(|value| value == 0.0 && value.is_sign_negative())
                {
                    return Err(ValidationError::new(
                        "canonical JSON numbers exclude negative zero",
                    ));
                }
            }
            continue;
        }
        index += 1;
    }
    Ok(())
}

pub(crate) fn strict_json_loads(input: &[u8], label: &str) -> Result<Value, ValidationError> {
    reject_negative_zero(input)?;
    let mut deserializer = serde_json::Deserializer::from_slice(input);
    let value = StrictValue::deserialize(&mut deserializer)
        .map_err(|_| ValidationError::new(format!("invalid {label}")))?
        .0;
    deserializer
        .end()
        .map_err(|_| ValidationError::new(format!("invalid {label}")))?;
    if !is_object(&value) {
        return Err(ValidationError::new(format!(
            "{label} must be a JSON object"
        )));
    }
    Ok(value)
}

pub(crate) fn exact_keys(
    value: &Value,
    expected: &[&str],
    label: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let Some(object) = value.as_object() else {
        return Err(boxed_validation(format!(
            "{label} fields do not match the v1 contract"
        )));
    };
    let expected: BTreeSet<&str> = expected.iter().copied().collect();
    let actual: BTreeSet<&str> = object.keys().map(String::as_str).collect();
    if actual != expected {
        return Err(boxed_validation(format!(
            "{label} fields do not match the v1 contract"
        )));
    }
    Ok(())
}

pub(crate) fn json_string(
    value: &Value,
    field_name: &str,
    maximum: usize,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let value = value
        .as_str()
        .ok_or_else(|| boxed_validation(format!("{field_name} must be a string")))?;
    if value.len() > maximum || value.contains('\0') {
        return Err(boxed_validation(format!("{field_name} exceeds its bound")));
    }
    Ok(value.to_owned())
}

pub(crate) fn json_integer(
    value: &Value,
    field_name: &str,
) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
    let value = value
        .as_u64()
        .filter(|value| *value <= SAFE_INTEGER_MAX)
        .ok_or_else(|| boxed_validation(format!("{field_name} must be a safe integer")))?;
    Ok(value)
}

pub(crate) fn existing_directory(path: &Path) -> SdkResult<PathBuf> {
    let canonical =
        fs::canonicalize(path).map_err(|_| boxed_validation("project_root is unavailable"))?;
    if !canonical.is_dir() {
        return Err(boxed_validation("project_root is unavailable"));
    }
    if canonical.as_os_str().to_string_lossy().len() > MAX_PATH_BYTES {
        return Err(boxed_validation("project_root exceeds the path bound"));
    }
    Ok(canonical)
}
