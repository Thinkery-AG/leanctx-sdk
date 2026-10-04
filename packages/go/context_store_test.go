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

const storeFixtures = "../../fixtures/context-store-preview-v1"

type storeManifest struct {
	Documents map[string]struct {
		Valid   []string `json:"valid"`
		Invalid []string `json:"invalid"`
	} `json:"documents"`
}

func loadStoreFixture(t *testing.T, kind, validity, name string) any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(storeFixtures, kind, validity, name+".json"))
	if err != nil {
		t.Fatal(err)
	}
	document, err := strictJSONLoads(raw, "fixture")
	if err != nil {
		t.Fatal(err)
	}
	return document
}

func TestContextStoreFixturesParseAndInvalidOnesAreRejected(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(storeFixtures, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest storeManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	parsers := map[string]func(any) error{
		"evidence": func(value any) error { _, err := ParsePolicyEvidence(value); return err },
		"lineage":  func(value any) error { _, err := ParseTaskLineage(value); return err },
	}
	for kind, parse := range parsers {
		for _, name := range manifest.Documents[kind].Valid {
			if err := parse(loadStoreFixture(t, kind, "valid", name)); err != nil {
				t.Errorf("%s/%s: %v", kind, name, err)
			}
		}
		for _, name := range manifest.Documents[kind].Invalid {
			err := parse(loadStoreFixture(t, kind, "invalid", name))
			var protocol *EngineProtocolError
			if !errors.As(err, &protocol) {
				t.Errorf("%s/%s: want EngineProtocolError, got %v", kind, name, err)
			}
		}
	}
}

func TestContextStoreUnmeasuredNeverReadsAsMeasured(t *testing.T) {
	evidence, err := ParsePolicyEvidence(loadStoreFixture(t, "evidence", "valid", "rich"))
	if err != nil {
		t.Fatal(err)
	}
	unmeasured := evidence.Records[1]
	if unmeasured.Quality.Measured || unmeasured.Quality.Retained != nil || unmeasured.Security.Regressions != nil {
		t.Fatalf("unmeasured evidence carries values: %+v", unmeasured)
	}
	if measured := evidence.Records[0].Security; !measured.Measured || *measured.Regressions != 0 {
		t.Fatalf("measured security lost: %+v", measured)
	}
	lineage, err := ParseTaskLineage(loadStoreFixture(t, "lineage", "valid", "gapped"))
	if err != nil {
		t.Fatal(err)
	}
	if lineage.IsComplete() || lineage.Deliveries[0].Summary != nil {
		t.Fatalf("gapped lineage reads complete: %+v", lineage)
	}
}

func TestContextStoreLiveEngine(t *testing.T) {
	binary := os.Getenv("LEANCTX_ENGINE_BINARY")
	if binary == "" {
		t.Skip("needs a real Engine")
	}
	client, err := NewSubprocessEngineClient(SubprocessEngineClientOptions{EngineBinary: binary})
	if err != nil {
		t.Fatal(err)
	}
	// Private Engine storage refuses symlinked ancestors (macOS /var).
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	evidence, err := client.ReadPolicyEvidence(context.Background(), root, ContextStoreScope{ProjectID: "sdk-preview-fresh"})
	if err != nil {
		t.Fatal(err)
	}
	if len(evidence.Records) != 0 || len(evidence.Evaluations) != 0 {
		t.Fatalf("fresh scope has evidence: %+v", evidence)
	}
	lineage, err := client.ReadTaskLineage(context.Background(), root, "sdk-preview-unknown-task",
		ContextStoreScope{ProjectID: "sdk-preview-fresh", TenantID: "tenant-a"})
	if err != nil {
		t.Fatal(err)
	}
	if lineage.Outcome != "unknown" || !slices.Contains(lineage.Gaps, "no_plan_recorded") || !strings.Contains(*lineage.Scope, "tenant-a") {
		t.Fatalf("unknown task lineage: %+v", lineage)
	}
}
