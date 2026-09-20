// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"context"
	"unicode/utf8"
)

const (
	enterpriseProviderExecutionPath         = "/v1/engine/provider-execute"
	enterpriseProviderExecutionMaxResponse  = 1024 * 1024
	enterpriseProviderExecutionMaxOutput    = 256 * 1024
	enterpriseProviderExecutionMaxOutputTok = uint64(65_536)
	enterpriseProviderExecutionMaxSafe      = uint64(maxSafeInteger)
)

var (
	enterpriseProviderExecutionStatuses = map[string]bool{
		"succeeded": true, "failed": true, "rejected": true,
		"timed_out": true, "dispatch_uncertain": true,
	}
	enterpriseProviderExecutionFailureCodes = map[string]bool{
		"policy_rejected": true, "source_unavailable": true,
		"source_integrity_mismatch": true, "resource_limit": true,
		"unsupported_operation": true, "internal": true,
	}
)

// EngineProviderExecutionRequest is a bounded, authenticated provider
// execution request. The Enterprise host remains authoritative for provider
// admission, source content, dispatch, accounting, and acceptance.
type EngineProviderExecutionRequest struct {
	Task            map[string]any
	Plan            map[string]any
	Materialization EngineSourceMaterializationRequest
	MaxOutputTokens uint64
}

// EngineProviderExecutionOutput contains provider output and its content hash.
type EngineProviderExecutionOutput struct {
	Content      string
	SHA256Digest string
}

// EngineProviderExecutionUsage preserves nullable provider usage counters.
type EngineProviderExecutionUsage struct {
	State                 string
	UncachedInputTokens   *uint64
	CacheWriteInputTokens *uint64
	CacheReadInputTokens  *uint64
	TotalInputTokens      *uint64
	OutputTokens          *uint64
}

// EngineProviderExecutionCost is a provenance label, not a billing proof.
type EngineProviderExecutionCost struct {
	Basis  string
	Micros *uint64
}

// EngineProviderExecutionFailure is always non-retryable by the host.
type EngineProviderExecutionFailure struct {
	Code            string
	RecoveryRef     *string
	RetryableByHost bool
}

// EngineProviderExecutionResponse is a validated provider projection. It does
// not verify a signer, establish acceptance, or claim an observed charge.
type EngineProviderExecutionResponse struct {
	SchemaVersion          uint64
	TransportVersion       uint64
	EngineInterfaceVersion string
	AttemptID              string
	TaskID                 string
	PlanID                 string
	ContextDigest          string
	RequestDigest          string
	Provider               string
	Model                  string
	Status                 string
	Acceptance             string
	Output                 *EngineProviderExecutionOutput
	Usage                  EngineProviderExecutionUsage
	Cost                   EngineProviderExecutionCost
	Failure                *EngineProviderExecutionFailure
	RawResponse            map[string]any
}

// ProviderExecute performs one bounded authenticated provider execution.
func (c *EnterpriseEngineClient) ProviderExecute(request EngineProviderExecutionRequest) (*EngineProviderExecutionResponse, error) {
	return c.ProviderExecuteContext(context.Background(), request)
}

