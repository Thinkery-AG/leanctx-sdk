// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const (
	enterpriseOutcomeReceiptDigest     = "sha256:7f14e6c63be2b916ad8a2c6f4f9d89fbc8d7b7f4e21f7ac1d1f69e1d4050fbd1"
	enterpriseOutcomeDecisionDigest    = "sha256:3dc99b8b4c2dbe7fbc2a57e4ed4a57eb7c0b3c24b1f2a6b3c8f8f2ec9a8c0b6e"
	enterpriseOutcomePreviousReceiptID = "sha256:bd1a5d7f9a9e9f62a6d531e7f8e4d2b8f4a6c0d9e3f1b5a7c9d2e4f6a8b0c1d3"
)

type enterpriseOutcomeCapture struct {
	calls         int
	method        string
	path          string
	authorization string
	contentType   string
	body          []byte
}

func newEnterpriseOutcomeServer(t *testing.T, bodyFactory func([]byte) []byte) (*httptest.Server, *enterpriseOutcomeCapture) {
	t.Helper()
	capture := &enterpriseOutcomeCapture{}
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		capture.calls++
		capture.method = request.Method
		capture.path = request.URL.Path
		capture.authorization = request.Header.Get("Authorization")
		capture.contentType = request.Header.Get("Content-Type")
		capture.body, _ = io.ReadAll(request.Body)
		payload := bodyFactory(capture.body)
		response.Header().Set("Content-Type", "application/json")
		response.Header().Set("Content-Length", fmt.Sprintf("%d", len(payload)))
		response.WriteHeader(http.StatusOK)
		_, _ = response.Write(payload)
	}))
	t.Cleanup(server.Close)
	return server, capture
}

