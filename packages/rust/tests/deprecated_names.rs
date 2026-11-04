// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
//! The Engine names deprecated in 1.3.0 keep compiling and behaving like the
//! Instance names until 2.0.
#![allow(deprecated)]

use std::error::Error;
use std::path::Path;
use std::time::Duration;

use leanctx_sdk::{
    AgentContextBuilder, ContextSession, EngineClient, EngineCrashed, EngineError,
    EngineExecutionError, EnginePlanningRequest, EngineProtocolError, EngineRejected, EngineStatus,
    EngineTimeout, EngineUnavailable, InstanceStatus, InstanceUnavailable, SubprocessEngineClient,
    SubprocessInstanceClient, UnsupportedEngineError,
};

fn returns_instance_error() -> Box<dyn Error + Send + Sync> {
    Box::new(InstanceUnavailable::new("LeanCTX instance is unavailable"))
}

#[test]
fn deprecated_error_names_match_instance_errors() {
    let error = returns_instance_error();
    let old = error
        .downcast_ref::<EngineUnavailable>()
        .expect("old name matches");
    assert_eq!(old.code(), "engine_unavailable");
    assert!(old.retryable());
    assert_eq!(old.to_string(), "LeanCTX instance is unavailable");

    assert_eq!(EngineError::new("x").code(), "engine_error");
    assert_eq!(EngineTimeout::new("x").code(), "engine_timeout");
    assert_eq!(EngineCrashed::new("x").code(), "engine_crashed");
    assert_eq!(
        EngineProtocolError::new("x").code(),
        "engine_protocol_error"
    );
    assert_eq!(EngineRejected::new("x").code(), "engine_rejected");
    assert_eq!(
        EngineExecutionError::new("x").code(),
        "engine_execution_error"
    );
    assert_eq!(
        UnsupportedEngineError::new("x").code(),
        "unsupported_engine"
    );
}

#[test]
fn deprecated_type_and_constant_names_are_the_instance_items() {
    let status: EngineStatus = InstanceStatus::Degraded;
    assert!(matches!(status, EngineStatus::Degraded));
    assert_eq!(status.as_str(), "degraded");

    assert_eq!(
        leanctx_sdk::SUPPORTED_AGENT_TOOLS_ENGINE_VERSION,
        leanctx_sdk::SUPPORTED_AGENT_TOOLS_INSTANCE_VERSION
    );
    assert_eq!(
        leanctx_sdk::ENGINE_PRESENTER_CAPABILITY_MIN_ENGINE_VERSION,
        leanctx_sdk::INSTANCE_PRESENTER_CAPABILITY_MIN_INSTANCE_VERSION
    );
    assert_eq!(
        leanctx_sdk::MAX_ENGINE_PRESENTER_TTL_SECONDS,
        leanctx_sdk::MAX_INSTANCE_PRESENTER_TTL_SECONDS
    );
    assert!(EnginePlanningRequest::new("task-1", "inspect", 1024).is_ok());
}

#[test]
fn deprecated_client_and_builder_methods_still_work() -> Result<(), Box<dyn Error + Send + Sync>> {
    let client: SubprocessEngineClient =
        SubprocessInstanceClient::new(Some("/opt/lean-ctx".into()), Duration::from_secs(5))?;
    assert_eq!(client.engine_binary(), Some(Path::new("/opt/lean-ctx")));
    assert_eq!(client.engine_binary(), client.instance_binary());

    let boxed: Box<dyn EngineClient> = Box::new(client.clone());
    let session = ContextSession::with_engine("inspect", boxed)?;
    drop(session);
    let session = ContextSession::new_with_engine("inspect", None, client)?;
    drop(session);

    let builder = AgentContextBuilder::new("/PROJECT").engine_binary("/opt/lean-ctx");
    let renamed = AgentContextBuilder::new("/PROJECT").instance_binary("/opt/lean-ctx");
    assert_eq!(format!("{builder:?}"), format!("{renamed:?}"));
    Ok(())
}
