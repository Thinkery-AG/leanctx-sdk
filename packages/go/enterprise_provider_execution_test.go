// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"bytes"
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"testing"
)

func providerExecutionTaskFixture() map[string]any {
	return enterpriseExecutionTaskFixture()
}

func providerExecutionPlanFixture() map[string]any {
	plan := enterpriseExecutionPlanFixture()
	plan["plan_id"] = "provider-execution-plan"
	plan["context_plan_id"] = "provider-context-plan"
	plan["provider"] = "openai"
	plan["model"] = "gpt-4o"
	return plan
}

func providerExecutionRequestFixture(t *testing.T) EngineProviderExecutionRequest {
	t.Helper()
	materialization := enterpriseMaterializationRequest(
		t,
		sha256Hex([]byte("source binding")),
		7,
		[]string{enterpriseTestSource},
	)
	evaluationTime := "2026-09-20T12:34:56Z"
	materialization.PlanningEvaluationTime = &evaluationTime
	return EngineProviderExecutionRequest{
		Task:            providerExecutionTaskFixture(),
		Plan:            providerExecutionPlanFixture(),
		Materialization: materialization,
		MaxOutputTokens: 64,
	}
}

func providerExecutionResponseFixture() map[string]any {
	content := "provider output"
	return map[string]any{
		"schema_version":           int64(1),
		"transport_version":        int64(1),
		"engine_interface_version": EngineInterfaceVersion,
		"attempt_id":               "provider-attempt-1",
		"task_id":                  "enterprise-task",
		"plan_id":                  "provider-execution-plan",
		"context_digest":           sha256Hex([]byte("materialized context")),
		"request_digest":           sha256Hex([]byte("host-owned request")),
		"provider":                 "openai",
		"model":                    "gpt-4o",
		"status":                   "succeeded",
		"acceptance":               "unknown",
		"output": map[string]any{
			"content":       content,
			"sha256_digest": sha256Hex([]byte(content)),
		},
		"usage": map[string]any{
			"state":                    "measured",
			"uncached_input_tokens":    uint64(100),
			"cache_write_input_tokens": uint64(0),
			"cache_read_input_tokens":  uint64(0),
			"total_input_tokens":       uint64(100),
			"output_tokens":            uint64(20),
		},
		"cost": map[string]any{"basis": "unavailable"},
	}
}

