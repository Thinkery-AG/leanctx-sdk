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

const enterpriseMaterializedText = "materialized enterprise \uFFFD source"

func enterpriseMaterializationResponseValue(
	t *testing.T,
	tenantID, taskID, sourceID, permission string,
	content string,
) (map[string]any, string) {
	t.Helper()
	planResponse := enterprisePlanResponseValue(t, tenantID, taskID, sourceID, 64, permission)
	sourcePlan := planResponse["plan"].(map[string]any)
	bindingDigest := sourcePlan["binding_digest"].(string)
	materialization := map[string]any{
		"schema_version":           int64(1),
		"transport_version":        int64(1),
		"engine_interface_version": EngineInterfaceVersion,
		"plan":                     sourcePlan,
		"materialized_digest":      sha256Hex([]byte(content)),
		"materialized_token_count": uint64(1),
		"content":                  content,
	}
	return map[string]any{
		"schema_version":      int64(1),
		"tenant_id":           tenantID,
		"governance_revision": uint64(7),
		"materialization":     materialization,
	}, bindingDigest
}

func enterpriseMaterializationResponse(t *testing.T, value map[string]any) []byte {
	t.Helper()
	payload, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func enterpriseMaterializationRequest(
	t *testing.T,
	bindingDigest string,
	revision uint64,
	sourceIDs []string,
) EngineSourceMaterializationRequest {
	t.Helper()
	return EngineSourceMaterializationRequest{
		Planning:                   enterpriseTestRequest(t),
		SourceIDs:                  sourceIDs,
		ExpectedGovernanceRevision: revision,
		ExpectedBindingDigest:      bindingDigest,
	}
}

func TestEnterpriseContextMaterializeSourcesRoundTripAndBinding(t *testing.T) {
	value, bindingDigest := enterpriseMaterializationResponseValue(
		t,
		enterpriseTestTenant,
		"enterprise-task",
		enterpriseTestSource,
		"permitted",
		enterpriseMaterializedText,
	)
	server, capture := newEnterprisePlanServer(t, http.StatusOK, enterpriseMaterializationResponse(t, value))
	client := newEnterprisePlanClient(t, server)
	request := enterpriseMaterializationRequest(t, bindingDigest, 7, []string{enterpriseTestSource})
	evaluationTime := "2026-09-20T12:00:00Z"
	request.PlanningEvaluationTime = &evaluationTime

	result, err := client.ContextMaterializeSources(request)
	if err != nil {
		t.Fatal(err)
	}
	if result.SchemaVersion != 1 || result.TenantID != enterpriseTestTenant || result.GovernanceRevision != 7 {
		t.Fatalf("unexpected materialization envelope: %#v", result)
	}
	if result.RawResponse["tenant_id"] != enterpriseTestTenant {
		t.Fatalf("raw response lost tenant binding: %#v", result.RawResponse)
	}
	if result.Materialization["content"] != enterpriseMaterializedText {
		t.Fatalf("unexpected materialized content: %#v", result.Materialization)
	}
	if result.Materialization["materialized_digest"] != sha256Hex([]byte(enterpriseMaterializedText)) {
		t.Fatalf("materialized content digest was not preserved: %#v", result.Materialization)
	}
	if capture.calls != 1 || capture.method != http.MethodPost || capture.path != "/v1/engine/context-materialize" {
		t.Fatalf("unexpected HTTP request: %#v", capture)
	}
	if capture.authorization == "" || capture.contentType != "application/json" {
		t.Fatalf("unexpected HTTP authentication: %#v", capture)
	}
	body, err := strictJSONLoads(capture.body, "captured Enterprise materialization request")
	if err != nil {
		t.Fatal(err)
	}
	bodyObject := body.(map[string]any)
	if bodyObject["expected_binding_digest"] != bindingDigest {
		t.Fatalf("unexpected binding digest body: %#v", bodyObject)
	}
	if bodyObject["expected_governance_revision"] != json.Number("7") {
		t.Fatalf("unexpected governance revision body: %#v", bodyObject)
	}
	if bodyObject["planning_evaluation_time"] != evaluationTime {
		t.Fatalf("unexpected planning evaluation time body: %#v", bodyObject)
	}
	if _, sentTenant := bodyObject["tenant_id"]; sentTenant {
		t.Fatal("tenant binding was sent as an authorization override")
	}
}

func TestEnterpriseContextMaterializeSourcesRejectsBindingAndContentMismatches(t *testing.T) {
	valid, bindingDigest := enterpriseMaterializationResponseValue(
		t,
		enterpriseTestTenant,
		"enterprise-task",
		enterpriseTestSource,
		"permitted",
		enterpriseMaterializedText,
	)
	tests := []struct {
		name    string
		value   map[string]any
		request EngineSourceMaterializationRequest
	}{
		{
			name: "response revision",
			value: func() map[string]any {
				copy, _ := enterpriseMaterializationResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, "permitted", enterpriseMaterializedText)
				copy["governance_revision"] = uint64(8)
				return copy
			}(),
			request: enterpriseMaterializationRequest(t, bindingDigest, 7, []string{enterpriseTestSource}),
		},
		{
			name:    "expected binding",
			value:   valid,
			request: enterpriseMaterializationRequest(t, "sha256:"+strings.Repeat("0", 64), 7, []string{enterpriseTestSource}),
		},
		{
			name: "content digest",
			value: func() map[string]any {
				copy, _ := enterpriseMaterializationResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, "permitted", enterpriseMaterializedText)
				materialization := copy["materialization"].(map[string]any)
				materialization["materialized_digest"] = "sha256:" + strings.Repeat("0", 64)
				return copy
			}(),
			request: enterpriseMaterializationRequest(t, bindingDigest, 7, []string{enterpriseTestSource}),
		},
		{
			name: "token budget",
			value: func() map[string]any {
				copy, _ := enterpriseMaterializationResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, "permitted", enterpriseMaterializedText)
				materialization := copy["materialization"].(map[string]any)
				materialization["materialized_token_count"] = uint64(65)
				return copy
			}(),
			request: enterpriseMaterializationRequest(t, bindingDigest, 7, []string{enterpriseTestSource}),
		},
		{
			name: "denied source",
			value: func() map[string]any {
				denied, _ := enterpriseMaterializationResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, "denied", enterpriseMaterializedText)
				return denied
			}(),
			request: enterpriseMaterializationRequest(t, func() string {
				denied, binding := enterpriseMaterializationResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, "denied", enterpriseMaterializedText)
				_ = denied
				return binding
			}(), 7, []string{enterpriseTestSource}),
		},
		{
			name: "foreign source",
			value: func() map[string]any {
				foreign, _ := enterpriseMaterializationResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestOther, "permitted", enterpriseMaterializedText)
				return foreign
			}(),
			request: enterpriseMaterializationRequest(t, func() string {
				foreign, binding := enterpriseMaterializationResponseValue(t, enterpriseTestTenant, "enterprise-task", enterpriseTestOther, "permitted", enterpriseMaterializedText)
				_ = foreign
				return binding
			}(), 7, []string{enterpriseTestSource}),
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, capture := newEnterprisePlanServer(t, http.StatusOK, enterpriseMaterializationResponse(t, test.value))
			client := newEnterprisePlanClient(t, server)
			_, err := client.ContextMaterializeSourcesContext(context.Background(), test.request)
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected EngineProtocolError, got %T: %v", err, err)
			}
			if strings.Contains(err.Error(), "credential-test") {
				t.Fatal("materialization error leaked the bearer credential")
			}
			if capture.calls != 1 {
				t.Fatalf("expected one bounded materialization request, got %d", capture.calls)
			}
		})
	}
}

