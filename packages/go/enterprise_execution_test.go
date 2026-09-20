// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

const enterpriseExecutionReceiptDocument = "{\"issued_at\":\"2026-09-20T12:34:56Z\",\"note\":\"✓\"}"

func enterpriseExecutionTaskFixture() map[string]any {
	return map[string]any{
		"schema_version":        int64(1),
		"task_id":               "enterprise-task",
		"trace_id":              "trace-enterprise-execution",
		"project_id":            "project-enterprise",
		"session_id":            "session-enterprise",
		"agent_id":              "agent-enterprise",
		"complexity":            "unknown",
		"created_at":            "2026-09-20T12:34:56Z",
		"tenant_id":             enterpriseTestTenant,
		"consumer_extension_v1": map[string]any{"label": "neutral-client", "revision": int64(1)},
	}
}

func enterpriseExecutionPlanFixture() map[string]any {
	return map[string]any{
		"schema_version":             int64(1),
		"plan_id":                    "execution-plan-request",
		"task_id":                    "enterprise-task",
		"context_budget_tokens":      uint64(64),
		"context_strategy":           "minimal",
		"knowledge_refs":             []any{"artifact://context/✓"},
		"capability_ids":             []any{"capability://leanctx/context-optimization"},
		"model":                      "local-native",
		"provider":                   "local-native",
		"reasoning_allocation_milli": uint64(0),
		"max_retries":                uint64(0),
		"fallback_refs":              []any{},
		"stop_condition":             "on_completion",
		"expected_cost_micros":       uint64(0),
		"expected_quality_milli":     uint64(0),
		"expected_latency_ms":        uint64(30000),
		"capability_bindings": []any{
			map[string]any{
				"capability_id": "capability://leanctx/context-optimization",
				"version":       "1.0.0",
			},
		},
	}
}

func enterpriseExecutionResponseValue(
	t *testing.T,
	tenantID, taskID, sourceID, permission, outcome, receiptDocument string,
) (map[string]any, string) {
	t.Helper()
	planResponse := enterprisePlanResponseValue(t, tenantID, taskID, sourceID, 64, permission)
	sourcePlan := planResponse["plan"].(map[string]any)
	bindingDigest := sourcePlan["binding_digest"].(string)
	executionPlan := enterpriseExecutionPlanFixture()
	executionPlan["task_id"] = taskID
	executionPlan["context_plan_id"] = "context-plan-1"
	executionPlan["context_autopilot_decision_ref"] = "decision:context-autopilot-1"
	outputText := "executed source context"
	outputDigest := sha256Hex([]byte(outputText))
	inputDigest := sha256Hex([]byte("materialized source input"))
	taskDigest, err := canonicalDigest(enterpriseExecutionTaskFixture())
	if err != nil {
		t.Fatal(err)
	}
	sourcePlanDigest, err := canonicalDigest(sourcePlan)
	if err != nil {
		t.Fatal(err)
	}
	executionPlanDigest, err := canonicalDigest(executionPlan)
	if err != nil {
		t.Fatal(err)
	}
	refs := []any{
		"input:source-materialization-sha256:" + strings.TrimPrefix(inputDigest, "sha256:"),
		"artifact://execution/evidence/" + strings.TrimPrefix(sourcePlanDigest, "sha256:"),
		"task:sha256:" + strings.TrimPrefix(taskDigest, "sha256:"),
		"plan:sha256:" + strings.TrimPrefix(executionPlanDigest, "sha256:"),
	}
	invocationID := "invocation-enterprise-1"
	engineReceiptDigest := sha256Hex([]byte("engine receipt"))
	hostReceiptDigest := sha256Hex([]byte("host receipt"))
	observation := map[string]any{
		"schema_version": int64(1),
		"invocation_id":  invocationID,
		"status":         "succeeded",
		"output_ref":     "output:" + strings.TrimPrefix(outputDigest, "sha256:"),
		"output_digest":  outputDigest,
		"source_lineage": refs,
		"measurements":   []any{},
		"failure":        nil,
		"receipt_link": map[string]any{
			"schema_version": int64(1),
			"receipt_id":     "engine-receipt-id",
			"receipt_ref":    "receipt:" + engineReceiptDigest,
			"receipt_digest": engineReceiptDigest,
			"invocation_id":  invocationID,
		},
	}
	execution := map[string]any{
		"schema_version":           int64(1),
		"transport_version":        int64(1),
		"engine_interface_version": EngineInterfaceVersion,
		"source_plan":              sourcePlan,
		"execution_plan":           executionPlan,
		"view": map[string]any{
			"text":          outputText,
			"output_ref":    "output:" + strings.TrimPrefix(outputDigest, "sha256:"),
			"output_digest": outputDigest,
		},
		"invocation": map[string]any{
			"schema_version": int64(1),
			"invocation_id":  invocationID,
			"engine": map[string]any{
				"engine_id":      "lean-ctx-local",
				"engine_version": "3.10.1",
			},
			"operation": map[string]any{
				"capability_id":      "capability://leanctx/context-optimization",
				"capability_version": "1.0.0",
			},
			"input_ref":    refs[0],
			"input_digest": inputDigest,
			"source_refs":  refs,
			"policy_admission": map[string]any{
				"policy_ref": "policy:engine-transport-v1:admitted",
				"decision":   "admitted",
			},
		},
		"observation": observation,
		"canonical_receipt": map[string]any{
			"receipt_id":     "host-receipt-id",
			"receipt_ref":    "id:" + hostReceiptDigest,
			"receipt_digest": hostReceiptDigest,
			"outcome":        outcome,
		},
	}
	documentDigest := sha256Hex([]byte(receiptDocument))
	canonicalReceipt := execution["canonical_receipt"].(map[string]any)
	canonicalReceipt["receipt_ref"] = "id:" + documentDigest
	canonicalReceipt["receipt_digest"] = documentDigest
	return map[string]any{
		"schema_version":      int64(2),
		"tenant_id":           tenantID,
		"governance_revision": uint64(7),
		"execution": map[string]any{
			"schema_version":        int64(2),
			"execution":             execution,
			"receipt_document_json": receiptDocument,
		},
	}, bindingDigest
}