// ProviderExecuteContext is the cancellable form of ProviderExecute.
func (c *EnterpriseEngineClient) ProviderExecuteContext(parent context.Context, request EngineProviderExecutionRequest) (*EngineProviderExecutionResponse, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseEngineClient is not initialized")
	}
	planning, sourceIDs, bindingDigest, err := request.Materialization.checked()
	if err != nil {
		return nil, err
	}
	normalizedTask, err := enterpriseExecutionTask(request.Task, planning, c.tenantID, false)
	if err != nil {
		return nil, err
	}
	normalizedPlan, err := enterpriseExecutionPlan(request.Plan, planning.TaskID, false, true)
	if err != nil {
		return nil, err
	}
	contextPlanID, contextPlanOK := normalizedPlan["context_plan_id"].(string)
	if !contextPlanOK || contextPlanID == "" {
		return nil, NewValidationError("providerExecute requires a concrete context_plan_id")
	}
	contextBudget, err := enterpriseExecutionBoundedU64(normalizedPlan["context_budget_tokens"], "plan.context_budget_tokens", planning.BudgetTokens, false)
	if err != nil || contextBudget < 1 {
		return nil, NewValidationError("providerExecute requires a bounded positive context budget")
	}
	provider, providerOK := normalizedPlan["provider"].(string)
	model, modelOK := normalizedPlan["model"].(string)
	if !providerOK || !modelOK || provider == enterpriseExecutionLocalNative || provider == "auto" || model == enterpriseExecutionLocalNative || model == "auto" {
		return nil, NewValidationError("providerExecute requires a concrete non-local provider plan")
	}
	maxRetries, err := enterpriseExecutionBoundedU64(normalizedPlan["max_retries"], "plan.max_retries", enterpriseMaxExecutionU32, false)
	if err != nil || maxRetries != 0 {
		return nil, NewValidationError("providerExecute does not permit host retries or fallbacks")
	}
	fallbackRefs, fallbackOK := normalizedPlan["fallback_refs"].([]any)
	if !fallbackOK || len(fallbackRefs) != 0 {
		return nil, NewValidationError("providerExecute does not permit host retries or fallbacks")
	}
	if request.MaxOutputTokens < 1 || request.MaxOutputTokens > enterpriseProviderExecutionMaxOutputTok {
		return nil, NewValidationError("max_output_tokens is outside its protocol bounds")
	}
	payloadValue := map[string]any{
		"schema_version": enterpriseSchemaVersion,
		"task":           normalizedTask,
		"plan":           normalizedPlan,
		"materialization": map[string]any{
			"planning":                     planning.ToDict(),
			"source_ids":                   sourceIDs,
			"expected_governance_revision": request.Materialization.ExpectedGovernanceRevision,
			"expected_binding_digest":      bindingDigest,
		},
		"max_output_tokens": request.MaxOutputTokens,
	}
	materialization := payloadValue["materialization"].(map[string]any)
	if request.Materialization.PlanningEvaluationTime != nil {
		materialization["planning_evaluation_time"] = *request.Materialization.PlanningEvaluationTime
	}
	payload, err := canonicalJSON(payloadValue)
	if err != nil || len(payload) > enterpriseMaxRequestBytes {
		return nil, NewValidationError("Enterprise Engine provider execution request exceeds its byte bound")
	}
	raw, err := c.transport.postJSONContext(parent, enterpriseProviderExecutionPath, payload, enterpriseProviderExecutionMaxResponse, "Enterprise provider execution", "provider execution request")
	if err != nil {
		return nil, err
	}
	return parseEnterpriseProviderExecutionResponse(raw, normalizedTask, normalizedPlan, c.tenantID)
}