func TestEnterpriseContextMaterializeSourcesRejectsMalformedUnicode(t *testing.T) {
	value, bindingDigest := enterpriseMaterializationResponseValue(
		t,
		enterpriseTestTenant,
		"enterprise-task",
		enterpriseTestSource,
		"permitted",
		enterpriseMaterializedText,
	)
	validPayload := enterpriseMaterializationResponse(t, value)
	baselineServer, _ := newEnterprisePlanServer(t, http.StatusOK, validPayload)
	baselineRequest := enterpriseMaterializationRequest(t, bindingDigest, 7, []string{enterpriseTestSource})
	if _, err := newEnterprisePlanClient(t, baselineServer).ContextMaterializeSources(baselineRequest); err != nil {
		t.Fatalf("valid U+FFFD materialization baseline was rejected: %v", err)
	}
	replacement := []byte(string(rune(0xFFFD)))
	if bytes.Count(validPayload, replacement) != 1 {
		t.Fatalf("valid fixture must contain exactly one U+FFFD, got %d", bytes.Count(validPayload, replacement))
	}
	malformedSurrogate := bytes.Replace(validPayload, replacement, []byte(`\ud800`), 1)
	if bytes.Equal(malformedSurrogate, validPayload) || !json.Valid(malformedSurrogate) {
		t.Fatal("unpaired surrogate fixture must be changed while remaining syntactically valid JSON")
	}
	invalidUTF8 := bytes.Replace(validPayload, replacement, []byte{0xff}, 1)
	if bytes.Equal(invalidUTF8, validPayload) {
		t.Fatal("invalid UTF-8 fixture must change the valid payload")
	}

	for _, test := range []struct {
		name    string
		payload []byte
	}{
		{name: "unpaired surrogate", payload: malformedSurrogate},
		{name: "invalid UTF-8", payload: invalidUTF8},
	} {
		t.Run(test.name, func(t *testing.T) {
			server, _ := newEnterprisePlanServer(t, http.StatusOK, test.payload)
			client := newEnterprisePlanClient(t, server)
			request := enterpriseMaterializationRequest(t, bindingDigest, 7, []string{enterpriseTestSource})
			_, err := client.ContextMaterializeSourcesContext(context.Background(), request)
			if !enterpriseErrorIs[*EngineProtocolError](t, err) {
				t.Fatalf("expected EngineProtocolError, got %T: %v", err, err)
			}
			if strings.Contains(err.Error(), "credential-test") {
				t.Fatal("malformed response error leaked the bearer credential")
			}
		})
	}
}
