use serde_json::{Map, Value};

use crate::errors::{boxed, ArtifactIntegrityError, SdkResult, ValidationError};
use crate::protocol::{
    canonical_bytes, validate_ref, validate_text, ContextSource, ContextView, HostOutcome,
    Integrity, MAX_REF_BYTES, SCHEMA_VERSION,
};

#[derive(Clone, Debug, PartialEq)]
pub struct ContextReceipt {
    session_id: String,
    task_id: String,
    plan_id: Option<String>,
    view: Option<ContextView>,
    outcome: HostOutcome,
    integrity_status: Integrity,
    degradations: Vec<String>,
    usage: Option<Value>,
    host_exception_type: Option<String>,
    host_result: Option<Value>,
}

impl ContextReceipt {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        session_id: impl AsRef<str>,
        task_id: impl AsRef<str>,
        plan_id: Option<String>,
        view: Option<ContextView>,
        outcome: HostOutcome,
        integrity_status: Integrity,
        degradations: Vec<String>,
        usage: Option<Value>,
        host_exception_type: Option<String>,
        host_result: Option<Value>,
    ) -> SdkResult<Self> {
        let session_id = validate_text(session_id.as_ref(), "session_id", MAX_REF_BYTES, true)?;
        let task_id = validate_text(task_id.as_ref(), "task_id", MAX_REF_BYTES, true)?;
        let plan_id = plan_id
            .map(|value| validate_ref(&value, "plan_id"))
            .transpose()?;
        if let Some(value) = &plan_id {
            if value.len() != 76 || !value.starts_with("plan:sha256:") {
                return Err(boxed(ValidationError::new(
                    "plan_id must be a deterministic plan reference",
                )));
            }
        }
        for value in &degradations {
            if value.is_empty() || value.chars().any(|character| character < '\u{20}') {
                return Err(boxed(ValidationError::new(
                    "degradations must be non-empty strings",
                )));
            }
        }
        if let Some(value) = &usage {
            canonical_bytes(value).map_err(boxed)?;
        }
        if let Some(value) = &host_exception_type {
            validate_text(value, "host_exception_type", MAX_REF_BYTES, true)?;
            if value.contains(':') {
                return Err(boxed(ValidationError::new(
                    "host_exception_type must be a safe type name",
                )));
            }
        }
        if matches!(integrity_status, Integrity::Sealed)
            && view.as_ref().map_or(true, |value| !value.verify())
        {
            return Err(boxed(ValidationError::new(
                "sealed receipt requires verified Engine evidence",
            )));
        }
        Ok(Self {
            session_id,
            task_id,
            plan_id,
            view,
            outcome,
            integrity_status,
            degradations,
            usage,
            host_exception_type,
            host_result,
        })
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn plan_id(&self) -> Option<&str> {
        self.plan_id.as_deref()
    }

    pub fn view(&self) -> Option<&ContextView> {
        self.view.as_ref()
    }

    pub fn outcome(&self) -> HostOutcome {
        self.outcome
    }

    pub fn integrity_status(&self) -> Integrity {
        self.integrity_status
    }

    pub fn degradations(&self) -> &[String] {
        &self.degradations
    }

    pub fn usage(&self) -> Option<&Value> {
        self.usage.as_ref()
    }

    pub fn host_exception_type(&self) -> Option<&str> {
        self.host_exception_type.as_deref()
    }

    pub fn host_result(&self) -> Option<&Value> {
        self.host_result.as_ref()
    }

    pub fn sealed(&self) -> bool {
        self.integrity_status == Integrity::Sealed
    }

    pub fn status(&self) -> Option<crate::protocol::EngineStatus> {
        self.view.as_ref().map(ContextView::status)
    }

    pub fn source(&self) -> Option<&ContextSource> {
        self.view.as_ref().map(ContextView::source)
    }

    pub fn invocation(&self) -> Option<&Value> {
        self.view.as_ref().map(ContextView::invocation)
    }

    pub fn observation(&self) -> Option<&Value> {
        self.view.as_ref().map(ContextView::observation)
    }

    pub fn receipt_link(&self) -> Option<&crate::protocol::ContextReceiptLink> {
        self.view.as_ref().and_then(ContextView::receipt_link)
    }

    pub fn recovery_ref(&self) -> Option<&str> {
        self.view.as_ref().and_then(ContextView::recovery_ref)
    }

    pub fn output_digest(&self) -> Option<&str> {
        self.view.as_ref().and_then(ContextView::output_digest)
    }

    pub fn verify(&self) -> bool {
        self.sealed() && self.view.as_ref().is_some_and(ContextView::verify)
    }

    pub fn require_verified(&self) -> SdkResult<()> {
        if self.verify() {
            Ok(())
        } else {
            Err(boxed(ArtifactIntegrityError::new(
                "receipt evidence is not sealed",
            )))
        }
    }

    pub fn to_dict(&self) -> Value {
        let mut object = Map::new();
        object.insert("schema_version".to_owned(), Value::from(SCHEMA_VERSION));
        object.insert(
            "session_id".to_owned(),
            Value::String(self.session_id.clone()),
        );
        object.insert("task_id".to_owned(), Value::String(self.task_id.clone()));
        object.insert(
            "plan_id".to_owned(),
            self.plan_id.clone().map_or(Value::Null, Value::String),
        );
        object.insert(
            "outcome".to_owned(),
            Value::String(self.outcome.to_string()),
        );
        object.insert(
            "integrity_status".to_owned(),
            Value::String(self.integrity_status.to_string()),
        );
        object.insert(
            "degradations".to_owned(),
            Value::Array(
                self.degradations
                    .iter()
                    .cloned()
                    .map(Value::String)
                    .collect(),
            ),
        );
        object.insert(
            "usage".to_owned(),
            self.usage.clone().unwrap_or(Value::Null),
        );
        object.insert(
            "host_exception_type".to_owned(),
            self.host_exception_type
                .clone()
                .map_or(Value::Null, Value::String),
        );
        object.insert(
            "status".to_owned(),
            self.status()
                .map_or(Value::Null, |value| Value::String(value.to_string())),
        );
        object.insert(
            "source".to_owned(),
            self.source()
                .and_then(|value| value.to_dict().ok())
                .unwrap_or(Value::Null),
        );
        object.insert(
            "invocation".to_owned(),
            self.invocation().cloned().unwrap_or(Value::Null),
        );
        object.insert(
            "observation".to_owned(),
            self.observation().cloned().unwrap_or(Value::Null),
        );
        object.insert(
            "receipt_link".to_owned(),
            self.receipt_link()
                .map(crate::protocol::ContextReceiptLink::to_value)
                .unwrap_or(Value::Null),
        );
        object.insert(
            "recovery_ref".to_owned(),
            self.recovery_ref()
                .map_or(Value::Null, |value| Value::String(value.to_owned())),
        );
        object.insert(
            "output_digest".to_owned(),
            self.output_digest()
                .map_or(Value::Null, |value| Value::String(value.to_owned())),
        );
        Value::Object(object)
    }
}
