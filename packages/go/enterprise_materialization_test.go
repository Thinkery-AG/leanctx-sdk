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
) InstanceSourceMaterializationRequest {
	t.Helper()
	return InstanceSourceMaterializationRequest{
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
		request InstanceSourceMaterializationRequest
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
			if !enterpriseErrorIs[*InstanceProtocolError](t, err) {
				t.Fatalf("expected InstanceProtocolError, got %T: %v", err, err)
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
			if !enterpriseErrorIs[*InstanceProtocolError](t, err) {
				t.Fatalf("expected InstanceProtocolError, got %T: %v", err, err)
			}
			if strings.Contains(err.Error(), "credential-test") {
				t.Fatal("malformed response error leaked the bearer credential")
			}
		})
	}
}

func TestEnterpriseContextMaterializeSourcesV2KeepsTheCapabilityOutOfBodiesAndRenderings(t *testing.T) {
	value, bindingDigest := enterpriseMaterializationResponseValue(
		t, enterpriseTestTenant, "enterprise-task", enterpriseTestSource, "permitted", enterpriseMaterializedText,
	)
	// Assembled at run time: the source holds no capability-shaped text.
	secret := "lcp1_" + strings.Repeat("ab", 32)
	reference := "presenter:" + strings.Repeat("0123456789abcdef", 2)
	status := http.StatusOK
	var paths, shown, bodies []string
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		paths = append(paths, request.URL.Path)
		shown = append(shown, request.Header.Get(PresenterCapabilityHeader))
		bodies = append(bodies, string(body))
		if status != http.StatusOK {
			response.WriteHeader(status)
			return
		}
		grant := map[string]any{"presenter_ref": reference, "expires_at_unix": uint64(1_900_000_000)}
		if request.Header.Get(PresenterCapabilityHeader) == "" {
			grant["capability"] = secret
		}
		_, _ = response.Write(enterpriseMaterializationResponse(t, map[string]any{
			"schema_version":      int64(2),
			"tenant_id":           value["tenant_id"],
			"governance_revision": value["governance_revision"],
			"materialization": map[string]any{
				"schema_version": int64(2), "materialization": value["materialization"], "presenter": grant,
			},
		}))
	}))
	t.Cleanup(server.Close)
	client := newEnterprisePlanClient(t, server)
	request := InstanceSourceMaterializationRequestV2{
		InstanceSourceMaterializationRequest: enterpriseMaterializationRequest(t, bindingDigest, 7, []string{enterpriseTestSource}),
		PresenterTTLSeconds:                  600,
	}

	issued, err := client.ContextMaterializeSourcesV2(request)
	if err != nil {
		t.Fatal(err)
	}
	if issued.SchemaVersion != 2 || issued.Presenter.PresenterRef != reference || issued.Presenter.Capability.Reveal() != secret {
		t.Fatalf("unexpected issue result: %v", issued)
	}
	if issued.Materialization["content"] != enterpriseMaterializedText {
		t.Fatalf("unexpected materialized content: %v", issued.Materialization["content"])
	}
	encoded, encodeErr := json.Marshal(issued)
	for _, rendering := range []string{
		fmt.Sprint(issued.Presenter.Capability), fmt.Sprintf("%+v", issued), fmt.Sprintf("%#v", issued),
		fmt.Sprintf("%s %q %x", issued.Presenter.Capability, issued.Presenter.Capability, issued.Presenter.Capability),
		string(encoded),
	} {
		if strings.Contains(rendering, secret) {
			t.Fatal("a rendering of the result carries the capability")
		}
	}
	if encodeErr == nil {
		t.Fatal("a capability must refuse JSON encoding")
	}

	renew := request
	renew.PresenterTTLSeconds, renew.Presenter = 0, issued.Presenter.Capability
	renewed, err := client.ContextMaterializeSourcesV2(renew)
	if err != nil {
		t.Fatal(err)
	}
	if renewed.Presenter.Capability != nil || renewed.Presenter.PresenterRef != reference {
		t.Fatalf("a renewal returns no capability: %v", renewed.Presenter)
	}
	if len(paths) != 2 || paths[0] != "/v1/engine/context-materialize-v2" || shown[0] != "" || shown[1] != secret {
		t.Fatalf("the capability travels in the request header of a renewal only: %v", paths)
	}
	if strings.Contains(strings.Join(bodies, ""), secret) ||
		!strings.Contains(bodies[0], `"presenter":{"mode":"issue","ttl_seconds":600}`) ||
		!strings.Contains(bodies[1], `"presenter":{"mode":"renew"}`) {
		t.Fatal("a request body carries the capability or the wrong presenter mode")
	}

	both := renew
	both.PresenterTTLSeconds = 600
	for _, invalid := range []InstanceSourceMaterializationRequestV2{both, {InstanceSourceMaterializationRequest: request.InstanceSourceMaterializationRequest}} {
		if _, err := client.ContextMaterializeSourcesV2(invalid); !enterpriseErrorIs[*ValidationError](t, err) {
			t.Fatalf("expected ValidationError, got %T", err)
		}
	}
	if len(paths) != 2 {
		t.Fatal("an invalid presenter mode reached the network")
	}
	for _, unsupported := range []int{http.StatusNotFound, http.StatusNotImplemented} {
		status = unsupported
		var typed *UnsupportedInstanceError
		if _, err := client.ContextMaterializeSourcesV2(request); !errors.As(err, &typed) {
			t.Fatalf("status %d: expected UnsupportedInstanceError, got %T", unsupported, err)
		}
	}
}

