package leanctx

// Stable SDK and protocol versions.  The Agent Tools contract is deliberately
// separate from the Product/Engine Interface v1 contract.
const (
	Version                          = "1.1.0"
	EngineInterfaceVersion           = "1.0.0"
	SchemaVersion                    = 1
	TransportVersion                 = 1
	AgentToolsInterfaceVersion       = "1.0.0"
	AgentToolsSchemaVersion          = 1
	AgentToolsTransportVersion       = 1
	SupportedAgentToolsEngineVersion = "3.10.1"

	// Contract-spelling aliases keep generated integrations source-compatible
	// with the other SDKs, whose constants use uppercase names.
	ENGINE_INTERFACE_VERSION             = EngineInterfaceVersion
	SCHEMA_VERSION                       = SchemaVersion
	TRANSPORT_VERSION                    = TransportVersion
	AGENT_TOOLS_INTERFACE_VERSION        = AgentToolsInterfaceVersion
	AGENT_TOOLS_SCHEMA_VERSION           = AgentToolsSchemaVersion
	AGENT_TOOLS_TRANSPORT_VERSION        = AgentToolsTransportVersion
	SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = SupportedAgentToolsEngineVersion
)
