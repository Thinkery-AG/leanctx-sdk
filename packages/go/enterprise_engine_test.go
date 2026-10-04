// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

const (
	enterpriseTestTenant = "11111111-1111-1111-1111-111111111111"
	enterpriseTestSource = "22222222-2222-2222-2222-222222222222"
	enterpriseTestOther  = "33333333-3333-3333-3333-333333333333"
)

type enterprisePlanCapture struct {
	calls         int
	method        string
	path          string
	authorization string
	contentType   string
	body          []byte
}

func newEnterprisePlanServer(t *testing.T, status int, payload []byte) (*httptest.Server, *enterprisePlanCapture) {
	t.Helper()
	capture := &enterprisePlanCapture{}
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		capture.calls++
		capture.method = request.Method
		capture.path = request.URL.Path
		capture.authorization = request.Header.Get("Authorization")
		capture.contentType = request.Header.Get("Content-Type")
		capture.body, _ = io.ReadAll(request.Body)
		if status >= 300 && status < 400 {
			response.Header().Set("Location", "http://127.0.0.1:1/v1/engine/context-plan")
		}
		if status == http.StatusOK && payload != nil {
			response.Header().Set("Content-Type", "application/json")
		}
		if status == http.StatusOK && payload != nil && len(payload) > enterpriseMaxResponseBytes {
			response.Header().Set("Content-Length", strconv.Itoa(len(payload)))
		}
		response.WriteHeader(status)
		if payload != nil {
			_, _ = response.Write(payload)
		}
	}))
	t.Cleanup(server.Close)
	return server, capture
}

