package leanctx

// Deprecated Engine names (SDK 1.3.0). LeanCTX running on a machine is an
// instance; the Instance names are primary. Every Engine name below keeps
// working until SDK 2.0. Types are aliases, so errors.As, type assertions
// and type switches on an old name match exactly what the SDK returns.
// The engine interface contract (wire fields such as
// engine_interface_version, engine_id, engine_version) is unchanged.

// Deprecated: use InstanceClient.
type EngineClient = InstanceClient

// Deprecated: use SubprocessInstanceClient.
type SubprocessEngineClient = SubprocessInstanceClient

// Deprecated: use SubprocessInstanceClientOptions.
type SubprocessEngineClientOptions = SubprocessInstanceClientOptions

// Deprecated: use InstanceContextClient.
type EngineContextClient = InstanceContextClient

// Deprecated: use InstanceContextClientOptions.
type EngineContextClientOptions = InstanceContextClientOptions

// Deprecated: use InstanceContextReadResult.
type EngineContextReadResult = InstanceContextReadResult

// Deprecated: use EnterpriseInstanceClient.
type EnterpriseEngineClient = EnterpriseInstanceClient

// Deprecated: use InstancePlanningRequest.
type EnginePlanningRequest = InstancePlanningRequest

// Deprecated: use InstanceSourcePlanResponse.
type EngineSourcePlanResponse = InstanceSourcePlanResponse

// Deprecated: use InstanceSourceMaterializationRequest.
type EngineSourceMaterializationRequest = InstanceSourceMaterializationRequest

// Deprecated: use InstanceSourceMaterializationResponse.
type EngineSourceMaterializationResponse = InstanceSourceMaterializationResponse

// Deprecated: use InstanceSourceMaterializationRequestV2.
type EngineSourceMaterializationRequestV2 = InstanceSourceMaterializationRequestV2

// Deprecated: use InstanceSourceMaterializationResponseV2.
type EngineSourceMaterializationResponseV2 = InstanceSourceMaterializationResponseV2

// Deprecated: use InstancePresenterGrant.
type EnginePresenterGrant = InstancePresenterGrant

// Deprecated: use InstanceSourceExecutionV2Request.
type EngineSourceExecutionV2Request = InstanceSourceExecutionV2Request

// Deprecated: use InstanceSourceExecutionV2Response.
type EngineSourceExecutionV2Response = InstanceSourceExecutionV2Response

// Deprecated: use InstanceOutcomeRequest.
type EngineOutcomeRequest = InstanceOutcomeRequest

// Deprecated: use InstanceOutcomeResponse.
type EngineOutcomeResponse = InstanceOutcomeResponse

// Deprecated: use InstanceProviderExecutionRequest.
type EngineProviderExecutionRequest = InstanceProviderExecutionRequest

// Deprecated: use InstanceProviderExecutionOutput.
type EngineProviderExecutionOutput = InstanceProviderExecutionOutput

// Deprecated: use InstanceProviderExecutionUsage.
type EngineProviderExecutionUsage = InstanceProviderExecutionUsage

// Deprecated: use InstanceProviderExecutionCost.
type EngineProviderExecutionCost = InstanceProviderExecutionCost

// Deprecated: use InstanceProviderExecutionFailure.
type EngineProviderExecutionFailure = InstanceProviderExecutionFailure

// Deprecated: use InstanceProviderExecutionResponse.
type EngineProviderExecutionResponse = InstanceProviderExecutionResponse

// Deprecated: use InstanceStatus.
type EngineStatus = InstanceStatus

// Deprecated: use InstanceError.
type EngineError = InstanceError

// Deprecated: use InstanceUnavailable.
type EngineUnavailable = InstanceUnavailable

// Deprecated: use InstanceTimeout.
type EngineTimeout = InstanceTimeout

// Deprecated: use InstanceCrashed.
type EngineCrashed = InstanceCrashed

// Deprecated: use InstanceProtocolError.
type EngineProtocolError = InstanceProtocolError