func providerExecutionResponseBytes(t *testing.T, value map[string]any) []byte {
	t.Helper()
	payload, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func TestEnterpriseProviderExecutePostsBoundedRequestAndParsesSuccess(t *testing.T) {
	request := providerExecutionRequestFixture(t)
	server, capture := newEnterprisePlanServer(
		t,
		http.StatusOK,
		providerExecutionResponseBytes(t, providerExecutionResponseFixture()),
	)
	result, err := newEnterprisePlanClient(t, server).ProviderExecute(request)
	if err != nil {
		t.Fatal(err)
	}
	if result.SchemaVersion != 1 || result.TransportVersion != 1 || result.Acceptance != "unknown" {
		t.Fatalf("unexpected provider response header: %#v", result)
	}
	if result.TaskID != request.Task["task_id"] || result.PlanID != request.Plan["plan_id"] ||
		result.Provider != "openai" || result.Model != "gpt-4o" {
		t.Fatalf("provider response lost request joins: %#v", result)
	}
	if result.Output == nil || result.Output.Content != "provider output" || result.Usage.State != "measured" ||
		result.Cost.Basis != "unavailable" {
		t.Fatalf("unexpected typed provider result: %#v", result)
	}
	if result.RawResponse["acceptance"] != "unknown" {
		t.Fatalf("raw provider response was not retained: %#v", result.RawResponse)
	}
	if capture.calls != 1 || capture.method != http.MethodPost || capture.path != "/v1/engine/provider-execute" {
		t.Fatalf("unexpected provider HTTP request: %#v", capture)
	}
	if !strings.HasPrefix(capture.authorization, "Bearer ") || capture.contentType != "application/json" {
		t.Fatalf("unexpected provider HTTP authentication: %#v", capture)
	}
	bodyValue, err := strictJSONLoads(capture.body, "captured provider execution request")
	if err != nil {
		t.Fatal(err)
	}
	body := bodyValue.(map[string]any)
	if len(body) != 5 || !reflect.DeepEqual(map[string]bool{
		"schema_version":    true,
		"task":              true,
		"plan":              true,
		"materialization":   true,
		"max_output_tokens": true,
	}, map[string]bool{
		"schema_version":    body["schema_version"] != nil,
		"task":              body["task"] != nil,
		"plan":              body["plan"] != nil,
		"materialization":   body["materialization"] != nil,
		"max_output_tokens": body["max_output_tokens"] != nil,
	}) {
		t.Fatalf("provider request fields changed: %#v", body)
	}
	if body["schema_version"] != json.Number("1") || body["max_output_tokens"] != json.Number("64") {
		t.Fatalf("provider request versions/bounds changed: %#v", body)
	}
	task := body["task"].(map[string]any)
	plan := body["plan"].(map[string]any)
	if task["tenant_id"] != enterpriseTestTenant || task["task_id"] != request.Task["task_id"] ||
		plan["task_id"] != request.Plan["task_id"] || plan["provider"] != "openai" || plan["model"] != "gpt-4o" {
		t.Fatalf("provider request joins changed: %#v", body)
	}
	materialization := body["materialization"].(map[string]any)
	if materialization["expected_governance_revision"] != json.Number("7") ||
		materialization["expected_binding_digest"] != sha256Hex([]byte("source binding")) ||
		materialization["planning_evaluation_time"] != "2026-09-20T12:34:56Z" {
		t.Fatalf("provider materialization binding changed: %#v", materialization)
	}
	if _, sentContent := body["content"]; sentContent {
		t.Fatal("provider request sent source content instead of source IDs")
	}
}

func TestEnterpriseProviderExecuteRejectsInvalidInputBeforeHTTP(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*EngineProviderExecutionRequest)
	}{
		{
			name: "local-native provider",
			mutate: func(request *EngineProviderExecutionRequest) {
				request.Plan["provider"] = "local-native"
				request.Plan["model"] = "local-native"
			},
		},
		{
			name: "retry",
			mutate: func(request *EngineProviderExecutionRequest) {
				request.Plan["max_retries"] = uint64(1)
			},
		},
		{
			name: "fallback",
			mutate: func(request *EngineProviderExecutionRequest) {
				request.Plan["fallback_refs"] = []any{"provider-fallback"}
			},
		},
		{
			name: "missing context plan",
			mutate: func(request *EngineProviderExecutionRequest) {
				delete(request.Plan, "context_plan_id")
			},
		},
		{
			name: "budget exceeds planning request",
			mutate: func(request *EngineProviderExecutionRequest) {
				request.Plan["context_budget_tokens"] = uint64(65)
			},
		},
		{
			name: "output bound",
			mutate: func(request *EngineProviderExecutionRequest) {
				request.MaxOutputTokens = 65_537
			},
		},
		{
			name: "lexical request integer",
			mutate: func(request *EngineProviderExecutionRequest) {
				request.Plan["max_retries"] = json.Number("0.0")
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, capture := newEnterprisePlanServer(t, http.StatusOK, providerExecutionResponseBytes(t, providerExecutionResponseFixture()))
			request := providerExecutionRequestFixture(t)
			test.mutate(&request)
			_, err := newEnterprisePlanClient(t, server).ProviderExecute(request)
			if !enterpriseErrorIs[*ValidationError](t, err) {
				t.Fatalf("expected preflight ValidationError, got %T: %v", err, err)
			}
			if capture.calls != 0 {
				t.Fatal("invalid provider request reached the network")
			}
		})
	}
}

func TestEnterpriseProviderExecutePreservesUnavailableUsageAndUnknownAcceptance(t *testing.T) {
	response := providerExecutionResponseFixture()
	response["usage"] = map[string]any{
		"state":                    "unavailable",
		"uncached_input_tokens":    nil,
		"cache_write_input_tokens": nil,
		"cache_read_input_tokens":  nil,
		"total_input_tokens":       nil,
		"output_tokens":            nil,
	}
	server, _ := newEnterprisePlanServer(t, http.StatusOK, providerExecutionResponseBytes(t, response))
	result, err := newEnterprisePlanClient(t, server).ProviderExecute(providerExecutionRequestFixture(t))
	if err != nil {
		t.Fatal(err)
	}
	if result.Acceptance != "unknown" || result.Usage.State != "unavailable" || result.Cost.Basis != "unavailable" {
		t.Fatalf("unknown provider provenance was changed: %#v", result)
	}
	if result.Usage.UncachedInputTokens != nil || result.Usage.CacheWriteInputTokens != nil ||
		result.Usage.CacheReadInputTokens != nil || result.Usage.TotalInputTokens != nil ||
		result.Usage.OutputTokens != nil || result.Cost.Micros != nil {
		t.Fatal("unavailable metrics were converted into numeric values")
	}
}

