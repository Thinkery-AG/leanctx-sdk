// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"bytes"
	"context"
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	enterpriseContextExecuteV2Path       = "/v2/engine/context-execute"
	enterpriseExecutionV2SchemaVersion   = uint64(2)
	enterpriseMaxExecutionRequestBytes   = 1024 * 1024
	enterpriseMaxExecutionResponseBytes  = 4 * 1024 * 1024
	enterpriseMaxExecutionWrapperBytes   = 64 * 1024
	enterpriseMaxExecutionTotalBytes     = enterpriseMaxExecutionResponseBytes + enterpriseMaxExecutionWrapperBytes
	enterpriseMaxExecutionReceiptBytes   = 1024 * 1024
	enterpriseMaxExecutionViewBytes      = 1024 * 1024
	enterpriseMaxExecutionTaskBytes      = 16 * 1024
	enterpriseMaxExecutionU32            = uint64(1<<32 - 1)
	enterpriseExecutionLocalNative       = "local-native"
	enterpriseExecutionCapability        = "capability://leanctx/context-optimization"
	enterpriseExecutionCapabilityVersion = "1.0.0"
	enterpriseExecutionInputPrefix       = "input:source-materialization-sha256:"
	enterpriseExecutionSourcePlanPrefix  = "artifact://execution/evidence/"
	enterpriseExecutionTaskPrefix        = "task:sha256:"
	enterpriseExecutionPlanPrefix        = "plan:sha256:"
)

var enterpriseExecutionSemver = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)

// EngineSourceExecutionV2Request contains caller-owned task and local-native
// plan data plus the already-bound source materialization request.
type EngineSourceExecutionV2Request struct {
	Task            map[string]any
	Plan            map[string]any
	Materialization EngineSourceMaterializationRequest
}

// EngineSourceExecutionV2Response is a validated v2 execution projection.
// ReceiptDocumentJSON and ReceiptDocumentBytes are an opaque exact UTF-8
// carrier; the SDK does not verify a signer or claim acceptance.
type EngineSourceExecutionV2Response struct {
	SchemaVersion        uint64
	TenantID             string
	GovernanceRevision   uint64
	Execution            map[string]any
	ReceiptDocumentJSON  string
	ReceiptDocumentBytes []byte
	RawResponse          map[string]any
}

// ContextExecuteV2 performs one bounded authenticated local-native execution.
func (c *EnterpriseEngineClient) ContextExecuteV2(request EngineSourceExecutionV2Request) (*EngineSourceExecutionV2Response, error) {
	return c.ContextExecuteV2Context(context.Background(), request)
}

// ContextExecuteV2Context is the cancellable form of ContextExecuteV2.
func (c *EnterpriseEngineClient) ContextExecuteV2Context(parent context.Context, request EngineSourceExecutionV2Request) (*EngineSourceExecutionV2Response, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseEngineClient is not initialized")
	}
	planning, sourceIDs, bindingDigest, err := request.Materialization.checked()
	if err != nil {
		return nil, err
	}
	task, plan, err := enterpriseExecutionInput(request.Task, request.Plan, planning, c.tenantID)
	if err != nil {
		return nil, err
	}
	payloadValue := map[string]any{
		"task": task,
		"plan": plan,
		"materialization": map[string]any{
			"planning":                     planning.ToDict(),
			"source_ids":                   sourceIDs,
			"expected_governance_revision": request.Materialization.ExpectedGovernanceRevision,
			"expected_binding_digest":      bindingDigest,
		},
	}
	materialization := payloadValue["materialization"].(map[string]any)
	if request.Materialization.PlanningEvaluationTime != nil {
		materialization["planning_evaluation_time"] = *request.Materialization.PlanningEvaluationTime
	}
	payload, err := canonicalJSON(payloadValue)
	if err != nil || len(payload) > enterpriseMaxExecutionRequestBytes {
		return nil, NewValidationError("Enterprise Engine source execution request exceeds its byte bound")
	}
	raw, err := c.transport.postJSONContext(
		parent,
		enterpriseContextExecuteV2Path,
		payload,
		enterpriseMaxExecutionTotalBytes,
		"Enterprise Engine source execution v2",
		"source execution v2 request",
	)
	if err != nil {
		return nil, err
	}
	return c.parseExecutionV2Response(
		raw,
		planning,
		sourceIDs,
		task,
		plan,
		request.Materialization.ExpectedGovernanceRevision,
		bindingDigest,
		request.Materialization.PlanningEvaluationTime,
	)
}

func enterpriseExecutionInput(task, plan map[string]any, request EnginePlanningRequest, tenantID string) (map[string]any, map[string]any, error) {
	validatedTask, err := enterpriseExecutionTask(task, request, tenantID, false)
	if err != nil {
		return nil, nil, err
	}
	validatedPlan, err := enterpriseExecutionPlan(plan, request.TaskID, false, false)
	if err != nil {
		return nil, nil, err
	}
	// Validate reserved integer fields before canonicalization can normalize
	// decimal/exponent JSON numbers into lexical integers.
	normalizedTask, err := enterpriseExecutionCanonicalObject(validatedTask, "task")
	if err != nil {
		return nil, nil, err
	}
	normalizedPlan, err := enterpriseExecutionCanonicalObject(validatedPlan, "plan")
	if err != nil {
		return nil, nil, err
	}
	if normalizedPlan["provider"] != enterpriseExecutionLocalNative || normalizedPlan["model"] != enterpriseExecutionLocalNative {
		return nil, nil, NewValidationError("context_execute requires a local-native plan")
	}
	capabilities, ok := normalizedPlan["capability_ids"].([]any)
	if !ok || len(capabilities) != 1 || capabilities[0] != enterpriseExecutionCapability {
		return nil, nil, NewValidationError("context_execute requires the local-native capability")
	}
	bindings, ok := normalizedPlan["capability_bindings"].([]any)
	if !ok || len(bindings) != 1 {
		return nil, nil, NewValidationError("context_execute requires a bound local-native capability")
	}
	binding, ok := bindings[0].(map[string]any)
	if !ok || len(binding) != 2 || binding["capability_id"] != enterpriseExecutionCapability || binding["version"] != enterpriseExecutionCapabilityVersion {
		return nil, nil, NewValidationError("context_execute requires a bound local-native capability")
	}
	return normalizedTask, normalizedPlan, nil
}

