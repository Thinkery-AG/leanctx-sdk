use std::error::Error as StdError;

use serde_json::json;
use thiserror::Error;

/// A boxed error used internally by the SDK's fallible operations.
pub(crate) type SdkResult<T> = Result<T, Box<dyn StdError + Send + Sync>>;

pub(crate) fn boxed<E>(error: E) -> Box<dyn StdError + Send + Sync>
where
    E: StdError + Send + Sync + 'static,
{
    Box::new(error)
}

macro_rules! sdk_error {
    ($name:ident, $code:literal, $guidance:literal, $retryable:literal,
     $degrade:literal, $abort:literal, $configuration:literal,
     $version:literal) => {
        #[derive(Debug, Error)]
        #[error("{message}")]
        pub struct $name {
            message: String,
        }

        impl $name {
            pub fn new(message: impl Into<String>) -> Self {
                Self {
                    message: message.into(),
                }
            }

            pub fn code(&self) -> &'static str {
                $code
            }

            pub fn guidance(&self) -> &'static str {
                $guidance
            }

            pub fn retryable(&self) -> bool {
                $retryable
            }

            pub fn degrade_allowed(&self) -> bool {
                $degrade
            }

            pub fn abort_required(&self) -> bool {
                $abort
            }

            pub fn configuration_fix(&self) -> bool {
                $configuration
            }

            pub fn version_change(&self) -> bool {
                $version
            }

            pub fn as_dict(&self) -> serde_json::Value {
                json!({
                    "abort_required": self.abort_required(),
                    "code": self.code(),
                    "configuration_fix": self.configuration_fix(),
                    "degrade_allowed": self.degrade_allowed(),
                    "guidance": self.guidance(),
                    "retryable": self.retryable(),
                    "version_change": self.version_change(),
                })
            }
        }
    };
}

sdk_error!(
    SDKError,
    "sdk_error",
    "inspect the stable error code and preserve original evidence",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    ValidationError,
    "validation_error",
    "fix caller input before retrying",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    ConfigurationError,
    "configuration_error",
    "fix SDK configuration before retrying",
    false,
    false,
    true,
    true,
    false
);
sdk_error!(
    SessionStateError,
    "session_state_error",
    "fix lifecycle ordering or create a new session",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    EngineError,
    "engine_error",
    "preserve Engine evidence and classify the concrete error",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    EngineUnavailable,
    "engine_unavailable",
    "restore the configured Engine binary or use explicit bounded fail-open",
    true,
    true,
    false,
    true,
    false
);
sdk_error!(
    EngineTimeout,
    "engine_timeout",
    "retry within host policy or use explicit bounded fail-open",
    true,
    true,
    false,
    false,
    false
);
sdk_error!(
    EngineCrashed,
    "engine_crashed",
    "create a new AgentContext; mutation and execution calls are never retried",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    AgentPermissionError,
    "agent_permission_denied",
    "create a new AgentContext with the required explicit permission",
    false,
    false,
    true,
    true,
    false
);
sdk_error!(
    UnsupportedCapabilityError,
    "unsupported_capability",
    "install a compatible Engine or choose a negotiated capability",
    false,
    false,
    true,
    false,
    true
);
sdk_error!(
    EngineProtocolError,
    "engine_protocol_error",
    "fail closed and verify Engine interface, schema, and transport",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    CompatibilityError,
    "compatibility_error",
    "install a supported version from the compatibility matrix",
    false,
    false,
    true,
    false,
    true
);
sdk_error!(
    UnsupportedEngineError,
    "unsupported_engine",
    "install an Engine identity and capability supported by this SDK",
    false,
    false,
    true,
    false,
    true
);
sdk_error!(
    EngineRejected,
    "engine_rejected",
    "fail closed and satisfy the reported Engine policy",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    PolicyAdmissionError,
    "policy_admission_rejected",
    "abort or change the request to satisfy the reported Engine policy",
    false,
    false,
    true,
    true,
    false
);
sdk_error!(
    EngineExecutionError,
    "engine_execution_error",
    "fail closed and retain the factual Engine failure evidence",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    SourceUnavailableError,
    "source_unavailable",
    "restore source access or select another source before retrying",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    RecoveryUnavailableError,
    "recovery_unavailable",
    "abort and restore the exact source and recovery binding",
    false,
    false,
    true,
    false,
    false
);
sdk_error!(
    FrameworkIntegrationError,
    "framework_integration_error",
    "fix the framework installation or adapter lifecycle before retrying",
    false,
    false,
    true,
    true,
    false
);
sdk_error!(
    FrameworkCompatibilityError,
    "framework_compatibility_error",
    "install the exact certified framework version",
    false,
    false,
    true,
    false,
    true
);
sdk_error!(
    ArtifactIntegrityError,
    "artifact_integrity_error",
    "abort and replace the artifact with a digest-verified copy",
    false,
    false,
    true,
    false,
    false
);
