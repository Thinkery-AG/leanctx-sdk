// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"context"
	"unicode/utf8"
)

const (
	enterpriseContextMaterializePath         = "/v1/engine/context-materialize"
	enterpriseMaxMaterializedContentBytes    = 1024 * 1024
	enterpriseMaxMaterializationResponseSize = enterpriseMaxResponseBytes + enterpriseMaxMaterializedContentBytes
)

// EngineSourceMaterializationRequest binds a source plan to the governance
// revision and binding digest that the caller observed during planning.
type EngineSourceMaterializationRequest struct {
	Planning                   EnginePlanningRequest
	SourceIDs                  []string
	ExpectedGovernanceRevision uint64
	ExpectedBindingDigest      string
	PlanningEvaluationTime     *string
}

// EngineSourceMaterializationResponse is a validated, tenant-bound materialization.
// RawResponse is detached strict JSON; this type is not an execution or receipt proof.
type EngineSourceMaterializationResponse struct {
	SchemaVersion      uint64
	TenantID           string
	GovernanceRevision uint64
	Materialization    map[string]any
	RawResponse        map[string]any
}

func (r EngineSourceMaterializationRequest) checked() (EnginePlanningRequest, []string, string, error) {
	planning := r.Planning.normalized()
	if err := planning.validate(); err != nil {
		return EnginePlanningRequest{}, nil, "", err
	}
	sourceIDs, err := enterpriseSourceIDs(r.SourceIDs)
	if err != nil {
		return EnginePlanningRequest{}, nil, "", err
	}
	if err := validateDigest(r.ExpectedBindingDigest, "expected_binding_digest"); err != nil {
		return EnginePlanningRequest{}, nil, "", err
	}
	if r.PlanningEvaluationTime != nil {
		if _, timestampErr := enterpriseTimestamp(*r.PlanningEvaluationTime, "planning_evaluation_time"); timestampErr != nil {
			return EnginePlanningRequest{}, nil, "", NewValidationError("planning_evaluation_time is invalid")
		}
	}
	return planning, sourceIDs, r.ExpectedBindingDigest, nil
}

// ContextMaterializeSources performs one bounded authenticated materialization.
func (c *EnterpriseEngineClient) ContextMaterializeSources(request EngineSourceMaterializationRequest) (*EngineSourceMaterializationResponse, error) {
	return c.ContextMaterializeSourcesContext(context.Background(), request)
}

// ContextMaterializeSourcesContext is the cancellable form of ContextMaterializeSources.
func (c *EnterpriseEngineClient) ContextMaterializeSourcesContext(parent context.Context, request EngineSourceMaterializationRequest) (*EngineSourceMaterializationResponse, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseEngineClient is not initialized")
	}
	planning, sourceIDs, bindingDigest, err := request.checked()
	if err != nil {
		return nil, err
	}
	payloadValue := map[string]any{
		"planning":                     planning.ToDict(),
		"source_ids":                   sourceIDs,
		"expected_governance_revision": request.ExpectedGovernanceRevision,
		"expected_binding_digest":      bindingDigest,
	}
	if request.PlanningEvaluationTime != nil {
		payloadValue["planning_evaluation_time"] = *request.PlanningEvaluationTime
	}
	payload, err := canonicalJSON(payloadValue)
	if err != nil || len(payload) > enterpriseMaxRequestBytes {
		return nil, NewValidationError("Enterprise Engine materialization request exceeds its byte bound")
	}
	raw, err := c.transport.postJSONContext(
		parent,
		enterpriseContextMaterializePath,
		payload,
		enterpriseMaxMaterializationResponseSize,
		"Enterprise Engine materialization",
		"materialization request",
	)
	if err != nil {
		return nil, err
	}
	return c.parseMaterializationResponse(raw, planning, sourceIDs, request.ExpectedGovernanceRevision, bindingDigest)
}