func enterpriseExecutionCanonicalObject(value map[string]any, field string) (map[string]any, error) {
	encoded, err := canonicalJSON(value)
	if err != nil {
		return nil, NewValidationError(field + " contains non-canonical JSON data")
	}
	decoded, err := strictJSONLoads(encoded, field)
	if err != nil {
		return nil, NewValidationError(field + " contains non-canonical JSON data")
	}
	return enterpriseObject(decoded, field)
}

func enterpriseExecutionTask(raw map[string]any, request EnginePlanningRequest, tenantID string, protocol bool) (map[string]any, error) {
	required := map[string]bool{
		"schema_version": true, "task_id": true, "trace_id": true, "project_id": true,
		"session_id": true, "agent_id": true, "complexity": true, "created_at": true,
	}
	if err := enterpriseExecutionFields(raw, nil, required, "task", protocol); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionVersion(raw["schema_version"], "task.schema_version", protocol); err != nil {
		return nil, err
	}
	taskID, err := enterpriseExecutionText(raw["task_id"], "task.task_id", enterpriseMaxReferenceBytes, protocol, true, false)
	if err != nil {
		return nil, err
	}
	if taskID != request.TaskID {
		return nil, enterpriseExecutionFail("task.task_id does not bind the planning request", protocol)
	}
	for _, field := range []string{"trace_id", "project_id", "session_id", "agent_id", "created_at"} {
		if _, err := enterpriseExecutionText(raw[field], "task."+field, enterpriseMaxReferenceBytes, protocol, true, false); err != nil {
			return nil, err
		}
	}
	if err := enterpriseExecutionEnum(raw["complexity"], "task.complexity", map[string]bool{"unknown": true, "low": true, "medium": true, "high": true, "critical": true}, protocol); err != nil {
		return nil, err
	}
	tenant, ok := raw["tenant_id"]
	if !ok || tenant != tenantID {
		return nil, enterpriseExecutionFail("task.tenant_id does not bind the authenticated tenant", protocol)
	}
	if parent, present := raw["parent_task_id"]; present && parent != nil {
		parentID, parentErr := enterpriseExecutionText(parent, "task.parent_task_id", enterpriseMaxReferenceBytes, protocol, true, false)
		if parentErr != nil {
			return nil, parentErr
		}
		if parentID == taskID {
			return nil, enterpriseExecutionFail("task cannot be its own parent", protocol)
		}
	}
	for _, field := range []string{"intent", "task_class", "region_policy_ref", "model_policy_ref", "context_state_ref", "outcome_contract_ref"} {
		if value, present := raw[field]; present && value != nil {
			if _, textErr := enterpriseExecutionText(value, "task."+field, enterpriseMaxReferenceBytes, protocol, true, false); textErr != nil {
				return nil, textErr
			}
		}
	}
	if value, present := raw["risk_class"]; present && value != nil {
		if err := enterpriseExecutionEnum(value, "task.risk_class", map[string]bool{"low": true, "medium": true, "high": true, "critical": true}, protocol); err != nil {
			return nil, err
		}
	}
	if value, present := raw["quality_requirement_milli"]; present && value != nil {
		if _, err := enterpriseExecutionBoundedU64(value, "task.quality_requirement_milli", 1000, protocol); err != nil {
			return nil, err
		}
	}
	for _, field := range []string{"cost_budget_micros", "latency_budget_ms"} {
		if value, present := raw[field]; present && value != nil {
			if _, err := enterpriseExecutionBoundedU64(value, "task."+field, ^uint64(0), protocol); err != nil {
				return nil, err
			}
		}
	}
	if value, present := raw["data_classification"]; present && value != nil {
		if err := enterpriseExecutionEnum(value, "task.data_classification", map[string]bool{"Public": true, "Internal": true, "Confidential": true, "Restricted": true}, protocol); err != nil {
			return nil, err
		}
	}
	result := enterpriseExecutionDropNulls(raw, map[string]bool{"parent_task_id": true, "tenant_id": true, "intent": true, "task_class": true, "risk_class": true, "quality_requirement_milli": true, "cost_budget_micros": true, "latency_budget_ms": true, "data_classification": true, "region_policy_ref": true, "model_policy_ref": true, "context_state_ref": true, "outcome_contract_ref": true})
	if encoded, encodeErr := canonicalJSON(result); encodeErr != nil || len(encoded) > enterpriseMaxExecutionTaskBytes {
		return nil, enterpriseExecutionFail("task exceeds its byte bound", protocol)
	}
	return result, nil
}

