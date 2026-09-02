package leanctx

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

func testRepositoryRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", ".."))
}

func testSource(t *testing.T, root string) *ContextSource {
	t.Helper()
	source, err := NewContextSource("fixture/source.txt", ContextSourceOptions{ProjectRoot: root})
	if err != nil {
		t.Fatal(err)
	}
	return source
}

func testFixtureView(t *testing.T, source ContextSource) *ContextView {
	t.Helper()
	text := "fresh synthetic view\n"
	outputDigest := sha256Hex([]byte(text))
	sourceDigest := sha256Hex([]byte("fresh synthetic source\n"))
	inputRef := "input:synthetic-request-sha256:" + "b" + "\x00"
	inputRef = "input:synthetic-request-sha256:" + "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	sourceRef := "source:synthetic-path-sha256:" + "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	invocationID := "engine-invocation-synthetic"
	invocation := map[string]any{
		"schema_version": int64(1),
		"invocation_id":  invocationID,
		"engine": map[string]any{
			"engine_id": "lean-ctx-local", "engine_version": "3.9.20",
		},
		"operation": map[string]any{
			"capability_id":      "capability://leanctx/context-optimization",
			"capability_version": "1.0.0",
		},
		"input_ref":    inputRef,
		"input_digest": "sha256:" + "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
		"source_refs":  []string{inputRef, sourceRef},
		"policy_admission": map[string]any{
			"policy_ref": "policy:synthetic", "decision": "admitted",
		},
	}
	valueOne, valueTwo := int64(1), int64(2)
	measurementOne, err := NewContextMeasurement("input_tokens", "token", "measured", &valueOne)
	if err != nil {
		t.Fatal(err)
	}
	measurementTwo, err := NewContextMeasurement("output_tokens", "token", "measured", &valueTwo)
	if err != nil {
		t.Fatal(err)
	}
	link, err := NewContextReceiptLink(1, "engine-receipt-synthetic", "receipt:sha256:"+stringsRepeat("d", 64), "sha256:"+stringsRepeat("d", 64), invocationID)
	if err != nil {
		t.Fatal(err)
	}
	observation := map[string]any{
		"schema_version": int64(1), "invocation_id": invocationID, "status": "succeeded",
		"output_ref": "output:" + outputDigest[len("sha256:"):], "output_digest": outputDigest,
		"source_lineage": []string{inputRef, sourceRef},
		"measurements":   []ContextMeasurement{*measurementOne, *measurementTwo},
		"failure":        nil, "receipt_link": link,
	}
	view, err := NewContextView(ContextViewOptions{
		Source: source, Text: &text, OutputRef: stringPointer("output:" + outputDigest[len("sha256:"):]), OutputDigest: &outputDigest,
		SourceRef: sourceRef, SourceDigest: sourceDigest, RecoveryRef: inputRef,
		Status: EngineStatusSucceeded, Measurements: []ContextMeasurement{*measurementOne, *measurementTwo},
		ReceiptLink: link, Invocation: invocation, Observation: observation,
	})
	if err != nil {
		t.Fatal(err)
	}
	return view
}

func stringPointer(value string) *string { return &value }

func stringsRepeat(value string, count int) string {
	result := make([]byte, count)
	for index := range result {
		result[index] = value[0]
	}
	return string(result)
}

func readJSONFile(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	value, err := strictJSONLoads(data, filepath.Base(path))
	if err != nil {
		t.Fatal(err)
	}
	return value.(map[string]any)
}

func TestProductSerializationFingerprints(t *testing.T) {
	fixture := readJSONFile(t, filepath.Join(testRepositoryRoot(t), "fixtures", "sdk-v1", "serialization-sha256.json"))
	source, err := NewContextSource("fixture/source.txt", ContextSourceOptions{ProjectRoot: "/PROJECT"})
	if err != nil {
		t.Fatal(err)
	}
	plan, err := NewContextPlan("session-fixed", "task-fixed", "inspect", *source)
	if err != nil {
		t.Fatal(err)
	}
	view := testFixtureView(t, *source)
	receipt, err := NewContextReceipt("session-fixed", "task-fixed", &plan.PlanID, view, HostOutcomeCompleted, IntegritySealed, ReceiptOptions{Usage: map[string]any{"requests": int64(1)}})
	if err != nil {
		t.Fatal(err)
	}
	values := map[string]any{
		"ContextSource":  source.ToDict(),
		"ContextPlan":    plan.ToDict(),
		"ContextView":    view.ToDict(),
		"ContextReceipt": receipt.ToDict(),
		"ContextSession": map[string]any{"session_id": "session-fixed", "task_id": "task-fixed", "task": "inspect", "state": string(SessionStateCreated)},
	}
	for name, expectedValue := range fixture {
		value, ok := values[name]
		if !ok {
			t.Fatalf("fixture names unsupported primitive %q", name)
		}
		payload, err := canonicalJSON(value)
		if err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(payload)
		actual := hex.EncodeToString(digest[:])
		if actual != expectedValue.(string) {
			t.Errorf("%s hash = %s, want %s\ncanonical=%s", name, actual, expectedValue, payload)
		}
	}
}