func enterpriseExecutionResponse(t *testing.T, value map[string]any) []byte {
	t.Helper()
	payload, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func enterpriseExecutionRequest(t *testing.T, bindingDigest string, revision uint64) EngineSourceExecutionV2Request {
	t.Helper()
	return EngineSourceExecutionV2Request{
		Task: enterpriseExecutionTaskFixture(),
		Plan: enterpriseExecutionPlanFixture(),
		Materialization: enterpriseMaterializationRequest(
			t,
			bindingDigest,
			revision,
			[]string{enterpriseTestSource},
		),
	}
}

func TestEnterpriseContextExecuteV2PreservesReceiptBytesAndUnknownOutcome(t *testing.T) {
	value, bindingDigest := enterpriseExecutionResponseValue(
		t,
		enterpriseTestTenant,
		"enterprise-task",
		enterpriseTestSource,
		"permitted",
		"unknown",
		enterpriseExecutionReceiptDocument,
	)
	server, capture := newEnterprisePlanServer(t, http.StatusOK, enterpriseExecutionResponse(t, value))
	result, err := newEnterprisePlanClient(t, server).ContextExecuteV2(
		enterpriseExecutionRequest(t, bindingDigest, 7),
	)
	if err != nil {
		t.Fatal(err)
	}
	if result.SchemaVersion != 2 || result.TenantID != enterpriseTestTenant || result.GovernanceRevision != 7 {
		t.Fatalf("unexpected execution envelope: %#v", result)
	}
	if result.RawResponse["tenant_id"] != enterpriseTestTenant {
		t.Fatalf("raw response lost tenant binding: %#v", result.RawResponse)
	}
	if !bytes.Equal([]byte(result.ReceiptDocumentJSON), []byte(enterpriseExecutionReceiptDocument)) ||
		!bytes.Equal(result.ReceiptDocumentBytes, []byte(enterpriseExecutionReceiptDocument)) {
		t.Fatalf("receipt document bytes were not preserved: %q", result.ReceiptDocumentJSON)
	}
	receipt, ok := result.Execution["canonical_receipt"].(map[string]any)
	if !ok {
		t.Fatalf("missing canonical receipt: %#v", result.Execution)
	}
	if receipt["outcome"] != "unknown" || receipt["receipt_digest"] != sha256Hex([]byte(enterpriseExecutionReceiptDocument)) {
		t.Fatalf("receipt projection changed: %#v", receipt)
	}
	if capture.calls != 1 || capture.method != http.MethodPost || capture.path != "/v2/engine/context-execute" {
		t.Fatalf("unexpected HTTP request: %#v", capture)
	}
	if capture.authorization == "" || capture.contentType != "application/json" {
		t.Fatalf("unexpected HTTP authentication: %#v", capture)
	}
	body, err := strictJSONLoads(capture.body, "captured Enterprise execution request")
	if err != nil {
		t.Fatal(err)
	}
	bodyObject := body.(map[string]any)
	if _, sentContent := bodyObject["content"]; sentContent {
		t.Fatal("source bodies crossed the execution transport boundary")
	}
	if bodyObject["task"].(map[string]any)["tenant_id"] != enterpriseTestTenant {
		t.Fatalf("task tenant binding was not preserved: %#v", bodyObject["task"])
	}
}

func TestEnterpriseContextExecuteV2RejectsCriticalBindingMutations(t *testing.T) {
	valid, bindingDigest := enterpriseExecutionResponseValue(
		t,
		enterpriseTestTenant,
		"enterprise-task",
		enterpriseTestSource,
		"permitted",
		"unknown",
		enterpriseExecutionReceiptDocument,
	)
	tests := []struct {
		name    string
		value   map[string]any
		request EngineSourceExecutionV2Request
		payload []byte
	}{
		{
			name: "tenant",
			value: func() map[string]any {
				value, _ := enterpriseExecutionResponseValue(t, enterpriseTestOther, "enterprise-task", enterpriseTestSource, "permitted", "unknown", enterpriseExecutionReceiptDocument)
				return value
			}(),
			request: enterpriseExecutionRequest(t, bindingDigest, 7),
		},
		{
			name: "task",
			value: func() map[string]any {
				value, _ := enterpriseExecutionResponseValue(t, enterpriseTestTenant, "other-task", enterpriseTestSource, "permitted", "unknown", enterpriseExecutionReceiptDocument)
				return value
			}(),
			request: enterpriseExecutionRequest(t, bindingDigest, 7),
		},
		{
			name:    "materialization",
			value:   valid,
			request: enterpriseExecutionRequest(t, "sha256:"+strings.Repeat("0", 64), 7),
		},
		{
			name: "unknown outcome",
			value: func() map[string]any {
				value, _ := enterpriseExecutionResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, "permitted", "accepted", enterpriseExecutionReceiptDocument)
				return value
			}(),
			request: enterpriseExecutionRequest(t, bindingDigest, 7),
		},
		{
			name: "lexical integer",
			payload: func() []byte {
				payload := enterpriseExecutionResponse(t, valid)
				mutated := bytes.Replace(payload, []byte(`"schema_version":2`), []byte(`"schema_version":2.0`), 1)
				if bytes.Equal(payload, mutated) {
					t.Fatal("lexical integer mutation did not change the response")
				}
				return mutated
			}(),
			request: enterpriseExecutionRequest(t, bindingDigest, 7),
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			payload := test.payload
			if payload == nil {
				payload = enterpriseExecutionResponse(t, test.value)
			}
			server, capture := newEnterprisePlanServer(t, http.StatusOK, payload)
			_, err := newEnterprisePlanClient(t, server).ContextExecuteV2Context(
				context.Background(),
				test.request,
			)
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected EngineProtocolError, got %T: %v", err, err)
			}
			if strings.Contains(err.Error(), "credential-test") {
				t.Fatal("execution protocol error leaked the bearer credential")
			}
			if capture.calls != 1 {
				t.Fatalf("expected one bounded execution request, got %d", capture.calls)
			}
		})
	}
}