func enterpriseExecutionPlan(raw map[string]any, taskID string, protocol, allowContextPlanID bool) (map[string]any, error) {
	required := map[string]bool{
		"schema_version": true, "plan_id": true, "task_id": true, "context_budget_tokens": true,
		"context_strategy": true, "knowledge_refs": true, "capability_ids": true, "model": true,
		"provider": true, "reasoning_allocation_milli": true, "max_retries": true,
		"fallback_refs": true, "stop_condition": true, "expected_cost_micros": true,
		"expected_quality_milli": true, "expected_latency_ms": true,
	}
	if err := enterpriseExecutionFields(raw, nil, required, "plan", protocol); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionVersion(raw["schema_version"], "plan.schema_version", protocol); err != nil {
		return nil, err
	}
	planTaskID, err := enterpriseExecutionText(raw["task_id"], "plan.task_id", enterpriseMaxReferenceBytes, protocol, true, false)
	if err != nil {
		return nil, err
	}
	if planTaskID != taskID {
		return nil, enterpriseExecutionFail("plan.task_id does not bind task.task_id", protocol)
	}
	for _, field := range []string{"plan_id", "model", "provider"} {
		if _, textErr := enterpriseExecutionText(raw[field], "plan."+field, enterpriseMaxReferenceBytes, protocol, true, false); textErr != nil {
			return nil, textErr
		}
	}
	if _, err := enterpriseExecutionBoundedU64(raw["context_budget_tokens"], "plan.context_budget_tokens", ^uint64(0), protocol); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionEnum(raw["context_strategy"], "plan.context_strategy", map[string]bool{"minimal": true, "balanced": true, "comprehensive": true, "cached_first": true}, protocol); err != nil {
		return nil, err
	}
	_, err = enterpriseExecutionStringList(raw["knowledge_refs"], "plan.knowledge_refs", protocol)
	if err != nil {
		return nil, err
	}
	capabilities, err := enterpriseExecutionStringList(raw["capability_ids"], "plan.capability_ids", protocol)
	if err != nil {
		return nil, err
	}
	if len(capabilities) == 0 {
		return nil, enterpriseExecutionFail("plan.capability_ids must not be empty", protocol)
	}
	if _, err := enterpriseExecutionBoundedU64(raw["reasoning_allocation_milli"], "plan.reasoning_allocation_milli", 1000, protocol); err != nil {
		return nil, err
	}
	if _, err := enterpriseExecutionBoundedU64(raw["max_retries"], "plan.max_retries", enterpriseMaxExecutionU32, protocol); err != nil {
		return nil, err
	}
	_, err = enterpriseExecutionStringList(raw["fallback_refs"], "plan.fallback_refs", protocol)
	if err != nil {
		return nil, err
	}
	if err := enterpriseExecutionEnum(raw["stop_condition"], "plan.stop_condition", map[string]bool{"on_completion": true, "on_acceptance": true, "on_budget_exhaustion": true, "on_error": true, "manual": true}, protocol); err != nil {
		return nil, err
	}
	for _, field := range []string{"expected_cost_micros", "expected_latency_ms"} {
		if _, err := enterpriseExecutionBoundedU64(raw[field], "plan."+field, ^uint64(0), protocol); err != nil {
			return nil, err
		}
	}
	if _, err := enterpriseExecutionBoundedU64(raw["expected_quality_milli"], "plan.expected_quality_milli", 1000, protocol); err != nil {
		return nil, err
	}
	if value, present := raw["context_plan_id"]; present && value != nil {
		if !allowContextPlanID {
			return nil, enterpriseExecutionFail("source execution request plan must not contain context_plan_id", protocol)
		}
		if _, err := enterpriseExecutionText(value, "plan.context_plan_id", enterpriseMaxReferenceBytes, protocol, true, false); err != nil {
			return nil, err
		}
	}
	budget, _ := enterpriseExecutionBoundedU64(raw["context_budget_tokens"], "plan.context_budget_tokens", ^uint64(0), protocol)
	if value, present := raw["context_budget_policy"]; present && value != nil {
		policy, policyErr := enterpriseExecutionObject(value, "plan.context_budget_policy", protocol)
		if policyErr != nil {
			return nil, policyErr
		}
		kind, _ := policy["kind"].(string)
		switch kind {
		case "token_limit":
			if err := enterpriseExecutionFields(policy, map[string]bool{"kind": true, "tokens": true}, map[string]bool{"kind": true, "tokens": true}, "plan.context_budget_policy", protocol); err != nil {
				return nil, err
			}
			tokens, tokenErr := enterpriseExecutionBoundedU64(policy["tokens"], "plan.context_budget_policy.tokens", ^uint64(0), protocol)
			if tokenErr != nil {
				return nil, tokenErr
			}
			if tokens != budget {
				return nil, enterpriseExecutionFail("plan.context_budget_policy disagrees with context_budget_tokens", protocol)
			}
		case "no_token_limit":
			if err := enterpriseExecutionFields(policy, map[string]bool{"kind": true}, map[string]bool{"kind": true}, "plan.context_budget_policy", protocol); err != nil {
				return nil, err
			}
			if budget != 0 {
				return nil, enterpriseExecutionFail("plan.context_budget_policy disagrees with context_budget_tokens", protocol)
			}
		default:
			return nil, enterpriseExecutionFail("plan.context_budget_policy.kind is unsupported", protocol)
		}
	}
	if value, present := raw["estimates"]; present && value != nil {
		estimates, estimateErr := enterpriseExecutionObject(value, "plan.estimates", protocol)
		if estimateErr != nil {
			return nil, estimateErr
		}
		if err := enterpriseExecutionFields(estimates, map[string]bool{"cost_micros": true, "quality_milli": true, "latency_ms": true}, map[string]bool{"cost_micros": true, "quality_milli": true, "latency_ms": true}, "plan.estimates", protocol); err != nil {
			return nil, err
		}
		for _, field := range []string{"cost_micros", "quality_milli", "latency_ms"} {
			if estimate := estimates[field]; estimate != nil {
				maximum := uint64(^uint64(0))
				if field == "quality_milli" {
					maximum = 1000
				}
				if _, estimateErr := enterpriseExecutionBoundedU64(estimate, "plan.estimates."+field, maximum, protocol); estimateErr != nil {
					return nil, estimateErr
				}
			}
		}
		if estimates["cost_micros"] != nil && !enterpriseExecutionValuesEqual(estimates["cost_micros"], raw["expected_cost_micros"]) {
			return nil, enterpriseExecutionFail("plan.estimates.cost_micros disagrees with legacy scalar", protocol)
		}
		if estimates["quality_milli"] != nil && !enterpriseExecutionValuesEqual(estimates["quality_milli"], raw["expected_quality_milli"]) {
			return nil, enterpriseExecutionFail("plan.estimates.quality_milli disagrees with legacy scalar", protocol)
		}
		if estimates["latency_ms"] != nil && !enterpriseExecutionValuesEqual(estimates["latency_ms"], raw["expected_latency_ms"]) {
			return nil, enterpriseExecutionFail("plan.estimates.latency_ms disagrees with legacy scalar", protocol)
		}
	}
	for _, field := range []string{"policy_decision_ref", "scheduler_decision_ref", "executor_agent_id"} {
		if value, present := raw[field]; present && value != nil {
			if _, textErr := enterpriseExecutionText(value, "plan."+field, enterpriseMaxReferenceBytes, protocol, true, false); textErr != nil {
				return nil, textErr
			}
		}
	}
	if value, present := raw["capability_bindings"]; present && value != nil {
		bindings, bindingsOK := value.([]any)
		if !bindingsOK || len(bindings) != len(capabilities) {
			return nil, enterpriseExecutionFail("plan.capability_bindings has an invalid shape", protocol)
		}
		seen := make(map[string]bool, len(bindings))
		for index, value := range bindings {
			binding, bindingErr := enterpriseExecutionObject(value, "plan.capability_bindings["+strconv.Itoa(index)+"]", protocol)
			if bindingErr != nil {
				return nil, bindingErr
			}
			if err := enterpriseExecutionFields(binding, map[string]bool{"capability_id": true, "version": true}, map[string]bool{"capability_id": true, "version": true}, "plan.capability_bindings entry", protocol); err != nil {
				return nil, err
			}
			capabilityID, capabilityErr := enterpriseExecutionText(binding["capability_id"], "plan.capability_bindings.capability_id", enterpriseMaxReferenceBytes, protocol, true, false)
			if capabilityErr != nil {
				return nil, capabilityErr
			}
			version, versionErr := enterpriseExecutionText(binding["version"], "plan.capability_bindings.version", enterpriseMaxReferenceBytes, protocol, true, false)
			if versionErr != nil {
				return nil, versionErr
			}
			if !containsString(capabilities, capabilityID) || seen[capabilityID] || !enterpriseExecutionSemver.MatchString(version) {
				return nil, enterpriseExecutionFail("plan.capability_bindings does not match capability_ids", protocol)
			}
			seen[capabilityID] = true
		}
	}
	result := enterpriseExecutionDropNulls(raw, map[string]bool{"context_plan_id": true, "context_budget_policy": true, "estimates": true, "policy_decision_ref": true, "scheduler_decision_ref": true, "executor_agent_id": true, "capability_bindings": true})
	if !allowContextPlanID {
		delete(result, "context_plan_id")
	}
	if _, err := canonicalJSON(result); err != nil {
		return nil, enterpriseExecutionFail("plan contains non-canonical JSON data", protocol)
	}
	return result, nil
}