func TestEnterpriseProviderExecuteRejectsResponseTamperingAndNonCanonicalIntegers(t *testing.T) {
	valid := providerExecutionResponseBytes(t, providerExecutionResponseFixture())
	tests := []struct {
		name    string
		payload func([]byte) []byte
	}{
		{
			name: "output digest",
			payload: func(payload []byte) []byte {
				return bytes.Replace(payload, []byte(`"content":"provider output"`), []byte(`"content":"tampered output"`), 1)
			},
		},
		{
			name: "accepted outcome",
			payload: func(payload []byte) []byte {
				return bytes.Replace(payload, []byte(`"acceptance":"unknown"`), []byte(`"acceptance":"accepted"`), 1)
			},
		},
		{
			name: "usage total mismatch",
			payload: func(payload []byte) []byte {
				return bytes.Replace(payload, []byte(`"total_input_tokens":100`), []byte(`"total_input_tokens":101`), 1)
			},
		},
		{
			name: "estimated cost without usage",
			payload: func(payload []byte) []byte {
				mutated := bytes.Replace(payload, []byte(`"state":"measured"`), []byte(`"state":"unavailable"`), 1)
				mutated = bytes.Replace(mutated, []byte(`"uncached_input_tokens":100`), []byte(`"uncached_input_tokens":null`), 1)
				mutated = bytes.Replace(mutated, []byte(`"cache_write_input_tokens":0`), []byte(`"cache_write_input_tokens":null`), 1)
				mutated = bytes.Replace(mutated, []byte(`"cache_read_input_tokens":0`), []byte(`"cache_read_input_tokens":null`), 1)
				mutated = bytes.Replace(mutated, []byte(`"total_input_tokens":100`), []byte(`"total_input_tokens":null`), 1)
				mutated = bytes.Replace(mutated, []byte(`"output_tokens":20`), []byte(`"output_tokens":null`), 1)
				mutated = bytes.Replace(mutated, []byte(`"cost":{"basis":"unavailable"}`), []byte(`"cost":{"basis":"usage_priced_estimate","micros":1}`), 1)
				return mutated
			},
		},
		{
			name: "retryable failure",
			payload: func(payload []byte) []byte {
				var response map[string]any
				if err := json.Unmarshal(payload, &response); err != nil {
					t.Fatalf("invalid fixture response: %v", err)
				}
				response["status"] = "failed"
				response["output"] = nil
				response["failure"] = map[string]any{
					"code":              "internal",
					"retryable_by_host": true,
				}
				mutated, err := json.Marshal(response)
				if err != nil {
					t.Fatalf("failed to encode fixture response: %v", err)
				}
				return mutated
			},
		},
		{
			name: "decimal schema",
			payload: func(payload []byte) []byte {
				return bytes.Replace(payload, []byte(`"schema_version":1`), []byte(`"schema_version":1.0`), 1)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			payload := test.payload(valid)
			if bytes.Equal(payload, valid) {
				t.Fatal("tamper fixture did not change the response")
			}
			server, _ := newEnterprisePlanServer(t, http.StatusOK, payload)
			_, err := newEnterprisePlanClient(t, server).ProviderExecute(providerExecutionRequestFixture(t))
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected EngineProtocolError, got %T: %v", err, err)
			}
		})
	}
}

func TestEnterpriseProviderExecuteRejectsResponseIdentityChanges(t *testing.T) {
	for _, field := range []string{"task_id", "plan_id", "provider", "model"} {
		t.Run(field, func(t *testing.T) {
			response := providerExecutionResponseFixture()
			response[field] = "different"
			server, _ := newEnterprisePlanServer(t, http.StatusOK, providerExecutionResponseBytes(t, response))
			_, err := newEnterprisePlanClient(t, server).ProviderExecute(providerExecutionRequestFixture(t))
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected identity rejection, got %T: %v", err, err)
			}
		})
	}
}