func newEnterpriseOutcomeClient(t *testing.T, server *httptest.Server) *EnterpriseEngineClient {
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

func enterpriseOutcomeRequestFixture() EngineOutcomeRequest {
	return EngineOutcomeRequest{
		TaskID:                "enterprise-task",
		ReceiptDigest:         enterpriseOutcomeReceiptDigest,
		ContextDecisionDigest: enterpriseOutcomeDecisionDigest,
		Signals: []map[string]any{
			{"signal_type": "human_acceptance", "value": map[string]any{"boolean": true}},
			{"signal_type": "tests_passing", "value": map[string]any{"count": uint64(3)}},
			{"signal_type": "correction", "value": "unknown"},
		},
	}
}

func enterpriseOutcomeReceiptDocument(
	t *testing.T,
	taskID, acceptance, decisionDigest, previousReceiptID string,
) (string, string) {
	t.Helper()
	document := map[string]any{
		"schema_version": int64(1),
		"chain": map[string]any{
			"previous_receipt_id": previousReceiptID,
		},
		"lineage": map[string]any{
			"task_id": taskID,
		},
		"status":    "succeeded",
		"values":    []any{},
		"outcome":   map[string]any{"state": acceptance},
		"issued_at": "2026-09-20T00:00:00Z",
		"signer": map[string]any{
			"algorithm":     "ed25519",
			"key_id":        "fixture-only",
			"key_admission": "external_trust_store",
		},
		"evidence_refs": []any{
			map[string]any{
				"kind":   "runtime",
				"digest": decisionDigest,
				"uri":    "artifact://execution/evidence/" + strings.TrimPrefix(decisionDigest, "sha256:"),
			},
		},
	}
	receiptID, err := canonicalDigest(document)
	if err != nil {
		t.Fatal(err)
	}
	document["receipt_id"] = receiptID
	document["signature"] = "fixture-only-not-a-trusted-signature"
	payload, err := canonicalJSON(document)
	if err != nil {
		t.Fatal(err)
	}
	return string(payload), receiptID
}

func enterpriseOutcomeResponseValue(
	t *testing.T,
	tenantID, taskID, acceptance, decisionDigest, previousReceiptID string,
	receiptDigest, originalReceiptDigest string,
	alreadyRecorded bool,
) (map[string]any, string) {
	t.Helper()
	document, receiptID := enterpriseOutcomeReceiptDocument(
		t, taskID, acceptance, decisionDigest, previousReceiptID,
	)
	if receiptDigest == "" {
		receiptDigest = sha256Hex([]byte(document))
	}
	value := map[string]any{
		"schema_version": int64(1),
		"tenant_id":      tenantID,
		"outcome": map[string]any{
			"schema_version":          int64(1),
			"receipt_id":              receiptID,
			"receipt_digest":          receiptDigest,
			"original_receipt_digest": originalReceiptDigest,
			"acceptance":              acceptance,
			"already_recorded":        alreadyRecorded,
			"receipt_document_json":   document,
		},
	}
	return value, document
}

func enterpriseOutcomeResponse(t *testing.T, value map[string]any) []byte {
	t.Helper()
	payload, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func enterpriseOutcomeRebindDocument(t *testing.T, outcome map[string]any, document string, recomputeIdentity bool) {
	t.Helper()
	outcome["receipt_document_json"] = document
	outcome["receipt_digest"] = sha256Hex([]byte(document))
	if recomputeIdentity {
		var decoded map[string]any
		if err := json.Unmarshal([]byte(document), &decoded); err != nil {
			t.Fatal(err)
		}
		outcome["receipt_id"] = decoded["receipt_id"]
	}
}

func TestEnterpriseContextOutcomeRoundTripAndRequestBoundary(t *testing.T) {
	value, document := enterpriseOutcomeResponseValue(
		t,
		enterpriseTestTenant,
		"enterprise-task",
		"accepted",
		enterpriseOutcomeDecisionDigest,
		enterpriseOutcomePreviousReceiptID,
		"",
		enterpriseOutcomeReceiptDigest,
		false,
	)
	server, capture := newEnterpriseOutcomeServer(t, func(_ []byte) []byte {
		return enterpriseOutcomeResponse(t, value)
	})
	result, err := newEnterpriseOutcomeClient(t, server).ContextOutcome(enterpriseOutcomeRequestFixture())
	if err != nil {
		t.Fatal(err)
	}
	if result.SchemaVersion != 1 || result.TenantID != enterpriseTestTenant {
		t.Fatalf("unexpected outcome envelope: %#v", result)
	}
	if result.RawResponse["tenant_id"] != enterpriseTestTenant {
		t.Fatalf("raw response lost tenant binding: %#v", result.RawResponse)
	}
	outcome, ok := result.RawResponse["outcome"].(map[string]any)
	if !ok || outcome["receipt_document_json"] != document {
		t.Fatalf("receipt document was not preserved: %#v", result.RawResponse)
	}
	if result.ReceiptID != outcome["receipt_id"] ||
		result.ReceiptDigest != outcome["receipt_digest"] ||
		result.OriginalReceiptDigest != outcome["original_receipt_digest"] ||
		result.Acceptance != "accepted" || result.AlreadyRecorded ||
		result.ReceiptDocumentJSON != document ||
		!bytes.Equal(result.ReceiptDocumentBytes, []byte(document)) {
		t.Fatalf("typed outcome fields lost the canonical carrier: %#v", result)
	}
	if capture.calls != 1 || capture.method != http.MethodPost || capture.path != "/v1/engine/context-outcome" {
		t.Fatalf("unexpected HTTP request: %#v", capture)
	}
	if capture.authorization != "Bearer credential-test" || capture.contentType != "application/json" {
		t.Fatalf("unexpected HTTP authentication: %#v", capture)
	}
	body, err := strictJSONLoads(capture.body, "captured Enterprise outcome request")
	if err != nil {
		t.Fatal(err)
	}
	request, ok := body.(map[string]any)
	if !ok {
		t.Fatalf("request is not an object: %#v", body)
	}
	if len(request) != 4 || request["task_id"] != "enterprise-task" ||
		request["receipt_digest"] != enterpriseOutcomeReceiptDigest ||
		request["context_decision_digest"] != enterpriseOutcomeDecisionDigest {
		t.Fatalf("unexpected request subset: %#v", request)
	}
	if _, found := request["tenant_id"]; found {
		t.Fatal("caller tenant crossed the outcome request boundary")
	}
	if _, found := request["agent_id"]; found {
		t.Fatal("caller agent crossed the outcome request boundary")
	}
	if _, found := request["learn"]; found {
		t.Fatal("learning control crossed the outcome request boundary")
	}
}

func TestEnterpriseContextOutcomeAcceptsRejectedAndIdempotentProjections(t *testing.T) {
	for _, test := range []struct {
		name            string
		acceptance      string
		alreadyRecorded bool
	}{
		{name: "accepted", acceptance: "accepted"},
		{name: "rejected", acceptance: "rejected"},
		{name: "already recorded", acceptance: "accepted", alreadyRecorded: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			value, _ := enterpriseOutcomeResponseValue(
				t,
				enterpriseTestTenant,
				"enterprise-task",
				test.acceptance,
				enterpriseOutcomeDecisionDigest,
				enterpriseOutcomePreviousReceiptID,
				"",
				enterpriseOutcomeReceiptDigest,
				test.alreadyRecorded,
			)
			server, _ := newEnterpriseOutcomeServer(t, func(_ []byte) []byte {
				return enterpriseOutcomeResponse(t, value)
			})
			result, err := newEnterpriseOutcomeClient(t, server).ContextOutcome(enterpriseOutcomeRequestFixture())
			if err != nil {
				t.Fatal(err)
			}
			outcome := result.RawResponse["outcome"].(map[string]any)
			if outcome["acceptance"] != test.acceptance || outcome["already_recorded"] != test.alreadyRecorded {
				t.Fatalf("unexpected outcome projection: %#v", outcome)
			}
		})
	}
}

func TestEnterpriseContextOutcomeRejectsInvalidRequestsBeforeHTTP(t *testing.T) {
	valid := enterpriseOutcomeRequestFixture()
	cases := []struct {
		name   string
		mutate func(*EngineOutcomeRequest)
	}{
		{name: "empty task", mutate: func(request *EngineOutcomeRequest) { request.TaskID = "" }},
		{name: "invalid receipt digest", mutate: func(request *EngineOutcomeRequest) { request.ReceiptDigest = "not-a-digest" }},
		{name: "invalid decision digest", mutate: func(request *EngineOutcomeRequest) { request.ContextDecisionDigest = "not-a-digest" }},
		{name: "unknown signal", mutate: func(request *EngineOutcomeRequest) {
			request.Signals = []map[string]any{{"signal_type": "agent_completion", "value": map[string]any{"boolean": true}}}
		}},
		{name: "boolean type", mutate: func(request *EngineOutcomeRequest) {
			request.Signals = []map[string]any{{"signal_type": "human_acceptance", "value": map[string]any{"boolean": uint64(1)}}}
		}},
		{name: "count lexical integer", mutate: func(request *EngineOutcomeRequest) {
			request.Signals = []map[string]any{{"signal_type": "tests_passing", "value": map[string]any{"count": json.Number("1.0")}}}
		}},
		{name: "count exceeds u32", mutate: func(request *EngineOutcomeRequest) {
			request.Signals = []map[string]any{{"signal_type": "tests_passing", "value": map[string]any{"count": json.Number("4294967296")}}}
		}},
		{name: "signal fields", mutate: func(request *EngineOutcomeRequest) {
			request.Signals = []map[string]any{{"signal_type": "tests_passing", "value": map[string]any{"count": uint64(1), "extra": true}}}
		}},
		{name: "nil signals", mutate: func(request *EngineOutcomeRequest) { request.Signals = nil }},
		{name: "empty signals", mutate: func(request *EngineOutcomeRequest) { request.Signals = []map[string]any{} }},
		{name: "too many signals", mutate: func(request *EngineOutcomeRequest) {
			request.Signals = make([]map[string]any, 17)
			for index := range request.Signals {
				request.Signals[index] = map[string]any{"signal_type": "correction", "value": "unknown"}
			}
		}},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			request := valid
			test.mutate(&request)
			server, capture := newEnterpriseOutcomeServer(t, func(_ []byte) []byte {
				return []byte(`{}`)
			})
			_, err := newEnterpriseOutcomeClient(t, server).ContextOutcome(request)
			if !enterpriseErrorIs[*ValidationError](t, err) {
				t.Fatalf("expected ValidationError, got %T: %v", err, err)
			}
			if capture.calls != 0 {
				t.Fatalf("invalid request reached HTTP: %d calls", capture.calls)
			}
		})
	}
}