func (c *EnterpriseEngineClient) parseExecutionV2Response(
	raw []byte,
	request EnginePlanningRequest,
	sourceIDs []string,
	task map[string]any,
	plan map[string]any,
	expectedGovernanceRevision uint64,
	expectedBindingDigest string,
	planningEvaluationTime *string,
) (*EngineSourceExecutionV2Response, error) {
	if len(raw) > enterpriseMaxExecutionTotalBytes {
		return nil, enterpriseProtocol("Enterprise Engine source execution v2 response exceeds its byte bound")
	}
	if !contextReadUnicodeEscapesValid(raw) {
		return nil, enterpriseProtocol("Enterprise Engine source execution v2 response has an unpaired Unicode surrogate")
	}
	decoded, err := strictJSONLoads(raw, "Enterprise Engine source execution v2 response")
	if err != nil {
		return nil, NewEngineProtocolError("Enterprise Engine source execution v2 response is not valid JSON")
	}
	wrapper, err := enterpriseObject(decoded, "Enterprise Engine source execution v2 response")
	if err != nil {
		return nil, err
	}
	if err := enterpriseExecutionFields(wrapper, map[string]bool{
		"schema_version": true, "tenant_id": true, "governance_revision": true, "execution": true,
	}, map[string]bool{
		"schema_version": true, "tenant_id": true, "governance_revision": true, "execution": true,
	}, "Enterprise source execution v2 response", true); err != nil {
		return nil, err
	}
	schemaVersion, err := enterpriseExecutionBoundedU64(wrapper["schema_version"], "response.schema_version", enterpriseExecutionV2SchemaVersion, true)
	if err != nil || schemaVersion != enterpriseExecutionV2SchemaVersion {
		return nil, enterpriseProtocol("Enterprise source execution v2 response schema_version is unsupported")
	}
	tenantID, err := enterpriseUUIDValue(wrapper["tenant_id"], "response.tenant_id")
	if err != nil {
		return nil, err
	}
	if tenantID != c.tenantID {
		return nil, enterpriseProtocol("Enterprise source execution v2 tenant binding does not match")
	}
	governanceRevision, err := enterpriseExecutionBoundedU64(wrapper["governance_revision"], "response.governance_revision", ^uint64(0), true)
	if err != nil {
		return nil, err
	}
	if governanceRevision != expectedGovernanceRevision {
		return nil, enterpriseProtocol("Enterprise source execution v2 governance revision does not match")
	}
	executionV2, err := enterpriseExecutionObject(wrapper["execution"], "execution", true)
	if err != nil {
		return nil, err
	}
	if err := enterpriseExecutionFields(executionV2, map[string]bool{
		"schema_version": true, "execution": true, "receipt_document_json": true,
	}, map[string]bool{
		"schema_version": true, "execution": true, "receipt_document_json": true,
	}, "Enterprise source execution v2", true); err != nil {
		return nil, err
	}
	executionSchema, err := enterpriseExecutionBoundedU64(executionV2["schema_version"], "execution.schema_version", enterpriseExecutionV2SchemaVersion, true)
	if err != nil || executionSchema != enterpriseExecutionV2SchemaVersion {
		return nil, enterpriseProtocol("Enterprise source execution v2 execution schema_version is unsupported")
	}
	receiptDocument, err := enterpriseExecutionParseReceiptDocument(executionV2["receipt_document_json"])
	if err != nil {
		return nil, err
	}
	nested, err := enterpriseExecutionObject(executionV2["execution"], "execution.execution", true)
	if err != nil {
		return nil, err
	}
	parsedExecution, err := enterpriseExecutionV1(
		nested,
		request,
		sourceIDs,
		task,
		plan,
		expectedBindingDigest,
		planningEvaluationTime,
	)
	if err != nil {
		return nil, err
	}
	receiptDigest := parsedExecution["canonical_receipt"].(map[string]any)["receipt_digest"].(string)
	if sha256Hex(receiptDocument) != receiptDigest {
		return nil, enterpriseProtocol("Enterprise source execution v2 receipt document digest does not match")
	}
	innerBytes, err := canonicalJSON(map[string]any{
		"schema_version": executionSchema, "execution": parsedExecution, "receipt_document_json": string(receiptDocument),
	})
	if err != nil || len(innerBytes) > enterpriseMaxExecutionResponseBytes {
		return nil, enterpriseProtocol("Enterprise source execution v2 response exceeds its byte bound")
	}
	return &EngineSourceExecutionV2Response{
		SchemaVersion: schemaVersion, TenantID: tenantID, GovernanceRevision: governanceRevision,
		Execution: parsedExecution, ReceiptDocumentJSON: string(receiptDocument),
		ReceiptDocumentBytes: append([]byte(nil), receiptDocument...), RawResponse: wrapper,
	}, nil
}