func TestEnterpriseAHeldCapabilityIsUsedAndRevokedInTheHeaderOnly(t *testing.T) {
	// Assembled at run time: the source holds no capability-shaped text.
	secret := "lcp1_" + strings.Repeat("ab", 32)
	reference := "presenter:" + strings.Repeat("0123456789abcdef", 2)
	recoveryID := strings.Repeat("5e", 32)
	status, text := http.StatusOK, "ledger"
	var paths, shown, bodies []string
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		paths = append(paths, request.URL.Path)
		shown = append(shown, request.Header.Get(PresenterCapabilityHeader))
		bodies = append(bodies, string(body))
		if status != http.StatusOK {
			response.WriteHeader(status)
			return
		}
		if request.URL.Path == "/v1/engine/presenter-revoke" {
			_, _ = response.Write(enterpriseMaterializationResponse(t, map[string]any{
				"schema_version": int64(1), "tenant_id": enterpriseTestTenant,
				"revocation": map[string]any{"schema_version": int64(1), "presenter_ref": reference, "revoked": true},
			}))
			return
		}
		use := map[string]any{
			"schema_version": int64(1), "presenter_ref": reference, "usable": true,
			"source_ids": []string{enterpriseTestSource},
		}
		if strings.Contains(string(body), `"mode":"retain"`) {
			use["recovery_id"] = recoveryID
		}
		if strings.Contains(string(body), `"mode":"expand"`) {
			use["recovery_id"], use["content"] = recoveryID, text
		}
		_, _ = response.Write(enterpriseMaterializationResponse(t, map[string]any{
			"schema_version": int64(1), "tenant_id": enterpriseTestTenant,
			"governance_revision": int64(7), "use": use,
		}))
	}))
	t.Cleanup(server.Close)
	client := newEnterprisePlanClient(t, server)
	capability, err := NewPresenterCapability(secret)
	if err != nil {
		t.Fatal(err)
	}
	ask := func(mode PresentedUseMode, value string) (*PresentedUseResponse, error) {
		return client.PresentedUse(PresentedUseRequest{
			Presenter: capability, SourceIDs: []string{enterpriseTestSource},
			ExpectedGovernanceRevision: 7, Mode: mode, Value: value,
		})
	}

	kept, err := ask(PresentedUseRetain, "ledger")
	if err != nil || !kept.Usable || kept.RecoveryID != recoveryID || kept.Content != "" {
		t.Fatalf("unexpected retain result: %v %v", kept, err)
	}
	expanded, err := ask(PresentedUseExpand, kept.RecoveryID)
	if err != nil || expanded.Content != "ledger" || len(expanded.SourceIDs) != 1 {
		t.Fatalf("unexpected expand result: %v %v", expanded, err)
	}
	revoked, err := client.RevokePresenter(capability)
	if err != nil || !revoked.Revoked || revoked.PresenterRef != reference {
		t.Fatalf("unexpected revocation: %v %v", revoked, err)
	}
	if len(paths) != 3 || paths[0] != "/v1/engine/presented-use" || paths[2] != "/v1/engine/presenter-revoke" ||
		shown[0] != secret || shown[1] != secret || shown[2] != secret {
		t.Fatalf("the capability travels in the request header of every call: %v", paths)
	}
	if strings.Contains(strings.Join(bodies, ""), secret) {
		t.Fatal("a request body carries the capability")
	}
	for _, invalid := range [][2]string{{"expand", "../x"}, {"check", ""}, {"other", "ledger"}} {
		if _, err := ask(PresentedUseMode(invalid[0]), invalid[1]); !enterpriseErrorIs[*ValidationError](t, err) {
			t.Fatalf("expected ValidationError, got %T", err)
		}
	}
	if len(paths) != 3 {
		t.Fatal("an invalid use reached the network")
	}

	// An answer that hands the capability back inside the text, the one
	// capability refusal, and a suite without the route.
	text = "ledger " + secret
	if _, err := ask(PresentedUseExpand, recoveryID); !enterpriseErrorIs[*InstanceProtocolError](t, err) {
		t.Fatalf("expected InstanceProtocolError, got %T", err)
	}
	status = http.StatusForbidden
	var refused *PolicyAdmissionError
	if _, err := ask(PresentedUseExpand, recoveryID); !errors.As(err, &refused) {
		t.Fatalf("expected PolicyAdmissionError, got %T", err)
	}
	status = http.StatusNotFound
	var unsupported *UnsupportedInstanceError
	if _, err := ask(PresentedUseExpand, recoveryID); !errors.As(err, &unsupported) {
		t.Fatalf("expected UnsupportedInstanceError, got %T", err)
	}
}