func parseEnterpriseProviderExecutionResponse(raw []byte, task, plan map[string]any, tenantID string) (*EngineProviderExecutionResponse, error) {
	if len(raw) > enterpriseProviderExecutionMaxResponse {
		return nil, enterpriseProtocol("provider execution response exceeds its byte bound")
	}
	if !contextReadUnicodeEscapesValid(raw) {
		return nil, enterpriseProtocol("provider execution response has an unpaired Unicode surrogate")
	}
	decoded, err := strictJSONLoads(raw, "Enterprise provider execution response")
	if err != nil {
		return nil, enterpriseProtocol("Enterprise provider execution response is not valid JSON")
	}
	response, err := enterpriseObject(decoded, "provider execution response")
	if err != nil {
		return nil, err
	}
	required := map[string]bool{
		"schema_version": true, "transport_version": true, "engine_interface_version": true,
		"attempt_id": true, "task_id": true, "plan_id": true, "context_digest": true,
		"request_digest": true, "provider": true, "model": true, "status": true,
		"acceptance": true, "usage": true, "cost": true,
	}
	allowed := map[string]bool{}
	for key := range required {
		allowed[key] = true
	}
	allowed["output"], allowed["failure"] = true, true
	if err := enterpriseExecutionFields(response, allowed, required, "provider execution response", true); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionLexicalField(response, "schema_version", "response.schema_version", false); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionLexicalField(response, "transport_version", "response.transport_version", false); err != nil {
		return nil, err
	}
	schemaVersion, err := enterpriseExecutionBoundedU64(response["schema_version"], "response.schema_version", enterpriseSchemaVersion, true)
	if err != nil || schemaVersion != enterpriseSchemaVersion {
		return nil, enterpriseProtocol("provider execution response schema_version is unsupported")
	}
	transportVersion, err := enterpriseExecutionBoundedU64(response["transport_version"], "response.transport_version", enterpriseTransportVersion, true)
	if err != nil || transportVersion != enterpriseTransportVersion {
		return nil, enterpriseProtocol("provider execution response transport_version is unsupported")
	}
	interfaceVersion, err := enterpriseExecutionText(response["engine_interface_version"], "response.engine_interface_version", enterpriseMaxIdentifierBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	if interfaceVersion != EngineInterfaceVersion {
		return nil, enterpriseProtocol("provider execution Engine interface is unsupported")
	}
	if task["tenant_id"] != tenantID {
		return nil, enterpriseProtocol("provider execution task tenant does not bind the client")
	}
	attemptID, err := enterpriseExecutionText(response["attempt_id"], "response.attempt_id", enterpriseMaxReferenceBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	taskID, err := enterpriseExecutionText(response["task_id"], "response.task_id", enterpriseMaxReferenceBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	planID, err := enterpriseExecutionText(response["plan_id"], "response.plan_id", enterpriseMaxReferenceBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	requestedTaskID, _ := task["task_id"].(string)
	requestedPlanID, _ := plan["plan_id"].(string)
	if taskID != requestedTaskID || planID != requestedPlanID {
		return nil, enterpriseProtocol("provider execution response task or plan does not bind request")
	}
	contextDigest, err := enterpriseDigest(response["context_digest"], "response.context_digest")
	if err != nil {
		return nil, err
	}
	requestDigest, err := enterpriseDigest(response["request_digest"], "response.request_digest")
	if err != nil {
		return nil, err
	}
	provider, err := enterpriseExecutionText(response["provider"], "response.provider", enterpriseMaxReferenceBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	model, err := enterpriseExecutionText(response["model"], "response.model", enterpriseMaxReferenceBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	requestedProvider, _ := plan["provider"].(string)
	requestedModel, _ := plan["model"].(string)
	if provider != requestedProvider || model != requestedModel {
		return nil, enterpriseProtocol("provider execution response provider or model does not bind plan")
	}
	status, err := enterpriseExecutionText(response["status"], "response.status", enterpriseMaxIdentifierBytes, true, true, true)
	if err != nil || !enterpriseProviderExecutionStatuses[status] {
		return nil, enterpriseProtocol("provider execution response status is unsupported")
	}
	acceptance, err := enterpriseExecutionText(response["acceptance"], "response.acceptance", enterpriseMaxIdentifierBytes, true, true, true)
	if err != nil || acceptance != "unknown" {
		return nil, enterpriseProtocol("provider execution response acceptance must remain unknown")
	}
	usage, err := parseEnterpriseProviderUsage(response["usage"])
	if err != nil {
		return nil, err
	}
	cost, err := parseEnterpriseProviderCost(response["cost"])
	if err != nil {
		return nil, err
	}
	if cost.Basis == "usage_priced_estimate" && usage.State == "unavailable" {
		return nil, enterpriseProtocol("provider cost cannot price unavailable usage")
	}
	var output *EngineProviderExecutionOutput
	if value, present := response["output"]; present && value != nil {
		output, err = parseEnterpriseProviderOutput(value)
		if err != nil {
			return nil, err
		}
	}
	var failure *EngineProviderExecutionFailure
	if value, present := response["failure"]; present && value != nil {
		failure, err = parseEnterpriseProviderFailure(value)
		if err != nil {
			return nil, err
		}
	}
	if status == "succeeded" {
		if output == nil || failure != nil {
			return nil, enterpriseProtocol("succeeded provider response requires output only")
		}
	} else if output != nil || failure == nil {
		return nil, enterpriseProtocol("non-success provider response requires failure only")
	}
	return &EngineProviderExecutionResponse{
		SchemaVersion: schemaVersion, TransportVersion: transportVersion,
		EngineInterfaceVersion: interfaceVersion, AttemptID: attemptID,
		TaskID: taskID, PlanID: planID, ContextDigest: contextDigest,
		RequestDigest: requestDigest, Provider: provider, Model: model,
		Status: status, Acceptance: acceptance, Output: output, Usage: usage,
		Cost: cost, Failure: failure, RawResponse: response,
	}, nil
}

func parseEnterpriseProviderUsage(value any) (EngineProviderExecutionUsage, error) {
	raw, err := enterpriseObject(value, "provider usage")
	if err != nil {
		return EngineProviderExecutionUsage{}, err
	}
	fields := map[string]bool{"state": true, "uncached_input_tokens": true, "cache_write_input_tokens": true, "cache_read_input_tokens": true, "total_input_tokens": true, "output_tokens": true}
	if err := enterpriseExecutionFields(raw, fields, fields, "provider usage", true); err != nil {
		return EngineProviderExecutionUsage{}, err
	}
	state, err := enterpriseExecutionText(raw["state"], "provider usage.state", enterpriseMaxIdentifierBytes, true, true, true)
	if err != nil || (state != "measured" && state != "estimated" && state != "unavailable") {
		return EngineProviderExecutionUsage{}, enterpriseProtocol("provider usage state is unsupported")
	}
	values := make(map[string]*uint64, 5)
	for _, field := range []string{"uncached_input_tokens", "cache_write_input_tokens", "cache_read_input_tokens", "total_input_tokens", "output_tokens"} {
		value := raw[field]
		if value == nil {
			values[field] = nil
			continue
		}
		if err := enterpriseExecutionLexicalInteger(value, "provider usage."+field, false); err != nil {
			return EngineProviderExecutionUsage{}, err
		}
		parsed, parseErr := enterpriseExecutionBoundedU64(value, "provider usage."+field, enterpriseProviderExecutionMaxSafe, true)
		if parseErr != nil {
			return EngineProviderExecutionUsage{}, parseErr
		}
		copyValue := parsed
		values[field] = &copyValue
	}
	if state == "unavailable" {
		for _, value := range values {
			if value != nil {
				return EngineProviderExecutionUsage{}, enterpriseProtocol("unavailable provider usage must omit every counter")
			}
		}
	} else {
		for _, value := range values {
			if value == nil {
				return EngineProviderExecutionUsage{}, enterpriseProtocol("measured or estimated provider usage requires every counter")
			}
		}
		if values["uncached_input_tokens"] != nil && values["cache_write_input_tokens"] != nil && values["cache_read_input_tokens"] != nil && values["total_input_tokens"] != nil {
			uncached := *values["uncached_input_tokens"]
			writes := *values["cache_write_input_tokens"]
			reads := *values["cache_read_input_tokens"]
			if uncached > enterpriseProviderExecutionMaxSafe-writes || uncached+writes > enterpriseProviderExecutionMaxSafe-reads || uncached+writes+reads != *values["total_input_tokens"] {
				return EngineProviderExecutionUsage{}, enterpriseProtocol("provider usage total_input_tokens does not match components")
			}
		}
	}
	return EngineProviderExecutionUsage{State: state, UncachedInputTokens: values["uncached_input_tokens"], CacheWriteInputTokens: values["cache_write_input_tokens"], CacheReadInputTokens: values["cache_read_input_tokens"], TotalInputTokens: values["total_input_tokens"], OutputTokens: values["output_tokens"]}, nil
}

func parseEnterpriseProviderCost(value any) (EngineProviderExecutionCost, error) {
	raw, err := enterpriseObject(value, "provider cost")
	if err != nil {
		return EngineProviderExecutionCost{}, err
	}
	basis, basisOK := raw["basis"].(string)
	if !basisOK || basis == "" {
		return EngineProviderExecutionCost{}, enterpriseProtocol("provider cost basis is unsupported")
	}
	if basis == "unavailable" {
		if len(raw) != 1 {
			return EngineProviderExecutionCost{}, enterpriseProtocol("unavailable provider cost has unexpected fields")
		}
		return EngineProviderExecutionCost{Basis: basis}, nil
	}
	if basis != "usage_priced_estimate" && basis != "observed_charge" || len(raw) != 2 || raw["micros"] == nil {
		return EngineProviderExecutionCost{}, enterpriseProtocol("provider cost fields do not match the v1 contract")
	}
	if err := enterpriseExecutionLexicalInteger(raw["micros"], "provider cost.micros", false); err != nil {
		return EngineProviderExecutionCost{}, err
	}
	micros, err := enterpriseExecutionBoundedU64(raw["micros"], "provider cost.micros", enterpriseProviderExecutionMaxSafe, true)
	if err != nil {
		return EngineProviderExecutionCost{}, err
	}
	return EngineProviderExecutionCost{Basis: basis, Micros: &micros}, nil
}

func parseEnterpriseProviderFailure(value any) (*EngineProviderExecutionFailure, error) {
	raw, err := enterpriseObject(value, "provider failure")
	if err != nil {
		return nil, err
	}
	allowed := map[string]bool{"code": true, "retryable_by_host": true, "recovery_ref": true}
	required := map[string]bool{"code": true, "retryable_by_host": true}
	if err := enterpriseExecutionFields(raw, allowed, required, "provider failure", true); err != nil {
		return nil, err
	}
	code, err := enterpriseExecutionText(raw["code"], "provider failure.code", enterpriseMaxIdentifierBytes, true, true, true)
	if err != nil || !enterpriseProviderExecutionFailureCodes[code] {
		return nil, enterpriseProtocol("provider failure code is unsupported")
	}
	retryable, retryableOK := raw["retryable_by_host"].(bool)
	if !retryableOK {
		return nil, enterpriseProtocol("provider failure retryable_by_host must be boolean")
	}
	if retryable {
		return nil, enterpriseProtocol("provider failures must not request host retry")
	}
	var recovery *string
	if value, present := raw["recovery_ref"]; present && value != nil {
		text, textErr := protocolRef(value, "provider failure recovery_ref")
		if textErr != nil {
			return nil, enterpriseProtocol("provider failure recovery_ref is invalid")
		}
		recovery = &text
	}
	if code == "policy_rejected" && recovery != nil {
		return nil, enterpriseProtocol("policy rejection cannot request recovery")
	}
	if (code == "source_unavailable" || code == "source_integrity_mismatch") && recovery == nil {
		return nil, enterpriseProtocol("source failure requires a recovery_ref")
	}
	return &EngineProviderExecutionFailure{Code: code, RetryableByHost: false, RecoveryRef: recovery}, nil
}

func parseEnterpriseProviderOutput(value any) (*EngineProviderExecutionOutput, error) {
	raw, err := enterpriseObject(value, "provider output")
	if err != nil {
		return nil, err
	}
	if err := enterpriseExecutionFields(raw, map[string]bool{"content": true, "sha256_digest": true}, map[string]bool{"content": true, "sha256_digest": true}, "provider output", true); err != nil {
		return nil, err
	}
	content, ok := raw["content"].(string)
	if !ok || !utf8.ValidString(content) || len([]byte(content)) > enterpriseProviderExecutionMaxOutput {
		return nil, enterpriseProtocol("provider output.content exceeds its byte bound")
	}
	digest, err := enterpriseDigest(raw["sha256_digest"], "provider output.sha256_digest")
	if err != nil {
		return nil, err
	}
	if sha256Hex([]byte(content)) != digest {
		return nil, enterpriseProtocol("provider output digest does not match content")
	}
	return &EngineProviderExecutionOutput{Content: content, SHA256Digest: digest}, nil
}