func enterpriseExecutionV1(
	raw map[string]any,
	request EnginePlanningRequest,
	sourceIDs []string,
	task map[string]any,
	plan map[string]any,
	expectedBindingDigest string,
	planningEvaluationTime *string,
) (map[string]any, error) {
	if err := enterpriseExecutionFields(raw, map[string]bool{
		"schema_version": true, "transport_version": true, "engine_interface_version": true,
		"source_plan": true, "execution_plan": true, "view": true, "invocation": true,
		"observation": true, "canonical_receipt": true,
	}, map[string]bool{
		"schema_version": true, "transport_version": true, "engine_interface_version": true,
		"source_plan": true, "execution_plan": true, "view": true, "invocation": true,
		"observation": true, "canonical_receipt": true,
	}, "Enterprise source execution", true); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionVersion(raw["schema_version"], "execution.schema_version", true); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionVersion(raw["transport_version"], "execution.transport_version", true); err != nil {
		return nil, err
	}
	interfaceVersion, err := enterpriseExecutionText(raw["engine_interface_version"], "execution.engine_interface_version", enterpriseMaxIdentifierBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	if interfaceVersion != EngineInterfaceVersion {
		return nil, enterpriseProtocol("Enterprise source execution Engine interface is unsupported")
	}
	if err := enterpriseExecutionLexicalV1(raw); err != nil {
		return nil, err
	}
	sourcePlanRaw, err := enterpriseExecutionObject(raw["source_plan"], "execution.source_plan", true)
	if err != nil {
		return nil, err
	}
	sourcePlan, err := enterpriseSourcePlan(sourcePlanRaw, request)
	if err != nil {
		return nil, enterpriseProtocol("Enterprise source execution source plan is invalid")
	}
	if err := enterpriseSourceScope(sourcePlan, sourceIDs); err != nil {
		return nil, err
	}
	if sourcePlan["binding_digest"] != expectedBindingDigest {
		return nil, enterpriseProtocol("Enterprise source execution binding digest does not match")
	}
	executionPlanRaw, err := enterpriseExecutionObject(raw["execution_plan"], "execution.execution_plan", true)
	if err != nil {
		return nil, err
	}
	executionPlan, err := enterpriseExecutionPlan(executionPlanRaw, request.TaskID, true, true)
	if err != nil {
		return nil, err
	}
	sourceResult := sourcePlan["result"].(map[string]any)
	sourceResultPlan := sourceResult["plan"].(map[string]any)
	contextPlanID, contextPlanOK := sourceResultPlan["context_plan_id"].(string)
	if !contextPlanOK {
		return nil, enterpriseProtocol("Enterprise source execution source plan context_plan_id is missing")
	}
	executionContextPlanID, executionContextOK := executionPlan["context_plan_id"].(string)
	if !executionContextOK || executionContextPlanID != contextPlanID {
		return nil, enterpriseProtocol("Enterprise source execution plan does not bind source plan")
	}
	sourceBudget, sourceBudgetOK := sourceResultPlan["budget_tokens"].(uint64)
	executionBudget, executionBudgetErr := enterpriseExecutionBoundedU64(executionPlan["context_budget_tokens"], "execution_plan.context_budget_tokens", ^uint64(0), true)
	if !sourceBudgetOK || executionBudgetErr != nil || sourceBudget != executionBudget {
		return nil, enterpriseProtocol("Enterprise source execution budget does not bind source plan")
	}
	if err := enterpriseExecutionLocalPlan(executionPlan); err != nil {
		return nil, err
	}
	expectedPlan := enterpriseExecutionCopyMap(plan)
	expectedPlan["context_plan_id"] = contextPlanID
	if _, present := expectedPlan["context_autopilot_decision_ref"]; !present {
		decision, decisionOK := executionPlan["context_autopilot_decision_ref"].(string)
		if !decisionOK || decision == "" {
			return nil, enterpriseProtocol("Enterprise source execution decision reference is missing")
		}
		expectedPlan["context_autopilot_decision_ref"] = decision
	}
	if !enterpriseExecutionValuesEqual(expectedPlan, executionPlan) {
		return nil, enterpriseProtocol("Enterprise source execution changed the declared plan")
	}
	if planningEvaluationTime != nil {
		marker, markerOK := sourceResultPlan["context_plan_evaluation_v1"].(map[string]any)
		actual, actualOK := marker["evaluation_time"].(string)
		if !markerOK || !actualOK || actual != *planningEvaluationTime {
			return nil, enterpriseProtocol("Enterprise source execution changed the evaluation time")
		}
	}
	parsedView, err := parseView(raw["view"])
	if err != nil {
		return nil, err
	}
	if len([]byte(parsedView.Text)) > enterpriseMaxExecutionViewBytes {
		return nil, enterpriseProtocol("Enterprise source execution view exceeds its byte bound")
	}
	view := map[string]any{
		"text":          parsedView.Text,
		"output_ref":    pointerOrNil(parsedView.OutputRef),
		"output_digest": pointerOrNil(parsedView.OutputDigest),
	}
	if parsedView.OutputRef == nil || parsedView.OutputDigest == nil {
		return nil, enterpriseProtocol("Enterprise source execution view is missing output binding")
	}
	invocation, err := parseInvocation(raw["invocation"])
	if err != nil {
		return nil, err
	}
	policy, policyOK := invocation["policy_admission"].(map[string]any)
	if !policyOK || policy["decision"] != "admitted" {
		return nil, enterpriseProtocol("Enterprise source execution policy was not admitted")
	}
	invocationID, invocationIDOK := invocation["invocation_id"].(string)
	if !invocationIDOK {
		return nil, enterpriseProtocol("Enterprise source execution invocation_id is invalid")
	}
	observation, err := parseObservation(raw["observation"], invocationID)
	if err != nil {
		return nil, err
	}
	if observation["status"] != "succeeded" {
		return nil, enterpriseProtocol("Enterprise source execution observation did not succeed")
	}
	lineage, lineageOK := observation["source_lineage"].([]string)
	invocationRefs, invocationRefsOK := invocation["source_refs"].([]string)
	if !lineageOK || !invocationRefsOK || !enterpriseExecutionStringSequenceEqual(lineage, invocationRefs) {
		return nil, enterpriseProtocol("Enterprise source execution lineage does not bind invocation")
	}
	if !enterpriseExecutionValuesEqual(observation["output_ref"], view["output_ref"]) || !enterpriseExecutionValuesEqual(observation["output_digest"], view["output_digest"]) {
		return nil, enterpriseProtocol("Enterprise source execution view does not bind observation")
	}
	sourcePlanDigest, err := canonicalDigest(sourcePlanRaw)
	if err != nil {
		return nil, enterpriseProtocol("Enterprise source execution source plan digest is invalid")
	}
	executionPlanDigest, err := canonicalDigest(executionPlanRaw)
	if err != nil {
		return nil, enterpriseProtocol("Enterprise source execution plan digest is invalid")
	}
	taskDigest, err := canonicalDigest(task)
	if err != nil {
		return nil, enterpriseProtocol("Enterprise source execution task digest is invalid")
	}
	if err := enterpriseExecutionLineage(invocation, sourcePlanDigest, executionPlanDigest, taskDigest); err != nil {
		return nil, err
	}
	receipt, err := enterpriseExecutionCanonicalReceipt(raw["canonical_receipt"])
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"schema_version": enterpriseSchemaVersion, "transport_version": enterpriseTransportVersion,
		"engine_interface_version": EngineInterfaceVersion, "source_plan": sourcePlan,
		"execution_plan": executionPlan, "view": view, "invocation": invocation,
		"observation": observation, "canonical_receipt": receipt,
	}, nil
}