// Deprecated: use UnsupportedInstanceError.
type UnsupportedEngineError = UnsupportedInstanceError

// Deprecated: use InstanceRejected.
type EngineRejected = InstanceRejected

// Deprecated: use InstanceExecutionError.
type EngineExecutionError = InstanceExecutionError

const (
	// Deprecated: use InstanceStatusSucceeded.
	EngineStatusSucceeded = InstanceStatusSucceeded
	// Deprecated: use InstanceStatusDegraded.
	EngineStatusDegraded = InstanceStatusDegraded
	// Deprecated: use InstanceStatusRejected.
	EngineStatusRejected = InstanceStatusRejected
	// Deprecated: use InstanceStatusFailed.
	EngineStatusFailed = InstanceStatusFailed
	// Deprecated: use SessionContainmentInstanceStarter.
	SessionContainmentEngineStarter = SessionContainmentInstanceStarter
	// Deprecated: use InstancePresenterCapabilityMinInstanceVersion.
	EnginePresenterCapabilityMinEngineVersion = InstancePresenterCapabilityMinInstanceVersion
	// Deprecated: use MaxInstancePresenterTTLSeconds.
	MaxEnginePresenterTTLSeconds = MaxInstancePresenterTTLSeconds
	// Deprecated: use SupportedAgentToolsInstanceVersion.
	SupportedAgentToolsEngineVersion = SupportedAgentToolsInstanceVersion
	// Deprecated: use SUPPORTED_AGENT_TOOLS_INSTANCE_VERSION.
	SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = SupportedAgentToolsInstanceVersion
)

// Deprecated: use NewSubprocessInstanceClient.
func NewSubprocessEngineClient(options ...SubprocessInstanceClientOptions) (*SubprocessInstanceClient, error) {
	return NewSubprocessInstanceClient(options...)
}

// Deprecated: use NewInstanceContextClient.
func NewEngineContextClient(baseURL, credential string, options ...InstanceContextClientOptions) (*InstanceContextClient, error) {
	return NewInstanceContextClient(baseURL, credential, options...)
}

// Deprecated: use NewEnterpriseInstanceClient.
func NewEnterpriseEngineClient(baseURL, credential, tenantID string, options ...InstanceContextClientOptions) (*EnterpriseInstanceClient, error) {
	return NewEnterpriseInstanceClient(baseURL, credential, tenantID, options...)
}

// Deprecated: use NewInstancePlanningRequest.
func NewEnginePlanningRequest(taskID, query string, budgetTokens, maxCandidates uint64) (InstancePlanningRequest, error) {
	return NewInstancePlanningRequest(taskID, query, budgetTokens, maxCandidates)
}

// Deprecated: use NewInstanceUnavailable.
func NewEngineUnavailable(message string) *InstanceUnavailable {
	return NewInstanceUnavailable(message)
}

// Deprecated: use NewInstanceTimeout.
func NewEngineTimeout(message string) *InstanceTimeout { return NewInstanceTimeout(message) }

// Deprecated: use NewInstanceCrashed.
func NewEngineCrashed(message string) *InstanceCrashed { return NewInstanceCrashed(message) }

// Deprecated: use NewInstanceProtocolError.
func NewEngineProtocolError(message string) *InstanceProtocolError {
	return NewInstanceProtocolError(message)
}

// Deprecated: use NewUnsupportedInstanceError.
func NewUnsupportedEngineError(message string) *UnsupportedInstanceError {
	return NewUnsupportedInstanceError(message)
}

// Deprecated: use NewInstanceRejected.
func NewEngineRejected(message string, failure *ContextFailure, view *ContextView) *InstanceRejected {
	return NewInstanceRejected(message, failure, view)
}

// Deprecated: use NewInstanceExecutionError.
func NewEngineExecutionError(message string, failure *ContextFailure, view *ContextView) *InstanceExecutionError {
	return NewInstanceExecutionError(message, failure, view)
}