func (c *EnterpriseEngineClient) parseMaterializationResponse(
	raw []byte,
	request EnginePlanningRequest,
	sourceIDs []string,
	expectedGovernanceRevision uint64,
	expectedBindingDigest string,
) (*EngineSourceMaterializationResponse, error) {
	if !contextReadUnicodeEscapesValid(raw) {
		return nil, enterpriseProtocol("Enterprise Engine materialization response has an unpaired Unicode surrogate")
	}
	decoded, err := strictJSONLoads(raw, "Enterprise Engine materialization response")
	if err != nil {
		return nil, NewEngineProtocolError("Enterprise Engine materialization response is not valid JSON")
	}
	wrapper, err := enterpriseObject(decoded, "Enterprise Engine materialization response")
	if err != nil {
		return nil, err
	}
	if err := enterpriseExactKeys(wrapper, map[string]bool{
		"schema_version": true, "tenant_id": true, "governance_revision": true, "materialization": true,
	}, "Enterprise Engine materialization response"); err != nil {
		return nil, err
	}
	schemaVersion, err := enterpriseU64(wrapper["schema_version"], "response.schema_version")
	if err != nil || schemaVersion != enterpriseSchemaVersion {
		return nil, enterpriseProtocol("response.schema_version is unsupported")
	}
	tenantID, err := enterpriseUUIDValue(wrapper["tenant_id"], "response.tenant_id")
	if err != nil {
		return nil, err
	}
	if tenantID != c.tenantID {
		return nil, enterpriseProtocol("Enterprise Engine materialization response tenant binding does not match")
	}
	governanceRevision, err := enterpriseU64(wrapper["governance_revision"], "response.governance_revision")
	if err != nil {
		return nil, err
	}
	if governanceRevision != expectedGovernanceRevision {
		return nil, enterpriseProtocol("Enterprise Engine materialization governance revision does not match")
	}
	materialization, err := enterpriseObject(wrapper["materialization"], "response.materialization")
	if err != nil {
		return nil, err
	}
	if err := enterpriseExactKeys(materialization, map[string]bool{
		"schema_version": true, "transport_version": true, "engine_interface_version": true,
		"plan": true, "materialized_digest": true, "materialized_token_count": true, "content": true,
	}, "Enterprise Engine materialization"); err != nil {
		return nil, err
	}
	if err := enterpriseHeader(materialization, "Enterprise Engine materialization"); err != nil {
		return nil, err
	}
	planValue, err := enterpriseObject(materialization["plan"], "response.materialization.plan")
	if err != nil {
		return nil, err
	}
	plan, err := enterpriseSourcePlan(planValue, request)
	if err != nil {
		return nil, err
	}
	if err := enterpriseSourceScope(plan, sourceIDs); err != nil {
		return nil, err
	}
	if plan["binding_digest"] != expectedBindingDigest {
		return nil, enterpriseProtocol("Enterprise Engine materialization binding digest does not match")
	}
	materializedDigest, err := enterpriseDigest(materialization["materialized_digest"], "materialized_digest")
	if err != nil {
		return nil, err
	}
	materializedTokenCount, err := enterpriseU64(materialization["materialized_token_count"], "materialized_token_count")
	if err != nil {
		return nil, err
	}
	result, ok := plan["result"].(map[string]any)
	if !ok {
		return nil, enterpriseProtocol("Enterprise Engine materialization plan result is invalid")
	}
	resultPlan, ok := result["plan"].(map[string]any)
	if !ok {
		return nil, enterpriseProtocol("Enterprise Engine materialization plan projection is invalid")
	}
	budget, ok := resultPlan["budget_tokens"].(uint64)
	if !ok || materializedTokenCount > budget {
		return nil, enterpriseProtocol("Enterprise Engine materialized token metric exceeds the plan budget")
	}
	content, err := enterpriseMaterializedContent(materialization["content"])
	if err != nil {
		return nil, err
	}
	if sha256Hex([]byte(content)) != materializedDigest {
		return nil, enterpriseProtocol("Enterprise Engine materialized content digest does not match")
	}
	normalizedMaterialization := map[string]any{
		"schema_version":           enterpriseSchemaVersion,
		"transport_version":        enterpriseTransportVersion,
		"engine_interface_version": EngineInterfaceVersion,
		"plan":                     plan,
		"materialized_digest":      materializedDigest,
		"materialized_token_count": materializedTokenCount,
		"content":                  content,
	}
	return &EngineSourceMaterializationResponse{
		SchemaVersion: schemaVersion, TenantID: tenantID, GovernanceRevision: governanceRevision,
		Materialization: normalizedMaterialization, RawResponse: wrapper,
	}, nil
}

func enterpriseMaterializedContent(value any) (string, error) {
	content, ok := value.(string)
	if !ok || !utf8.ValidString(content) || len([]byte(content)) > enterpriseMaxMaterializedContentBytes {
		return "", enterpriseProtocol("Enterprise Engine materialized content exceeds its byte bound")
	}
	return content, nil
}
