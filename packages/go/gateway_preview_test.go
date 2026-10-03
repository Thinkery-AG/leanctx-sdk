// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"context"
	"encoding/json"
	"errors"
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

func TestGatewayPreviewRealEngineResponsesParse(t *testing.T) {
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

func TestGatewayPreviewRejectsEveryInvalidCase(t *testing.T) {
	for _, name := range readGatewayManifest(t).Invalid {
		_, err := ParseEgressAdmission(loadGatewayFixture(t, "invalid", name))
		var protocol *EngineProtocolError
		if !errors.As(err, &protocol) {
			t.Fatalf("%s: expected EngineProtocolError, got %v", name, err)
		}
	}
}

func TestGatewayPreviewLiveEngine(t *testing.T) {
	binary := os.Getenv("LEANCTX_ENGINE_BINARY")
	if binary == "" {
		t.Skip("needs a real Engine (LEANCTX_ENGINE_BINARY)")
	}
	client, err := NewSubprocessEngineClient(SubprocessEngineClientOptions{EngineBinary: binary})
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
	body, _ = json.Marshal(restricted.Body)
	if *restricted.Classification != "restricted" || restricted.Receipt.Outcome != "withheld" ||
		strings.Contains(string(body), "customer list") ||
		!slices.Contains(restricted.Receipt.Decisions[0].ReasonCodes, "destination.remote_restricted") {
		t.Fatal("restricted content was not withheld from the remote model")
	}
	marked := admit("Classification: Confidential\nboard minutes", "openai", "https://api.openai.com")
	if marked.Disposition != "forward" || *marked.Classification != "confidential" || marked.Receipt.Destination.Locality != "remote" {
		t.Fatal("marked content was not classified")
	}
}
