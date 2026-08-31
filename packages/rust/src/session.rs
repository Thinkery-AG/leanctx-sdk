use std::error::Error;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::Value;

use crate::engine::{EngineClient, SubprocessEngineClient};
use crate::errors::{
    boxed, ArtifactIntegrityError, EngineExecutionError, EngineRejected, EngineTimeout,
    EngineUnavailable, RecoveryUnavailableError, SdkResult, SessionStateError, ValidationError,
};
use crate::protocol::{
    canonical_bytes, validate_text, ContextPlan, ContextSource, ContextView, HostOutcome,
    Integrity, RecoveredSource, SessionState, MAX_REF_BYTES, MAX_TASK_BYTES,
};
use crate::receipt::ContextReceipt;

/// Product lifecycle owned by the host around one immutable intent.
pub struct ContextSession {
    task: String,
    project_root: Option<PathBuf>,
    session_id: String,
    task_id: String,
    fail_open: bool,
    engine: Box<dyn EngineClient>,
    state: SessionState,
    plan: Option<ContextPlan>,
    view: Option<ContextView>,
    receipt: Option<ContextReceipt>,
    prepared: bool,
    degradations: Vec<String>,
}

impl std::fmt::Debug for ContextSession {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ContextSession")
            .field("task", &self.task)
            .field("project_root", &self.project_root)
            .field("session_id", &self.session_id)
            .field("task_id", &self.task_id)
            .field("fail_open", &self.fail_open)
            .field("state", &self.state)
            .field("plan", &self.plan)
            .field("view", &self.view)
            .field("receipt", &self.receipt)
            .field("prepared", &self.prepared)
            .field("degradations", &self.degradations)
            .finish()
    }
}

impl ContextSession {
    pub fn new(task: impl AsRef<str>) -> SdkResult<Self> {
        let engine = SubprocessEngineClient::default_client()?;
        Self::new_with_configuration(task, None, None, None, false, Box::new(engine))
    }

    pub fn with_engine(task: impl AsRef<str>, engine: Box<dyn EngineClient>) -> SdkResult<Self> {
        Self::new_with_configuration(task, None, None, None, false, engine)
    }

    pub fn new_with_engine(
        task: impl AsRef<str>,
        project_root: Option<PathBuf>,
        engine: impl EngineClient + 'static,
    ) -> SdkResult<Self> {
        Self::new_with_configuration(task, project_root, None, None, false, Box::new(engine))
    }

    pub fn new_with_configuration(
        task: impl AsRef<str>,
        project_root: Option<PathBuf>,
        session_id: Option<String>,
        task_id: Option<String>,
        fail_open: bool,
        engine: Box<dyn EngineClient>,
    ) -> SdkResult<Self> {
        let task = validate_text(task.as_ref(), "task", MAX_TASK_BYTES, false)?;
        if let Some(root) = &project_root {
            if root.as_os_str().to_str().is_none() {
                return Err(boxed(ValidationError::new(
                    "project_root is not valid UTF-8",
                )));
            }
        }
        let session_id = session_id.unwrap_or_else(|| runtime_id("session"));
        let task_id = task_id.unwrap_or_else(|| runtime_id("task"));
        validate_text(&session_id, "session_id", MAX_REF_BYTES, true)?;
        validate_text(&task_id, "task_id", MAX_REF_BYTES, true)?;
        Ok(Self {
            task,
            project_root,
            session_id,
            task_id,
            fail_open,
            engine,
            state: SessionState::Created,
            plan: None,
            view: None,
            receipt: None,
            prepared: false,
            degradations: Vec::new(),
        })
    }

