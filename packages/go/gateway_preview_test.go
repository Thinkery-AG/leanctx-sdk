// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

// Source resolution: preserve private restricted-refusal and preparation/report cases.
// Public context-store cases are added separately in context_store_test.go.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

const gatewayFixtures = "../../fixtures/gateway-preview-v1"

type gatewayManifest struct {
	Valid map[string]struct {
		Disposition    string  `json:"disposition"`
		Classification *string `json:"classification"`
		Outcome        string  `json:"outcome"`
		Decisions      int     `json:"decisions"`
		Denied         int     `json:"denied"`
		Redactions     int64   `json:"redactions"`
		Signals        int     `json:"signals"`
	} `json:"valid"`
	Invalid []string `json:"invalid"`
}

func loadGatewayFixture(t *testing.T, kind, name string) any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(gatewayFixtures, kind, name+".json"))
	if err != nil {
		t.Fatal(err)
	}
	document, err := strictJSONLoads(raw, "fixture")
	if err != nil {
		t.Fatal(err)
	}
	return document
}

func readGatewayManifest(t *testing.T) gatewayManifest {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(gatewayFixtures, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest gatewayManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	return manifest
}

func TestGatewayPreviewRealInstanceResponsesParse(t *testing.T) {
	for name, expected := range readGatewayManifest(t).Valid {
		admission, err := ParseEgressAdmission(loadGatewayFixture(t, "valid", name))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		receipt := admission.Receipt
		denied := 0
		for _, decision := range receipt.Decisions {
			if decision.Disposition == "deny" {
				denied++
			}
		}
		if admission.Disposition != expected.Disposition ||
			(admission.Classification == nil) != (expected.Classification == nil) ||
			(expected.Classification != nil && *admission.Classification != *expected.Classification) ||
			receipt.Outcome != expected.Outcome || len(receipt.Decisions) != expected.Decisions ||
			denied != expected.Denied || receipt.Security["redactions"] != expected.Redactions ||
			len(receipt.Signals()) != expected.Signals || !admission.MaySend() || receipt.Principal.Kind != "unknown" {
			t.Fatalf("%s: parsed shape differs from the manifest", name)
		}
	}
}

func TestGatewayPreviewReceiptV2ObjectsAreKeyedLikeTheInstance(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(gatewayFixtures, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest struct {
		KeyedV2 struct {
			Fixture     string `json:"fixture"`
			KeyHex      string `json:"key_hex"`
			KeyID       string `json:"key_id"`
			ObjectText  string `json:"object_text"`
			KeyedObject string `json:"keyed_object"`
		} `json:"keyed_v2"`
	}
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	vector := manifest.KeyedV2
	key, err := hex.DecodeString(vector.KeyHex)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte(vector.ObjectText))
	plain := "sha256:" + hex.EncodeToString(sum[:])
	if id, err := ReceiptKeyID(key); err != nil || id != vector.KeyID {
		t.Fatalf("key id %q, %v", id, err)
	}
	if keyed, err := KeyedObjectDigest(key, plain); err != nil || keyed != vector.KeyedObject {
		t.Fatalf("keyed object %q, %v", keyed, err)
	}
	if other, _ := KeyedObjectDigest(make([]byte, 32), plain); other == vector.KeyedObject {
		t.Fatal("another key must give another digest")
	}
	admission, err := ParseEgressAdmission(loadGatewayFixture(t, "valid", vector.Fixture))
	if err != nil {
		t.Fatal(err)
	}
	receipt := admission.Receipt
	if receipt.SchemaVersion != 2 || receipt.ObjectDigest == nil ||
		receipt.ObjectDigest.Algorithm != "hmac-sha256" || receipt.ObjectDigest.KeyID != vector.KeyID ||
		len(receipt.Decisions) != 1 || receipt.Decisions[0].Object != vector.KeyedObject ||
		receipt.Decisions[0].Object == plain {
		t.Fatalf("v2 receipt does not name the keyed object: %+v", receipt)
	}
	v1, err := ParseEgressAdmission(loadGatewayFixture(t, "valid", "masked"))
	if err != nil || v1.Receipt.SchemaVersion != 1 || v1.Receipt.ObjectDigest != nil {
		t.Fatalf("v1 receipt: %v", err)
	}
}

func TestGatewayPreviewRejectsEveryInvalidCase(t *testing.T) {
	for _, name := range readGatewayManifest(t).Invalid {
		_, err := ParseEgressAdmission(loadGatewayFixture(t, "invalid", name))
		var protocol *InstanceProtocolError
		if !errors.As(err, &protocol) {
			t.Fatalf("%s: expected InstanceProtocolError, got %v", name, err)
		}
	}
}

func gatewayJSONDocument(t *testing.T, value any) any {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	document, err := strictJSONLoads(raw, "test document")
	if err != nil {
		t.Fatal(err)
	}
	return document
}

func TestEgressPreparePreservesBodyAndReportIsHostReported(t *testing.T) {
	prepared := `{ "model" : "m" }`
	digest := sha256.Sum256([]byte(prepared))
	decision := map[string]any{
		"schema_version": 1, "disposition": "forward", "body": map[string]any{"model": "m"},
		"classification": "internal",
		"receipt": map[string]any{
			"schema_version": 1, "receipt_id": "egress-test", "mode": "governed",
			"principal":   map[string]any{"kind": "unknown"},
			"destination": map[string]any{"provider": "openai", "locality": "remote"},
			"sources":     map[string]any{"inspected": 0, "permitted": 0, "selected": 0, "blocked": 0},
			"security": map[string]any{"redactions": 0, "blocked_objects": 0, "quarantined_objects": 0,
				"injection_signals": 0, "incomplete_coverage": 0},
			"tokens": map[string]any{"original": 0, "delivered": 0}, "outcome": "delivered",
			"duration_us": 0, "final_context": fmt.Sprintf("sha256:%x", digest),
		},
	}
	response := map[string]any{"schema_version": 2, "decision": decision, "prepared_body": prepared,
		"admission_receipt_digest": "sha256:" + strings.Repeat("b", 64)}
	parsed, err := ParseEgressPreparation(gatewayJSONDocument(t, response))
	if err != nil || parsed.PreparedBody == nil || *parsed.PreparedBody != prepared || !parsed.MaySend() {
		t.Fatalf("exact prepared body was not preserved: %#v, %v", parsed, err)
	}
	parsePreparation := func(candidate map[string]any) error {
		_, err := ParseEgressPreparation(gatewayJSONDocument(t, map[string]any{
			"schema_version": 2, "decision": candidate, "prepared_body": prepared,
		}))
		return err
	}
	if err := parsePreparation(map[string]any{"schema_version": 1, "disposition": "forward",
		"body": map[string]any{"model": "m"}}); err != nil {
		t.Fatalf("receipt-less best-effort forward was rejected: %v", err)
	}
	rewritten := make(map[string]any, len(decision))
	for key, value := range decision {
		rewritten[key] = value
	}
	rewritten["disposition"] = "rewritten"
	if err := parsePreparation(rewritten); err != nil {
		t.Fatalf("delivered rewritten decision was rejected: %v", err)
	}
	for _, disposition := range []string{"forward", "rewritten"} {
		for _, outcome := range []string{"withheld", "failed"} {
			badReceipt := make(map[string]any)
			for key, value := range decision["receipt"].(map[string]any) {
				badReceipt[key] = value
			}
			badReceipt["outcome"] = outcome
			delete(badReceipt, "final_context")
			badDecision := make(map[string]any, len(decision))
			for key, value := range decision {
				badDecision[key] = value
			}
			badDecision["disposition"] = disposition
			badDecision["receipt"] = badReceipt
			if err := parsePreparation(badDecision); err == nil {
				t.Fatalf("%s with %s receipt was accepted", disposition, outcome)
			}
		}
	}
	refusedDelivered := map[string]any{"schema_version": 1, "disposition": "refused",
		"refusal": "withheld", "receipt": decision["receipt"]}
	if _, err := ParseEgressPreparation(gatewayJSONDocument(t, map[string]any{
		"schema_version": 2, "decision": refusedDelivered,
	})); err == nil {
		t.Fatal("refused decision with delivered receipt was accepted")
	}
	response["prepared_body"] = `{"model":"m"}`
	if _, err := ParseEgressPreparation(gatewayJSONDocument(t, response)); err == nil {
		t.Fatal("prepared bytes whose digest differs from the receipt were accepted")
	}
	refused, err := ParseEgressPreparation(gatewayJSONDocument(t, map[string]any{"schema_version": 2,
		"decision": map[string]any{"schema_version": 1, "disposition": "refused", "refusal": "withheld"}}))
	if err != nil || refused.MaySend() {
		t.Fatalf("refusal became dispatchable: %#v, %v", refused, err)
	}

	request := EgressHostReportRequest{AdmissionReceiptDigest: "sha256:" + strings.Repeat("b", 64),
		Attempt: 2, Transport: "http", Result: "no_response_observed", Failure: ptr("timeout")}
	observation := map[string]any{"schema_version": 1, "admission_receipt_digest": request.AdmissionReceiptDigest,
		"attempt": 2, "transport": "http", "observer": "host_reported", "result": "no_response_observed", "failure": "timeout"}
	observationBytes, _ := json.Marshal(observation)
	observationHash := sha256.Sum256(observationBytes)
	report, err := ParseEgressHostReport(gatewayJSONDocument(t, map[string]any{"schema_version": 1,
		"observation_digest": fmt.Sprintf("sha256:%x", observationHash), "observation": observation}), request)
	if err != nil || report.Observation["result"] != "no_response_observed" {
		t.Fatalf("host report was not retained: %#v, %v", report, err)
	}
	observation["observer"] = "engine_observed"
	if _, err := ParseEgressHostReport(gatewayJSONDocument(t, map[string]any{"schema_version": 1,
		"observation_digest": "sha256:" + strings.Repeat("a", 64), "observation": observation}), request); err == nil {
		t.Fatal("an InstanceObserved claim was accepted from a host report")
	}
}

func ptr(value string) *string { return &value }

func TestGatewayPreviewLiveInstance(t *testing.T) {
	binary := instanceEnv("LEANCTX_INSTANCE_BINARY", "LEANCTX_ENGINE_BINARY")
	if binary == "" {
		t.Skip("needs a real instance (LEANCTX_INSTANCE_BINARY)")
	}
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: binary})
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	admit := func(text, provider, base string) *EgressAdmission {
		t.Helper()
		admission, err := client.AdmitEgress(context.Background(), root, EgressRequest{
			Provider: provider, UpstreamBase: base,
			Body: map[string]any{"model": "m", "temperature": 0.2,
				"messages": []any{map[string]any{"role": "user", "content": text}}},
		})
		if err != nil {
			t.Fatal(err)
		}
		return admission
	}
	credential := "AKIA" + "Q3EGRZ7LIVEX4KEY"
	masked := admit("deploy fails with "+credential, "openai", "https://api.openai.com")
	body, _ := json.Marshal(masked.Body)
	if masked.Disposition != "rewritten" || strings.Contains(string(body), credential) || masked.Receipt.Security["redactions"] != 1 {
		t.Fatal("the credential was not masked")
	}
	restricted := admit("Classification: Secret\nroot cause and customer list", "anthropic", "https://api.anthropic.com")
	if *restricted.Classification != "restricted" || restricted.Receipt.Outcome != "withheld" ||
		restricted.Disposition != "refused" || restricted.MaySend() || restricted.Body != nil ||
		!slices.Contains(restricted.Receipt.Decisions[0].ReasonCodes, "destination.remote_restricted") {
		t.Fatal("restricted content was not withheld from the remote model")
	}
	marked := admit("Classification: Confidential\nboard minutes", "openai", "https://api.openai.com")
	if marked.Disposition != "forward" || *marked.Classification != "confidential" || marked.Receipt.Destination.Locality != "remote" {
		t.Fatal("marked content was not classified")
	}

	model := "host-selected-model-2026"
	prepared, err := client.PrepareEgress(context.Background(), root, EgressRequest{
		Provider: "openai", UpstreamBase: "https://api.openai.com",
		Body: map[string]any{"model": model, "messages": []any{
			map[string]any{"role": "user", "content": "deploy fails with " + credential},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !prepared.MaySend() || prepared.PreparedBody == nil || prepared.AdmissionReceiptDigest == nil ||
		prepared.Decision == nil || prepared.Decision.Receipt == nil {
		t.Fatal("preparation did not return a dispatchable body and receipt")
	}
	var preparedBody map[string]any
	if err := json.Unmarshal([]byte(*prepared.PreparedBody), &preparedBody); err != nil {
		t.Fatal(err)
	}
	if preparedBody["model"] != model || strings.Contains(*prepared.PreparedBody, credential) ||
		prepared.Decision.Receipt.Security["redactions"] != 1 ||
		prepared.Decision.Receipt.Destination.Provider != "openai" {
		t.Fatal("preparation changed the caller model/provider or left the credential")
	}
	if prepared.Decision.Receipt.Destination.Model == nil || *prepared.Decision.Receipt.Destination.Model != model {
		t.Fatal("receipt does not bind the host-selected model")
	}
	bodyDigest := sha256.Sum256([]byte(*prepared.PreparedBody))
	if prepared.Decision.Receipt.FinalContext == nil ||
		*prepared.Decision.Receipt.FinalContext != fmt.Sprintf("sha256:%x", bodyDigest) {
		t.Fatal("receipt does not bind the exact prepared body bytes")
	}

	reportRequest := EgressHostReportRequest{
		AdmissionReceiptDigest: *prepared.AdmissionReceiptDigest,
		Attempt:                1, Transport: "http", Result: "no_response_observed", Failure: ptr("timeout"),
	}
	first, err := client.ReportEgress(context.Background(), root, reportRequest)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := client.ReportEgress(context.Background(), root, reportRequest)
	if err != nil {
		t.Fatal(err)
	}
	if first.Observation["observer"] != "host_reported" || first.Observation["result"] != "no_response_observed" ||
		first.Observation["failure"] != "timeout" || first.ObservationDigest != replay.ObservationDigest {
		t.Fatal("identical host timeout replay changed or lost its HostReported observation")
	}
	status := uint16(503)
	conflictingReport := reportRequest
	conflictingReport.Result = "response_observed"
	conflictingReport.HTTPStatus = &status
	conflictingReport.Failure = nil
	_, err = client.ReportEgress(context.Background(), root, conflictingReport)
	var executionErr *InstanceExecutionError
	if !errors.As(err, &executionErr) || !strings.Contains(err.Error(), "egress_observation_unavailable") {
		t.Fatalf("expected the instance to reject the contradictory report, got %v", err)
	}
}