func enterpriseExecutionFail(message string, protocol bool) error {
	if protocol {
		return enterpriseProtocol(message)
	}
	return NewValidationError(message)
}

func enterpriseExecutionFields(value map[string]any, allowed, required map[string]bool, field string, protocol bool) error {
	if allowed != nil {
		for key := range value {
			if !allowed[key] {
				return enterpriseExecutionFail(field+" fields do not match the v2 contract", protocol)
			}
		}
	}
	for key := range required {
		if _, present := value[key]; !present {
			return enterpriseExecutionFail(field+" is missing a required field", protocol)
		}
	}
	return nil
}

func enterpriseExecutionObject(value any, field string, protocol bool) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, enterpriseExecutionFail(field+" must be an object", protocol)
	}
	return object, nil
}

func enterpriseExecutionText(value any, field string, maximum int, protocol, nonblank, printable bool) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", enterpriseExecutionFail(field+" must be a string", protocol)
	}
	if !utf8.ValidString(text) || len([]byte(text)) == 0 || len([]byte(text)) > maximum || strings.IndexByte(text, 0) >= 0 {
		return "", enterpriseExecutionFail(field+" exceeds its UTF-8 byte bound", protocol)
	}
	if nonblank && strings.TrimSpace(text) == "" {
		return "", enterpriseExecutionFail(field+" must not be blank", protocol)
	}
	for _, character := range text {
		if unicode.IsControl(character) {
			return "", enterpriseExecutionFail(field+" contains a control character", protocol)
		}
	}
	if printable {
		for _, character := range text {
			if character < 0x20 || character > 0x7e {
				return "", enterpriseExecutionFail(field+" must be printable ASCII", protocol)
			}
		}
	}
	return text, nil
}

func enterpriseExecutionBoundedU64(value any, field string, maximum uint64, protocol bool) (uint64, error) {
	var parsed uint64
	var err error
	switch item := value.(type) {
	case json.Number:
		parsed, err = enterpriseU64(item, field)
	case uint64:
		parsed = item
	case uint32:
		parsed = uint64(item)
	case uint:
		parsed = uint64(item)
	case int:
		if item < 0 {
			err = enterpriseProtocol(field + " must be an unsigned integer")
		} else {
			parsed = uint64(item)
		}
	case int64:
		if item < 0 {
			err = enterpriseProtocol(field + " must be an unsigned integer")
		} else {
			parsed = uint64(item)
		}
	default:
		err = enterpriseProtocol(field + " must be an unsigned integer")
	}
	if err != nil || parsed > maximum {
		return 0, enterpriseExecutionFail(field+" is outside its protocol bounds", protocol)
	}
	return parsed, nil
}

func enterpriseExecutionVersion(value any, field string, protocol bool) error {
	version, err := enterpriseExecutionBoundedU64(value, field, enterpriseSchemaVersion, protocol)
	if err != nil {
		return err
	}
	if version != enterpriseSchemaVersion {
		return enterpriseExecutionFail(field+" is unsupported", protocol)
	}
	return nil
}

func enterpriseExecutionEnum(value any, field string, allowed map[string]bool, protocol bool) error {
	text, err := enterpriseExecutionText(value, field, enterpriseMaxIdentifierBytes, protocol, true, true)
	if err != nil {
		return err
	}
	if !allowed[text] {
		return enterpriseExecutionFail(field+" has an unsupported value", protocol)
	}
	return nil
}

func enterpriseExecutionStringList(value any, field string, protocol bool) ([]any, error) {
	values, ok := value.([]any)
	if !ok || len(values) > enterpriseMaxPlanItems {
		return nil, enterpriseExecutionFail(field+" has an invalid shape", protocol)
	}
	result := make([]any, len(values))
	seen := make(map[string]bool, len(values))
	for index, item := range values {
		text, err := enterpriseExecutionText(item, field+"["+strconv.Itoa(index)+"]", enterpriseMaxReferenceBytes, protocol, true, false)
		if err != nil {
			return nil, err
		}
		if seen[text] {
			return nil, enterpriseExecutionFail(field+" contains duplicates", protocol)
		}
		seen[text] = true
		result[index] = text
	}
	return result, nil
}

func enterpriseExecutionValuesEqual(left, right any) bool {
	leftBytes, leftErr := canonicalJSON(left)
	rightBytes, rightErr := canonicalJSON(right)
	return leftErr == nil && rightErr == nil && bytes.Equal(leftBytes, rightBytes)
}