    pub fn state(&self) -> SessionState {
        self.state
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

    pub fn project_root(&self) -> Option<&Path> {
        self.project_root.as_deref()
    }

    pub fn current_plan(&self) -> Option<&ContextPlan> {
        self.plan.as_ref()
    }

    pub fn view(&self) -> Option<&ContextView> {
        self.view.as_ref()
    }

    pub fn receipt(&self) -> Option<&ContextReceipt> {
        self.receipt.as_ref()
    }

    pub fn degradations(&self) -> &[String] {
        &self.degradations
    }

    pub fn plan_intent(
        &mut self,
        source: ContextSource,
        mode: impl AsRef<str>,
        freshness: crate::protocol::Freshness,
    ) -> SdkResult<&ContextPlan> {
        self.ensure_not_terminal()?;
        let candidate = ContextPlan::with_options(
            &self.session_id,
            &self.task_id,
            &self.task,
            source,
            mode,
            freshness,
        )?;
        if self.plan.is_some() {
            if self
                .plan
                .as_ref()
                .is_some_and(|existing| existing.plan_id() != candidate.plan_id())
            {
                return Err(boxed(SessionStateError::new(
                    "a session cannot replace its Product intent",
                )));
            }
            return self
                .plan
                .as_ref()
                .ok_or_else(|| boxed(SessionStateError::new("session plan disappeared")));
        }
        self.plan = Some(candidate);
        self.state = SessionState::Planned;
        Ok(self.plan.as_ref().expect("plan inserted"))
    }

    pub fn plan_for(
        &mut self,
        source: ContextSource,
        mode: impl AsRef<str>,
        freshness: crate::protocol::Freshness,
    ) -> SdkResult<&ContextPlan> {
        self.plan_intent(source, mode, freshness)
    }

    pub fn prepare(&mut self, source: &ContextSource) -> SdkResult<Option<&ContextView>> {
        if matches!(
            self.state,
            SessionState::Completed | SessionState::Aborted | SessionState::Closed
        ) {
            return Err(boxed(SessionStateError::new(
                "prepare is not legal after terminal completion",
            )));
        }
        if self.prepared {
            return Ok(self.view.as_ref());
        }
        if self.plan.is_none() {
            self.plan_intent(
                source.clone(),
                "aggressive",
                crate::protocol::Freshness::Reuse,
            )?;
        } else if self
            .plan
            .as_ref()
            .is_some_and(|plan| plan.source() != source)
        {
            return Err(boxed(SessionStateError::new(
                "a session cannot replace its Product intent",
            )));
        }
        let plan = self
            .plan
            .as_ref()
            .ok_or_else(|| boxed(SessionStateError::new("prepare could not establish a plan")))?;
        self.state = SessionState::Executing;
        match self.engine.context_view(plan) {
            Ok(view) => {
                if view.status() == crate::protocol::EngineStatus::Degraded {
                    self.add_degradation("engine:degraded");
                }
                self.view = Some(view);
                self.prepared = true;
                Ok(self.view.as_ref())
            }
            Err(error) if self.fail_open && is_fail_open_error(error.as_ref()) => {
                self.add_degradation(format!(
                    "engine:{}",
                    error_code(error.as_ref()).unwrap_or("engine_error")
                ));
                self.prepared = true;
                self.view = None;
                Ok(None)
            }
            Err(error) => {
                self.abort_engine_failure(error.as_ref());
                Err(error)
            }
        }
    }

    pub fn complete(
        &mut self,
        outcome: HostOutcome,
        host_result: Option<Value>,
        usage: Option<Value>,
    ) -> SdkResult<&ContextReceipt> {
        if self.state == SessionState::Completed {
            let receipt = self
                .receipt
                .as_ref()
                .ok_or_else(|| boxed(SessionStateError::new("completed session has no receipt")))?;
            if receipt.outcome() != outcome || !same_json(receipt.usage(), usage.as_ref()) {
                return Err(boxed(SessionStateError::new(
                    "conflicting repeated complete",
                )));
            }
            return Ok(receipt);
        }
        if matches!(self.state, SessionState::Aborted | SessionState::Closed) {
            return Err(boxed(SessionStateError::new(
                "complete is not legal after abort/close",
            )));
        }
        if self.state != SessionState::Executing {
            return Err(boxed(SessionStateError::new(
                "complete requires an executing session",
            )));
        }
        if matches!(outcome, HostOutcome::Aborted) {
            return Err(boxed(ValidationError::new(
                "complete outcome must be an explicit non-aborted host outcome",
            )));
        }
        let receipt = self.make_receipt(outcome, host_result, usage, None)?;
        self.receipt = Some(receipt);
        self.state = SessionState::Completed;
        Ok(self.receipt.as_ref().expect("receipt inserted"))
    }

    pub fn abort<E>(&mut self, _error: E) -> SdkResult<&ContextReceipt>
    where
        E: Error + 'static,
    {
        if self.state == SessionState::Aborted {
            return self
                .receipt
                .as_ref()
                .ok_or_else(|| boxed(SessionStateError::new("aborted session has no receipt")));
        }
        if self.state == SessionState::Closed {
            if self
                .receipt
                .as_ref()
                .is_some_and(|receipt| receipt.outcome() == HostOutcome::Aborted)
            {
                return Ok(self.receipt.as_ref().expect("receipt exists"));
            }
            return Err(boxed(SessionStateError::new(
                "closed session has no abort receipt",
            )));
        }
        if self.state == SessionState::Completed {
            return Err(boxed(SessionStateError::new(
                "cannot abort a completed session",
            )));
        }
        let host_exception_type = Some(std::any::type_name::<E>().replace("::", "."));
        let receipt = self.make_receipt(HostOutcome::Aborted, None, None, host_exception_type)?;
        self.receipt = Some(receipt);
        self.state = SessionState::Aborted;
        Ok(self.receipt.as_ref().expect("receipt inserted"))
    }

    pub fn abort_message(&mut self, message: impl Into<String>) -> SdkResult<&ContextReceipt> {
        self.abort(crate::errors::SDKError::new(message))
    }

    pub fn recover(&mut self, selected: Option<&ContextView>) -> SdkResult<RecoveredSource> {
        if !matches!(
            self.state,
            SessionState::Executing | SessionState::Completed | SessionState::Aborted
        ) {
            return Err(boxed(RecoveryUnavailableError::new(
                "recover requires an executing or terminal session",
            )));
        }
        let current = self.view.as_ref();
        let selected = selected.or(current).ok_or_else(|| {
            boxed(RecoveryUnavailableError::new(
                "no validated view is available for recovery",
            ))
        })?;
        if let Some(current) = current {
            if !std::ptr::eq(selected, current) {
                let selected_binding = selected.recovery_binding()?;
                let current_binding = current.recovery_binding()?;
                if selected_binding != current_binding {
                    return Err(boxed(RecoveryUnavailableError::new(
                        "recovery view is not bound to this session",
                    )));
                }
            }
        } else {
            return Err(boxed(RecoveryUnavailableError::new(
                "no validated view is available for recovery",
            )));
        }
        let plan = self
            .plan
            .as_ref()
            .ok_or_else(|| boxed(RecoveryUnavailableError::new("session has no Product plan")))?;
        let recovery_ref = selected.recovery_ref().ok_or_else(|| {
            boxed(RecoveryUnavailableError::new(
                "view has no recovery binding",
            ))
        })?;
        let result = self.engine.recover(
            plan.source().project_root(),
            &plan.source().relative_path()?,
            recovery_ref,
            selected.source_ref(),
            selected.source_digest(),
        )?;
        if result.recovery_ref() != recovery_ref
            || result.source_ref() != selected.source_ref()
            || result.source_digest() != selected.source_digest()
        {
            return Err(boxed(ArtifactIntegrityError::new(
                "recovery binding differs from the validated view",
            )));
        }
        Ok(result)
    }

    pub fn close(&mut self) -> SdkResult<()> {
        if self.state == SessionState::Closed {
            return Ok(());
        }
        if !matches!(self.state, SessionState::Completed | SessionState::Aborted) {
            return Err(boxed(SessionStateError::new(
                "close requires a terminal receipt",
            )));
        }
        self.state = SessionState::Closed;
        Ok(())
    }

    fn ensure_not_terminal(&self) -> SdkResult<()> {
        if matches!(
            self.state,
            SessionState::Completed | SessionState::Aborted | SessionState::Closed
        ) {
            return Err(boxed(SessionStateError::new(
                "planning is not legal after terminal completion",
            )));
        }
        Ok(())
    }

    fn add_degradation(&mut self, value: impl Into<String>) {
        let value = value.into();
        if !self.degradations.iter().any(|item| item == &value) {
            self.degradations.push(value);
        }
    }

    fn abort_engine_failure(&mut self, error: &(dyn Error + 'static)) {
        self.add_degradation(format!(
            "engine:{}",
            error_code(error).unwrap_or("engine_error")
        ));
        let receipt = self.make_receipt(HostOutcome::Aborted, None, None, None);
        if let Ok(receipt) = receipt {
            self.receipt = Some(receipt);
        }
        self.state = SessionState::Aborted;
    }

    fn make_receipt(
        &self,
        outcome: HostOutcome,
        host_result: Option<Value>,
        usage: Option<Value>,
        host_exception_type: Option<String>,
    ) -> SdkResult<ContextReceipt> {
        let integrity = self
            .view
            .as_ref()
            .map_or(Integrity::Unsealed, ContextView::integrity_status);
        ContextReceipt::new(
            &self.session_id,
            &self.task_id,
            self.plan.as_ref().map(|value| value.plan_id().to_owned()),
            self.view.clone(),
            outcome,
            integrity,
            self.degradations.clone(),
            usage,
            host_exception_type,
            host_result,
        )
    }
}

fn runtime_id(prefix: &str) -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    format!(
        "{prefix}-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

fn same_json(left: Option<&Value>, right: Option<&Value>) -> bool {
    match (left, right) {
        (None, None) => true,
        (Some(left), Some(right)) => canonical_bytes(left).ok() == canonical_bytes(right).ok(),
        _ => false,
    }
}

fn error_code(error: &(dyn Error + 'static)) -> Option<&'static str> {
    if error.is::<EngineUnavailable>() {
        Some("engine_unavailable")
    } else if error.is::<EngineTimeout>() {
        Some("engine_timeout")
    } else if error.is::<EngineRejected>() {
        Some("engine_rejected")
    } else if error.is::<EngineExecutionError>() {
        Some("engine_execution_error")
    } else {
        None
    }
}

fn is_fail_open_error(error: &(dyn Error + 'static)) -> bool {
    error.is::<EngineUnavailable>() || error.is::<EngineTimeout>()
}