func newEnterprisePlanClient(t *testing.T, server *httptest.Server) *EnterpriseEngineClient {
	t.Helper()
	client, err := NewEnterpriseEngineClient(
		server.URL,
		"credential-test",
		enterpriseTestTenant,
		EngineContextClientOptions{AllowLoopbackHTTP: true},
	)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func enterpriseTestRequest(t *testing.T) EnginePlanningRequest {
	t.Helper()
	request, err := NewEnginePlanningRequest("enterprise-task", "invoice ledger", 64, 64)
	if err != nil {
		t.Fatal(err)
	}
	return request
}

func enterpriseTestDescriptor(sourceID, permission string) map[string]any {
	return map[string]any{
		"object_ref":     sourceID,
		"source_id":      sourceID,
		"source_type":    "issue_tracker",
		"content_digest": sha256Hex([]byte("enterprise source")),
		"revision":       nil,
		"owner":          "owner-1",
		"observed_at":    nil,
		"valid_until":    nil,
		"classification": "Internal",
		"permission":     permission,
	}
}

func enterprisePlanResponseValue(t *testing.T, tenantID, taskID, sourceID string, budget uint64, permission string) map[string]any {
	t.Helper()
	descriptor := enterpriseTestDescriptor(sourceID, permission)
	selection := map[string]any{
		"source_ref":    sourceID,
		"provider":      sourceID,
		"disposition":   "selected",
		"token_count":   uint64(1),
		"sha256_digest": descriptor["content_digest"],
		"reason_codes":  []any{"relevant"},
	}
	unsignedPlan := map[string]any{
		"schema_version":  int64(1),
		"context_plan_id": "context-plan-1",
		"task_id":         taskID,
		"budget_tokens":   budget,
		"selections":      []any{selection},
	}
	projectionDigest, err := canonicalDigest(unsignedPlan)
	if err != nil {
		t.Fatal(err)
	}
	plan := map[string]any{
		"schema_version":    int64(1),
		"context_plan_id":   "context-plan-1",
		"task_id":           taskID,
		"budget_tokens":     budget,
		"selections":        []any{selection},
		"projection_digest": projectionDigest,
	}
	result := map[string]any{
		"schema_version":           int64(1),
		"transport_version":        int64(1),
		"engine_interface_version": EngineInterfaceVersion,
		"plan":                     plan,
	}
	bindings := []any{descriptor}
	bindingDigest, err := canonicalDigest([]any{result, bindings})
	if err != nil {
		t.Fatal(err)
	}
	return map[string]any{
		"schema_version":      int64(1),
		"tenant_id":           tenantID,
		"governance_revision": uint64(7),
		"plan": map[string]any{
			"result":          result,
			"source_bindings": bindings,
			"binding_digest":  bindingDigest,
		},
	}
}

func enterprisePlanResponse(t *testing.T, tenantID, taskID, sourceID string, budget uint64, permission string) []byte {
	t.Helper()
	payload, err := json.Marshal(enterprisePlanResponseValue(t, tenantID, taskID, sourceID, budget, permission))
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func enterpriseRehashPlanResponse(t *testing.T, response map[string]any) []byte {
	t.Helper()
	sourcePlan := response["plan"].(map[string]any)
	result := sourcePlan["result"].(map[string]any)
	plan := result["plan"].(map[string]any)
	unsigned := make(map[string]any, len(plan)-1)
	for key, value := range plan {
		if key != "projection_digest" {
			unsigned[key] = value
		}
	}
	projectionDigest, err := canonicalDigest(unsigned)
	if err != nil {
		t.Fatal(err)
	}
	plan["projection_digest"] = projectionDigest
	bindings := sourcePlan["source_bindings"].([]any)
	bindingDigest, err := canonicalDigest([]any{result, bindings})
	if err != nil {
		t.Fatal(err)
	}
	sourcePlan["binding_digest"] = bindingDigest
	payload, err := json.Marshal(response)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func enterpriseErrorIs[T error](t *testing.T, err error) bool {
	t.Helper()
	var target T
	return errors.As(err, &target)
}

func assertEnterpriseProtocolError(t *testing.T, client *EnterpriseEngineClient, request EnginePlanningRequest, sourceIDs []string) {
	t.Helper()
	_, err := client.ContextPlanSourcesContext(context.Background(), request, sourceIDs)
	if !enterpriseErrorIs[*EngineProtocolError](t, err) {
		t.Fatalf("expected EngineProtocolError, got %T: %v", err, err)
	}
	if strings.Contains(err.Error(), "credential-"+"test") {
		t.Fatal("protocol error leaked the bearer credential")
	}
}

func TestEnterpriseContextPlanSourcesRoundTripAndBinding(t *testing.T) {
	payload := enterprisePlanResponse(t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, 64, "permitted")
	server, capture := newEnterprisePlanServer(t, http.StatusOK, payload)
	client := newEnterprisePlanClient(t, server)
	request := enterpriseTestRequest(t)

	result, err := client.ContextPlanSources(request, []string{enterpriseTestSource})
	if err != nil {
		t.Fatal(err)
	}
	if result.SchemaVersion != 1 || result.TenantID != enterpriseTestTenant || result.GovernanceRevision != 7 {
		t.Fatalf("unexpected response envelope: %#v", result)
	}
	if result.RawResponse["tenant_id"] != enterpriseTestTenant {
		t.Fatalf("raw response lost tenant binding: %#v", result.RawResponse)
	}
	plan, ok := result.Plan["result"].(map[string]any)
	if !ok {
		t.Fatalf("unexpected result plan: %#v", result.Plan)
	}
	planBody, ok := plan["plan"].(map[string]any)
	if !ok || planBody["task_id"] != request.TaskID || planBody["budget_tokens"] != uint64(request.BudgetTokens) {
		t.Fatalf("unexpected validated plan: %#v", result.Plan)
	}
	if capture.calls != 1 || capture.method != http.MethodPost || capture.path != enterpriseContextPlanPath {
		t.Fatalf("unexpected HTTP request: %#v", capture)
	}
	if capture.authorization != "Bearer credential-test" || capture.contentType != "application/json" {
		t.Fatalf("unexpected HTTP authentication: %#v", capture)
	}
	requestValue, err := strictJSONLoads(capture.body, "captured Enterprise planning request")
	if err != nil {
		t.Fatal(err)
	}
	requestObject := requestValue.(map[string]any)
	planning, ok := requestObject["planning"].(map[string]any)
	if !ok || planning["task_id"] != request.TaskID || planning["budget_tokens"] != json.Number("64") {
		t.Fatalf("unexpected planning body: %#v", requestObject)
	}
	if _, sentTenant := requestObject["tenant_id"]; sentTenant {
		t.Fatal("tenant binding was sent as an authorization override")
	}
	sourceIDs, ok := requestObject["source_ids"].([]any)
	if !ok || !reflect.DeepEqual(sourceIDs, []any{enterpriseTestSource}) {
		t.Fatalf("unexpected source_ids body: %#v", requestObject["source_ids"])
	}
}

func TestEnterpriseContextPlanSourcesAcceptsMaximumDistinctSources(t *testing.T) {
	request := enterpriseTestRequest(t)
	sourceIDs := make([]string, 64)
	for index := range sourceIDs {
		sourceIDs[index] = fmt.Sprintf("00000000-0000-0000-0000-%012d", index+1)
	}
	payload := enterprisePlanResponse(t, enterpriseTestTenant, request.TaskID, sourceIDs[0], request.BudgetTokens, "permitted")
	server, capture := newEnterprisePlanServer(t, http.StatusOK, payload)
	client := newEnterprisePlanClient(t, server)

	if _, err := client.ContextPlanSources(request, sourceIDs); err != nil {
		t.Fatal(err)
	}
	requestValue, err := strictJSONLoads(capture.body, "captured maximum-source request")
	if err != nil {
		t.Fatal(err)
	}
	bodyIDs := requestValue.(map[string]any)["source_ids"].([]any)
	if len(bodyIDs) != 64 {
		t.Fatalf("expected 64 source IDs, got %d", len(bodyIDs))
	}

	duplicate := []string{enterpriseTestSource, enterpriseTestSource}
	server, capture = newEnterprisePlanServer(t, http.StatusOK, payload)
	client = newEnterprisePlanClient(t, server)
	if _, err := client.ContextPlanSources(request, duplicate); !enterpriseErrorIs[*ValidationError](t, err) {
		t.Fatalf("expected duplicate source validation, got %T: %v", err, err)
	}
	if capture.calls != 0 {
		t.Fatal("duplicate source IDs reached the network")
	}

	tooMany := append(append([]string{}, sourceIDs...), "00000000-0000-0000-0000-000000000065")
	server, capture = newEnterprisePlanServer(t, http.StatusOK, payload)
	client = newEnterprisePlanClient(t, server)
	if _, err := client.ContextPlanSources(request, tooMany); !enterpriseErrorIs[*ValidationError](t, err) {
		t.Fatalf("expected maximum source validation, got %T: %v", err, err)
	}
	if capture.calls != 0 {
		t.Fatal("more than 64 source IDs reached the network")
	}
}

func TestEnterpriseContextPlanSourcesRejectsBindingMismatches(t *testing.T) {
	request := enterpriseTestRequest(t)
	tests := []struct {
		name    string
		payload []byte
		source  []string
	}{
		{
			name:    "tenant",
			payload: enterprisePlanResponse(t, enterpriseTestOther, request.TaskID, enterpriseTestSource, request.BudgetTokens, "permitted"),
			source:  []string{enterpriseTestSource},
		},
		{
			name:    "task",
			payload: enterprisePlanResponse(t, enterpriseTestTenant, "other-task", enterpriseTestSource, request.BudgetTokens, "permitted"),
			source:  []string{enterpriseTestSource},
		},
		{
			name:    "budget",
			payload: enterprisePlanResponse(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens+1, "permitted"),
			source:  []string{enterpriseTestSource},
		},
		{
			name:    "source-membership",
			payload: enterprisePlanResponse(t, enterpriseTestTenant, request.TaskID, enterpriseTestOther, request.BudgetTokens, "permitted"),
			source:  []string{enterpriseTestSource},
		},
		{
			name:    "permission",
			payload: enterprisePlanResponse(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens, "denied"),
			source:  []string{enterpriseTestSource},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, _ := newEnterprisePlanServer(t, http.StatusOK, test.payload)
			assertEnterpriseProtocolError(t, newEnterprisePlanClient(t, server), request, test.source)
		})
	}

	t.Run("projection-digest", func(t *testing.T) {
		response := enterprisePlanResponseValue(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens, "permitted")
		plan := response["plan"].(map[string]any)["result"].(map[string]any)["plan"].(map[string]any)
		plan["projection_digest"] = "sha256:" + strings.Repeat("b", 64)
		payload, err := json.Marshal(response)
		if err != nil {
			t.Fatal(err)
		}
		server, _ := newEnterprisePlanServer(t, http.StatusOK, payload)
		assertEnterpriseProtocolError(t, newEnterprisePlanClient(t, server), request, []string{enterpriseTestSource})
	})

	t.Run("binding-digest", func(t *testing.T) {
		response := enterprisePlanResponseValue(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens, "permitted")
		plan := response["plan"].(map[string]any)
		plan["binding_digest"] = "sha256:" + strings.Repeat("c", 64)
		payload, err := json.Marshal(response)
		if err != nil {
			t.Fatal(err)
		}
		server, _ := newEnterprisePlanServer(t, http.StatusOK, payload)
		assertEnterpriseProtocolError(t, newEnterprisePlanClient(t, server), request, []string{enterpriseTestSource})
	})
}

func TestEnterpriseContextPlanSourcesRejectsNonCanonicalIntegers(t *testing.T) {
	request := enterpriseTestRequest(t)
	valid := enterprisePlanResponse(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens, "permitted")
	for _, lexical := range []string{"1.0", "1e0"} {
		t.Run(lexical, func(t *testing.T) {
			mutated := bytes.Replace(valid, []byte(`"schema_version":1`), []byte(`"schema_version":`+lexical), 1)
			if bytes.Equal(mutated, valid) {
				t.Fatal("test mutation did not change the top-level schema version")
			}
			server, _ := newEnterprisePlanServer(t, http.StatusOK, mutated)
			assertEnterpriseProtocolError(t, newEnterprisePlanClient(t, server), request, []string{enterpriseTestSource})
		})
	}
}

func TestEnterpriseContextPlanSourcesPreservesAdditivePlanEvidence(t *testing.T) {
	request := enterpriseTestRequest(t)
	response := enterprisePlanResponseValue(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens, "permitted")
	plan := response["plan"].(map[string]any)["result"].(map[string]any)["plan"].(map[string]any)
	plan["x_extension"] = map[string]any{"note": "preserve"}
	plan["evidence"] = []any{map[string]any{
		"kind":             "RuntimeLog",
		"uri":              "runtime://one",
		"digest":           "evidence-1",
		"signature_status": "Unverified",
		"extra_note":       "preserve",
	}}
	payload := enterpriseRehashPlanResponse(t, response)
	server, _ := newEnterprisePlanServer(t, http.StatusOK, payload)
	result, err := newEnterprisePlanClient(t, server).ContextPlanSources(request, []string{enterpriseTestSource})
	if err != nil {
		t.Fatal(err)
	}
	validatedPlan := result.Plan["result"].(map[string]any)["plan"].(map[string]any)
	if !reflect.DeepEqual(validatedPlan["x_extension"], map[string]any{"note": "preserve"}) {
		t.Fatalf("plan extension was not preserved: %#v", validatedPlan["x_extension"])
	}
	evidence, ok := validatedPlan["evidence"].([]any)
	if !ok || len(evidence) != 1 || evidence[0].(map[string]any)["extra_note"] != "preserve" {
		t.Fatalf("evidence extension was not preserved: %#v", validatedPlan["evidence"])
	}
}

func TestEnterpriseContextPlanSourcesRejectsNonCanonicalExtensionNumbers(t *testing.T) {
	request := enterpriseTestRequest(t)
	// Extension numbers use the existing canonical numeric-value domain, unlike
	// protocol integer fields whose lexical form must also be an integer.
	for _, lexical := range []string{"1.5", "9007199254740992", "1e50"} {
		t.Run(lexical, func(t *testing.T) {
			if err := enterpriseExtensionValue(json.Number(lexical), 0); !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("non-canonical extension number accepted without a projection digest: %v", err)
			}
			response := enterprisePlanResponseValue(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens, "permitted")
			plan := response["plan"].(map[string]any)["result"].(map[string]any)["plan"].(map[string]any)
			plan["x_extension"] = int64(1)
			valid := enterpriseRehashPlanResponse(t, response)
			payload := bytes.Replace(valid, []byte(`"x_extension":1`), []byte(`"x_extension":`+lexical), 1)
			if bytes.Equal(payload, valid) {
				t.Fatal("test mutation did not change the extension number")
			}
			server, _ := newEnterprisePlanServer(t, http.StatusOK, payload)
			assertEnterpriseProtocolError(t, newEnterprisePlanClient(t, server), request, []string{enterpriseTestSource})
		})
	}
}

func TestEnterpriseContextPlanSourcesRejectsUnpairedSurrogate(t *testing.T) {
	request := enterpriseTestRequest(t)
	response := enterprisePlanResponseValue(t, enterpriseTestTenant, request.TaskID, enterpriseTestSource, request.BudgetTokens, "permitted")
	sourcePlan := response["plan"].(map[string]any)
	result := sourcePlan["result"].(map[string]any)
	bindings := sourcePlan["source_bindings"].([]any)
	bindings[0].(map[string]any)["owner"] = "\uFFFD"
	bindingDigest, err := canonicalDigest([]any{result, bindings})
	if err != nil {
		t.Fatal(err)
	}
	sourcePlan["binding_digest"] = bindingDigest
	valid, err := json.Marshal(response)
	if err != nil {
		t.Fatal(err)
	}
	baselineServer, _ := newEnterprisePlanServer(t, http.StatusOK, valid)
	if _, err := newEnterprisePlanClient(t, baselineServer).ContextPlanSources(request, []string{enterpriseTestSource}); err != nil {
		t.Fatalf("valid U+FFFD baseline was rejected: %v", err)
	}
	mutated := bytes.Replace(valid, []byte("\uFFFD"), []byte(`\ud800`), 1)
	if !json.Valid(mutated) {
		t.Fatal("test mutation is not valid JSON")
	}
	if bytes.Equal(mutated, valid) {
		t.Fatal("test mutation did not change the owner value")
	}
	server, _ := newEnterprisePlanServer(t, http.StatusOK, mutated)
	assertEnterpriseProtocolError(t, newEnterprisePlanClient(t, server), request, []string{enterpriseTestSource})
}

func TestEnterpriseContextPlanSourcesMapsHTTPBoundaries(t *testing.T) {
	request := enterpriseTestRequest(t)
	tests := []struct {
		name      string
		status    int
		wantError func(*testing.T, error)
	}{
		{
			name:   "redirect",
			status: http.StatusTemporaryRedirect,
			wantError: func(t *testing.T, err error) {
				if !enterpriseErrorIs[*EngineProtocolError](t, err) {
					t.Fatalf("expected redirect protocol error, got %T: %v", err, err)
				}
			},
		},
		{
			name:   "unauthorized",
			status: http.StatusUnauthorized,
			wantError: func(t *testing.T, err error) {
				if !enterpriseErrorIs[*EngineRejected](t, err) {
					t.Fatalf("expected EngineRejected, got %T: %v", err, err)
				}
			},
		},
		{
			name:   "forbidden",
			status: http.StatusForbidden,
			wantError: func(t *testing.T, err error) {
				if !enterpriseErrorIs[*PolicyAdmissionError](t, err) {
					t.Fatalf("expected PolicyAdmissionError, got %T: %v", err, err)
				}
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, capture := newEnterprisePlanServer(t, test.status, []byte("credential-test"))
			_, err := newEnterprisePlanClient(t, server).ContextPlanSources(request, []string{enterpriseTestSource})
			if err == nil {
				t.Fatal("expected HTTP boundary error")
			}
			test.wantError(t, err)
			if capture.calls != 1 || strings.Contains(err.Error(), "credential-"+"test") {
				t.Fatalf("unexpected call count or credential leak: calls=%d err=%v", capture.calls, err)
			}
		})
	}
}

func TestEnterpriseContextPlanSourcesRejectsOversizedResponse(t *testing.T) {
	request := enterpriseTestRequest(t)
	server, capture := newEnterprisePlanServer(
		t,
		http.StatusOK,
		bytes.Repeat([]byte("x"), enterpriseMaxResponseBytes+1),
	)
	_, err := newEnterprisePlanClient(t, server).ContextPlanSources(request, []string{enterpriseTestSource})
	if !enterpriseErrorIs[*EngineProtocolError](t, err) {
		t.Fatalf("expected response-bound error, got %T: %v", err, err)
	}
	if capture.calls != 1 || strings.Contains(err.Error(), "credential-"+"test") {
		t.Fatalf("unexpected call count or credential leak: calls=%d err=%v", capture.calls, err)
	}
}