func enterpriseExecutionStringSequenceEqual(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func enterpriseExecutionDropNulls(value map[string]any, optional map[string]bool) map[string]any {
	result := make(map[string]any, len(value))
	for key, nested := range value {
		if optional[key] && nested == nil {
			continue
		}
		result[key] = nested
	}
	return result
}

func enterpriseExecutionCopyMap(value map[string]any) map[string]any {
	result := make(map[string]any, len(value))
	for key, nested := range value {
		result[key] = nested
	}
	return result
}

func enterpriseExecutionParseReceiptDocument(value any) ([]byte, error) {
	text, ok := value.(string)
	if !ok || !utf8.ValidString(text) {
		return nil, enterpriseProtocol("Enterprise source execution receipt document must be valid UTF-8 text")
	}
	bytesValue := []byte(text)
	if len(bytesValue) > enterpriseMaxExecutionReceiptBytes {
		return nil, enterpriseProtocol("Enterprise source execution receipt document exceeds its byte bound")
	}
	return bytesValue, nil
}

func enterpriseExecutionLocalPlan(plan map[string]any) error {
	if plan["provider"] != enterpriseExecutionLocalNative || plan["model"] != enterpriseExecutionLocalNative {
		return enterpriseProtocol("Enterprise source execution plan is not local-native")
	}
	capabilities, ok := plan["capability_ids"].([]any)
	if !ok || len(capabilities) != 1 || capabilities[0] != enterpriseExecutionCapability {
		return enterpriseProtocol("Enterprise source execution capability binding is invalid")
	}
	bindings, ok := plan["capability_bindings"].([]any)
	if !ok || len(bindings) != 1 {
		return enterpriseProtocol("Enterprise source execution capability binding is invalid")
	}
	binding, ok := bindings[0].(map[string]any)
	if !ok || len(binding) != 2 || binding["capability_id"] != enterpriseExecutionCapability || binding["version"] != enterpriseExecutionCapabilityVersion {
		return enterpriseProtocol("Enterprise source execution capability binding is invalid")
	}
	return nil
}

func enterpriseExecutionLexicalInteger(value any, field string, signed bool) error {
	number, ok := value.(json.Number)
	if !ok {
		return nil
	}
	lexical := number.String()
	if lexical == "" {
		return enterpriseProtocol(field + " must use canonical integer syntax")
	}
	if signed && lexical[0] == '-' {
		lexical = lexical[1:]
		if lexical == "" {
			return enterpriseProtocol(field + " must use canonical integer syntax")
		}
	}
	if len(lexical) > 1 && lexical[0] == '0' {
		return enterpriseProtocol(field + " must use canonical integer syntax")
	}
	for _, character := range lexical {
		if character < '0' || character > '9' {
			return enterpriseProtocol(field + " must use canonical integer syntax")
		}
	}
	return nil
}

func enterpriseExecutionLexicalField(value map[string]any, key, field string, signed bool) error {
	nested, present := value[key]
	if !present || nested == nil {
		return nil
	}
	return enterpriseExecutionLexicalInteger(nested, field, signed)
}

func enterpriseExecutionLexicalObject(value map[string]any, key, field string) map[string]any {
	nested, _ := value[key].(map[string]any)
	_ = field
	return nested
}

func enterpriseExecutionLexicalV1(raw map[string]any) error {
	if err := enterpriseExecutionLexicalField(raw, "schema_version", "execution.schema_version", false); err != nil {
		return err
	}
	if err := enterpriseExecutionLexicalField(raw, "transport_version", "execution.transport_version", false); err != nil {
		return err
	}
	sourcePlan := enterpriseExecutionLexicalObject(raw, "source_plan", "execution.source_plan")
	if sourcePlan != nil {
		result := enterpriseExecutionLexicalObject(sourcePlan, "result", "execution.source_plan.result")
		if result != nil {
			for _, field := range []string{"schema_version", "transport_version"} {
				if err := enterpriseExecutionLexicalField(result, field, "execution.source_plan.result."+field, false); err != nil {
					return err
				}
			}
			resultPlan := enterpriseExecutionLexicalObject(result, "plan", "execution.source_plan.result.plan")
			if resultPlan != nil {
				for _, field := range []string{"schema_version", "budget_tokens"} {
					if err := enterpriseExecutionLexicalField(resultPlan, field, "execution.source_plan.result.plan."+field, false); err != nil {
						return err
					}
				}
				if selections, ok := resultPlan["selections"].([]any); ok {
					for index, item := range selections {
						if selection, ok := item.(map[string]any); ok {
							if err := enterpriseExecutionLexicalField(selection, "token_count", "execution.source_plan.result.plan.selections["+strconv.Itoa(index)+"].token_count", false); err != nil {
								return err
							}
						}
					}
				}
				if providerStats, ok := resultPlan["provider_stats"].(map[string]any); ok {
					for provider, value := range providerStats {
						if stats, ok := value.(map[string]any); ok {
							for _, field := range []string{"candidates_offered", "candidates_selected", "tokens_used"} {
								if err := enterpriseExecutionLexicalField(stats, field, "execution.source_plan.result.plan.provider_stats."+provider+"."+field, false); err != nil {
									return err
								}
							}
						}
					}
				}
				if evidence, ok := resultPlan["evidence"].([]any); ok {
					for index, item := range evidence {
						if evidenceItem, ok := item.(map[string]any); ok {
							if err := enterpriseExecutionLexicalField(evidenceItem, "schema_version", "execution.source_plan.result.plan.evidence["+strconv.Itoa(index)+"].schema_version", false); err != nil {
								return err
							}
						}
					}
				}
			}
		}
	}
	executionPlan := enterpriseExecutionLexicalObject(raw, "execution_plan", "execution.execution_plan")
	if executionPlan != nil {
		for _, field := range []string{"schema_version", "context_budget_tokens", "reasoning_allocation_milli", "max_retries", "expected_cost_micros", "expected_quality_milli", "expected_latency_ms"} {
			if err := enterpriseExecutionLexicalField(executionPlan, field, "execution.execution_plan."+field, false); err != nil {
				return err
			}
		}
		if policy := enterpriseExecutionLexicalObject(executionPlan, "context_budget_policy", "execution.execution_plan.context_budget_policy"); policy != nil {
			if err := enterpriseExecutionLexicalField(policy, "tokens", "execution.execution_plan.context_budget_policy.tokens", false); err != nil {
				return err
			}
		}
		if estimates := enterpriseExecutionLexicalObject(executionPlan, "estimates", "execution.execution_plan.estimates"); estimates != nil {
			for _, field := range []string{"cost_micros", "quality_milli", "latency_ms"} {
				if err := enterpriseExecutionLexicalField(estimates, field, "execution.execution_plan.estimates."+field, false); err != nil {
					return err
				}
			}
		}
	}
	if invocation := enterpriseExecutionLexicalObject(raw, "invocation", "execution.invocation"); invocation != nil {
		if err := enterpriseExecutionLexicalField(invocation, "schema_version", "execution.invocation.schema_version", false); err != nil {
			return err
		}
	}
	if observation := enterpriseExecutionLexicalObject(raw, "observation", "execution.observation"); observation != nil {
		if err := enterpriseExecutionLexicalField(observation, "schema_version", "execution.observation.schema_version", false); err != nil {
			return err
		}
		if measurements, ok := observation["measurements"].([]any); ok {
			for index, item := range measurements {
				if measurement, ok := item.(map[string]any); ok {
					if err := enterpriseExecutionLexicalField(measurement, "value", "execution.observation.measurements["+strconv.Itoa(index)+"].value", true); err != nil {
						return err
					}
				}
			}
		}
		if receiptLink := enterpriseExecutionLexicalObject(observation, "receipt_link", "execution.observation.receipt_link"); receiptLink != nil {
			if err := enterpriseExecutionLexicalField(receiptLink, "schema_version", "execution.observation.receipt_link.schema_version", false); err != nil {
				return err
			}
		}
	}
	return nil
}

func enterpriseExecutionDigestSuffix(value, prefix, field string) (string, error) {
	if !strings.HasPrefix(value, prefix) {
		return "", enterpriseProtocol(field + " has an unsupported reference prefix")
	}
	suffix := value[len(prefix):]
	if err := validateDigest("sha256:"+suffix, field); err != nil {
		return "", enterpriseProtocol(field + " is not digest-bound")
	}
	return "sha256:" + suffix, nil
}

func enterpriseExecutionLineage(invocation map[string]any, sourcePlanDigest, executionPlanDigest, taskDigest string) error {
	refs, ok := invocation["source_refs"].([]string)
	if !ok || len(refs) != 4 {
		return enterpriseProtocol("execution invocation must contain four lineage references")
	}
	inputRef, inputRefOK := invocation["input_ref"].(string)
	inputDigest, inputDigestOK := invocation["input_digest"].(string)
	if !inputRefOK || !inputDigestOK {
		return enterpriseProtocol("execution input lineage is invalid")
	}
	inputSuffix, err := enterpriseExecutionDigestSuffix(inputRef, enterpriseExecutionInputPrefix, "execution.invocation.input_ref")
	if err != nil || inputSuffix != inputDigest {
		return enterpriseProtocol("execution input_ref does not bind input_digest")
	}
	counts := map[string]int{"input": 0, "evidence": 0, "task": 0, "plan": 0}
	for _, reference := range refs {
		switch {
		case strings.HasPrefix(reference, enterpriseExecutionInputPrefix):
			counts["input"]++
			suffix, suffixErr := enterpriseExecutionDigestSuffix(reference, enterpriseExecutionInputPrefix, "execution.invocation.source_refs")
			if suffixErr != nil || suffix != inputDigest {
				return enterpriseProtocol("execution input lineage does not bind input_digest")
			}
		case strings.HasPrefix(reference, enterpriseExecutionSourcePlanPrefix):
			counts["evidence"]++
			suffix, suffixErr := enterpriseExecutionDigestSuffix(reference, enterpriseExecutionSourcePlanPrefix, "execution.invocation.source_refs")
			if suffixErr != nil || suffix != sourcePlanDigest {
				return enterpriseProtocol("execution source-plan evidence is not digest-bound")
			}
		case strings.HasPrefix(reference, enterpriseExecutionTaskPrefix):
			counts["task"]++
			suffix, suffixErr := enterpriseExecutionDigestSuffix(reference, enterpriseExecutionTaskPrefix, "execution.invocation.source_refs")
			if suffixErr != nil || suffix != taskDigest {
				return enterpriseProtocol("execution task evidence is not digest-bound")
			}
		case strings.HasPrefix(reference, enterpriseExecutionPlanPrefix):
			counts["plan"]++
			suffix, suffixErr := enterpriseExecutionDigestSuffix(reference, enterpriseExecutionPlanPrefix, "execution.invocation.source_refs")
			if suffixErr != nil || suffix != executionPlanDigest {
				return enterpriseProtocol("execution plan evidence is not digest-bound")
			}
		default:
			return enterpriseProtocol("execution invocation contains unknown lineage reference")
		}
	}
	if counts["input"] != 1 || counts["evidence"] != 1 || counts["task"] != 1 || counts["plan"] != 1 {
		return enterpriseProtocol("execution invocation lineage is incomplete")
	}
	return nil
}

func enterpriseExecutionCanonicalReceipt(value any) (map[string]any, error) {
	raw, err := enterpriseExecutionObject(value, "execution.canonical_receipt", true)
	if err != nil {
		return nil, err
	}
	if err := enterpriseExecutionFields(raw, map[string]bool{"receipt_id": true, "receipt_ref": true, "receipt_digest": true, "outcome": true}, map[string]bool{"receipt_id": true, "receipt_ref": true, "receipt_digest": true, "outcome": true}, "execution.canonical_receipt", true); err != nil {
		return nil, err
	}
	receiptID, err := enterpriseExecutionText(raw["receipt_id"], "execution.canonical_receipt.receipt_id", enterpriseMaxReferenceBytes, true, true, false)
	if err != nil {
		return nil, err
	}
	digest, err := enterpriseDigest(raw["receipt_digest"], "execution.canonical_receipt.receipt_digest")
	if err != nil {
		return nil, err
	}
	receiptRef, err := protocolRef(raw["receipt_ref"], "execution.canonical_receipt.receipt_ref")
	if err != nil {
		return nil, err
	}
	if receiptRef != "id:"+digest || raw["outcome"] != "unknown" {
		return nil, enterpriseProtocol("execution canonical receipt is not an unknown digest-bound outcome")
	}
	return map[string]any{"receipt_id": receiptID, "receipt_ref": receiptRef, "receipt_digest": digest, "outcome": "unknown"}, nil
}

func containsString(values []any, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