func TestEngineFixtureResponseBuildsVerifiedView(t *testing.T) {
	fixture := readJSONFile(t, filepath.Join(testRepositoryRoot(t), "fixtures", "engine-interface-v1", "r1-success.json"))
	response := fixture["response"].(map[string]any)
	payload, err := canonicalJSON(response)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := parseResponse(payload)
	if err != nil {
		t.Fatal(err)
	}
	source, err := NewContextSource("fixture/source.txt", ContextSourceOptions{ProjectRoot: "/PROJECT"})
	if err != nil {
		t.Fatal(err)
	}
	view, err := buildView(*source, parsed)
	if err != nil {
		t.Fatal(err)
	}
	if !view.Verify() || view.Status != EngineStatusSucceeded || view.RequireTextValue(t) != "Synthetic context view\n" {
		t.Fatalf("fixture view was not verified: %#v", view)
	}
	if view.RecoveryBinding()["source_ref"] != "source:fixture-path-sha256:"+stringsRepeat("a", 64) {
		t.Fatalf("unexpected recovery binding: %#v", view.RecoveryBinding())
	}
	if got := parsed.Observation["source_lineage"].([]string); !reflect.DeepEqual(got, parsed.Invocation["source_refs"].([]string)) {
		t.Fatalf("lineage mismatch: %#v", got)
	}
}

func (v *ContextView) RequireTextValue(t *testing.T) string {
	t.Helper()
	value, err := v.RequireText()
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func TestCanonicalJSONRejectsUnsafeAndCyclicValues(t *testing.T) {
	cases := []any{json.Number("1.1"), json.Number("-0"), json.Number("9007199254740992"), map[string]any{}}
	cases[3].(map[string]any)["self"] = cases[3]
	for _, value := range cases {
		if _, err := canonicalJSON(value); err == nil {
			t.Errorf("canonicalJSON(%#v) unexpectedly succeeded", value)
		}
	}
	if got, err := canonicalJSON(map[string]any{"b": int64(2), "a": int64(1)}); err != nil || string(got) != `{"a":1,"b":2}` {
		t.Fatalf("canonical ordering: %q, %v", got, err)
	}
}

func TestStrictJSONRejectsDuplicateTrailingAndInvalidTopLevel(t *testing.T) {
	for _, payload := range []string{`{"a":1,"a":2}`, `{"a":1} trailing`, `[]`, `null`, `{"a":NaN}`} {
		if _, err := strictJSONLoads([]byte(payload), "test"); err == nil {
			t.Errorf("strictJSONLoads(%q) unexpectedly succeeded", payload)
		}
	}
	if _, err := strictJSONLoads([]byte(`{"a":1}`), "test"); err != nil {
		t.Fatal(err)
	}
}

func TestProductProjectionsAreDetached(t *testing.T) {
	source := testSource(t, "/PROJECT")
	projection := source.ToDict()
	projection["path"] = "changed.txt"
	if source.Path != "fixture/source.txt" {
		t.Fatal("source was mutated through projection")
	}
	plan, err := NewContextPlan("s", "t", "task", *source)
	if err != nil {
		t.Fatal(err)
	}
	intent := plan.ToIntent()
	intentSource := intent["source"].(map[string]any)
	intentSource["path"] = "changed.txt"
	if plan.Source.Descriptor()["path"] != "fixture/source.txt" {
		t.Fatal("plan was mutated through projection")
	}
}

func TestProductValidationAndErrorGuidance(t *testing.T) {
	if _, err := NewContextSource("../escape.txt", ContextSourceOptions{ProjectRoot: "/PROJECT"}); err == nil {
		t.Fatal("path escape unexpectedly accepted")
	}
	if _, err := NewContextSource("bad\x00path", ContextSourceOptions{ProjectRoot: "/PROJECT"}); err == nil {
		t.Fatal("NUL path unexpectedly accepted")
	}
	timeout := NewEngineTimeout("sensitive host detail")
	guidance := timeout.AsDict()
	if guidance.Guidance != "retry within host policy or use explicit bounded fail-open" || guidance.Code != "engine_timeout" || !guidance.Retryable || !guidance.DegradeAllowed || guidance.AbortRequired {
		t.Fatalf("unexpected timeout guidance: %#v", guidance)
	}
	if bytes.Contains([]byte(string(mustJSON(guidance))), []byte("sensitive")) {
		t.Fatal("error guidance exposed a message")
	}
	var typed *EngineTimeout
	if !errors.As(timeout, &typed) {
		t.Fatal("errors.As did not preserve concrete type")
	}
}

func mustJSON(value any) []byte {
	payload, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return payload
}