func TestEnterpriseContextOutcomeRejectsResponseBindingsAndCanonicalMutations(t *testing.T) {
	cases := []struct {
		name    string
		payload func(*testing.T, map[string]any) []byte
	}{
		{name: "duplicate outer key", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			outcome, err := json.Marshal(valid["outcome"])
			if err != nil {
				t.Fatal(err)
			}
			return []byte(fmt.Sprintf(`{"schema_version":1,"tenant_id":%q,"tenant_id":%q,"outcome":%s}`, enterpriseTestTenant, enterpriseTestOther, outcome))
		}},
		{name: "unknown outer field", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			mutated := map[string]any{}
			for key, value := range valid {
				mutated[key] = value
			}
			mutated["unexpected"] = true
			return enterpriseOutcomeResponse(t, mutated)
		}},
		{name: "foreign tenant", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			mutated := map[string]any{}
			for key, value := range valid {
				mutated[key] = value
			}
			mutated["tenant_id"] = enterpriseTestOther
			return enterpriseOutcomeResponse(t, mutated)
		}},
		{name: "nested schema", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			valid["outcome"].(map[string]any)["schema_version"] = int64(2)
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "original receipt digest", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			valid["outcome"].(map[string]any)["original_receipt_digest"] = enterpriseOutcomeDecisionDigest
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "receipt document digest", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			valid["outcome"].(map[string]any)["receipt_digest"] = enterpriseOutcomeReceiptDigest
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "receipt identity", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			valid["outcome"].(map[string]any)["receipt_id"] = enterpriseOutcomeDecisionDigest
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "invalid acceptance", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			valid["outcome"].(map[string]any)["acceptance"] = "unknown"
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "nonboolean replay", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			valid["outcome"].(map[string]any)["already_recorded"] = int64(1)
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "noncanonical document", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			outcome := valid["outcome"].(map[string]any)
			enterpriseOutcomeRebindDocument(t, outcome, " "+outcome["receipt_document_json"].(string), true)
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "document identity", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			outcome := valid["outcome"].(map[string]any)
			mutated := strings.Replace(outcome["receipt_document_json"].(string),
				"2026-09-20T00:00:00Z", "2026-09-21T00:00:00Z", 1)
			enterpriseOutcomeRebindDocument(t, outcome, mutated, false)
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "document outcome", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			outcome := valid["outcome"].(map[string]any)
			document, _ := enterpriseOutcomeReceiptDocument(t, "enterprise-task", "rejected", enterpriseOutcomeDecisionDigest, enterpriseOutcomePreviousReceiptID)
			enterpriseOutcomeRebindDocument(t, outcome, document, true)
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "missing predecessor", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			outcome := valid["outcome"].(map[string]any)
			document, _ := enterpriseOutcomeReceiptDocument(t, "enterprise-task", "accepted", enterpriseOutcomeDecisionDigest, "")
			enterpriseOutcomeRebindDocument(t, outcome, document, true)
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "runtime evidence digest", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			outcome := valid["outcome"].(map[string]any)
			document, _ := enterpriseOutcomeReceiptDocument(t, "enterprise-task", "accepted", enterpriseOutcomeReceiptDigest, enterpriseOutcomePreviousReceiptID)
			enterpriseOutcomeRebindDocument(t, outcome, document, true)
			return enterpriseOutcomeResponse(t, valid)
		}},
		{name: "other task document", payload: func(t *testing.T, valid map[string]any) []byte {
			t.Helper()
			outcome := valid["outcome"].(map[string]any)
			document, _ := enterpriseOutcomeReceiptDocument(t, "other-task", "accepted", enterpriseOutcomeDecisionDigest, enterpriseOutcomePreviousReceiptID)
			enterpriseOutcomeRebindDocument(t, outcome, document, true)
			return enterpriseOutcomeResponse(t, valid)
		}},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			value, _ := enterpriseOutcomeResponseValue(
				t,
				enterpriseTestTenant,
				"enterprise-task",
				"accepted",
				enterpriseOutcomeDecisionDigest,
				enterpriseOutcomePreviousReceiptID,
				"",
				enterpriseOutcomeReceiptDigest,
				false,
			)
			server, capture := newEnterpriseOutcomeServer(t, func(_ []byte) []byte {
				return test.payload(t, value)
			})
			_, err := newEnterpriseOutcomeClient(t, server).ContextOutcome(enterpriseOutcomeRequestFixture())
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected EngineProtocolError, got %T: %v", err, err)
			}
			if capture.calls != 1 {
				t.Fatalf("expected one bounded response parse, got %d calls", capture.calls)
			}
		})
	}
}

func TestEnterpriseContextOutcomeRejectsLexicalResponseIntegers(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{name: "outer schema", mutate: func(value map[string]any) {
			value["schema_version"] = json.Number("1.0")
		}},
		{name: "nested schema", mutate: func(value map[string]any) {
			value["outcome"].(map[string]any)["schema_version"] = json.Number("1e0")
		}},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			value, _ := enterpriseOutcomeResponseValue(
				t,
				enterpriseTestTenant,
				"enterprise-task",
				"accepted",
				enterpriseOutcomeDecisionDigest,
				enterpriseOutcomePreviousReceiptID,
				"",
				enterpriseOutcomeReceiptDigest,
				false,
			)
			test.mutate(value)
			payload := enterpriseOutcomeResponse(t, value)
			server, _ := newEnterpriseOutcomeServer(t, func(_ []byte) []byte { return payload })
			_, err := newEnterpriseOutcomeClient(t, server).ContextOutcome(enterpriseOutcomeRequestFixture())
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected EngineProtocolError, got %T: %v", err, err)
			}
		})
	}
}