func TestEnterpriseContextExecuteV2RejectsReceiptAndLineageMutations(t *testing.T) {
	for _, mutation := range []string{"receipt bytes", "declared plan", "task lineage", "observation lineage"} {
		t.Run(mutation, func(t *testing.T) {
			value, binding := enterpriseExecutionResponseValue(t, enterpriseTestTenant,
				"enterprise-task", enterpriseTestSource, "permitted", "unknown", enterpriseExecutionReceiptDocument)
			bundle := value["execution"].(map[string]any)
			execution := bundle["execution"].(map[string]any)
			switch mutation {
			case "receipt bytes":
				bundle["receipt_document_json"] = enterpriseExecutionReceiptDocument + " "
			case "declared plan":
				execution["execution_plan"].(map[string]any)["max_retries"] = uint64(1)
			case "task lineage":
				refs := execution["invocation"].(map[string]any)["source_refs"].([]any)
				refs[2] = "task:sha256:" + strings.Repeat("0", 64)
			case "observation lineage":
				observation := execution["observation"].(map[string]any)
				refs := append([]any(nil), observation["source_lineage"].([]any)...)
				refs[2], refs[3] = refs[3], refs[2]
				observation["source_lineage"] = refs
			}
			server, _ := newEnterprisePlanServer(t, http.StatusOK, enterpriseExecutionResponse(t, value))
			_, err := newEnterprisePlanClient(t, server).ContextExecuteV2(enterpriseExecutionRequest(t, binding, 7))
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected protocol rejection, got %T: %v", err, err)
			}
		})
	}
}

func TestEnterpriseContextExecuteV2RejectsLexicalRequestIntegersBeforeHTTP(t *testing.T) {
	for _, field := range []string{"task schema", "plan schema", "plan retries"} {
		t.Run(field, func(t *testing.T) {
			value, binding := enterpriseExecutionResponseValue(t, enterpriseTestTenant,
				"enterprise-task", enterpriseTestSource, "permitted", "unknown", enterpriseExecutionReceiptDocument)
			request := enterpriseExecutionRequest(t, binding, 7)
			switch field {
			case "task schema":
				request.Task["schema_version"] = json.Number("1.0")
			case "plan schema":
				request.Plan["schema_version"] = json.Number("1e0")
			case "plan retries":
				request.Plan["max_retries"] = json.Number("0.0")
			}
			server, capture := newEnterprisePlanServer(t, http.StatusOK, enterpriseExecutionResponse(t, value))
			_, err := newEnterprisePlanClient(t, server).ContextExecuteV2(request)
			if !enterpriseErrorIs[*ValidationError](t, err) || capture.calls != 0 {
				t.Fatalf("malformed request reached HTTP or wrong error: %T; calls=%d", err, capture.calls)
			}
		})
	}
}
