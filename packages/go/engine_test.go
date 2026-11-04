//go:build !windows

package leanctx

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func writeTestExecutable(t *testing.T, contents string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "fake-engine")
	if err := os.WriteFile(path, []byte(contents), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0700); err != nil {
		t.Fatal(err)
	}
	return path
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}

func writeFixtureResponse(t *testing.T, key string) string {
	t.Helper()
	fixture := readJSONFile(t, filepath.Join(testRepositoryRoot(t), "fixtures", "engine-interface-v1", "r1-success.json"))
	value := fixture[key]
	payload, err := canonicalJSON(value)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), key+".json")
	if err := os.WriteFile(path, append(payload, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func staticInstanceScript(t *testing.T, responsePath, recoveryPath string) string {
	t.Helper()
	return writeTestExecutable(t, "#!/bin/sh\ncase \"$2\" in\n"+
		"context-view) /bin/cat "+shellQuote(responsePath)+" ;;\n"+
		"recover) /bin/cat "+shellQuote(recoveryPath)+" ;;\n"+"*) /bin/echo 'engine:unsupported_mode' >&2; exit 1 ;;\n"+
		"esac\n")
}

func TestSubprocessInstanceClientUsesStrictFixtureAndCleansRequest(t *testing.T) {
	root := t.TempDir()
	responsePath := writeFixtureResponse(t, "response")
	recoveryPath := writeFixtureResponse(t, "recovery_response")
	binary := staticInstanceScript(t, responsePath, recoveryPath)
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: binary, Timeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	source := testSource(t, root)
	plan, err := NewContextPlan("s", "t", "inspect", *source)
	if err != nil {
		t.Fatal(err)
	}
	view, err := client.ContextViewContext(nil, plan)
	if err != nil {
		t.Fatal(err)
	}
	if !view.Verify() || view.Status != InstanceStatusSucceeded || view.EngineVersion() != "3.9.20" {
		t.Fatalf("unexpected instance view: %#v", view)
	}
	const digest = "sha256:6ef151f7f2bab27b80625e6f183e31599f3f639314fcce71a9c1480702d94241"
	recovered, err := client.RecoverContext(nil, root, "fixture/source.txt", "input:fixture-request-sha256:"+strings.Repeat("b", 64), "source:fixture-path-sha256:"+strings.Repeat("a", 64), digest)
	if err != nil {
		t.Fatal(err)
	}
	if recovered.SourceDigest != digest || recovered.Text != "Synthetic source bytes\n" {
		t.Fatalf("unexpected recovery: %#v", recovered)
	}
	entries, err := filepath.Glob(filepath.Join(root, ".leanctx-sdk-*.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("request files leaked: %v", entries)
	}
}

func TestSubprocessInstanceClientRejectsJailEscapeAndTypedProcessFailure(t *testing.T) {
	root := t.TempDir()
	responsePath := writeFixtureResponse(t, "response")
	recoveryPath := writeFixtureResponse(t, "recovery_response")
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: staticInstanceScript(t, responsePath, recoveryPath), Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Recover(root, "../escape", "input:x", "source:x", "sha256:"+strings.Repeat("a", 64)); err == nil {
		t.Fatal("path escape unexpectedly reached instance")
	} else {
		var protocol *InstanceProtocolError
		if !errors.As(err, &protocol) {
			t.Fatalf("path escape error type = %T", err)
		}
	}
	failing := writeTestExecutable(t, "#!/bin/sh\n/bin/echo engine:source_unavailable >&2\nexit 1\n")
	client.InstanceBinary = failing
	if _, err := client.ContextView(testPlanForRoot(t, root)); err == nil {
		t.Fatal("failed instance unexpectedly succeeded")
	} else {
		var unavailable *SourceUnavailableError
		if !errors.As(err, &unavailable) {
			t.Fatalf("typed process failure = %T: %v", err, err)
		}
	}
}

func testPlanForRoot(t *testing.T, root string) *ContextPlan {
	t.Helper()
	plan, err := NewContextPlan("s", "t", "inspect", *testSource(t, root))
	if err != nil {
		t.Fatal(err)
	}
	return plan
}

func TestSubprocessInstanceClientTimesOutAndPreservesCompatibilityErrors(t *testing.T) {
	root := t.TempDir()
	slow := writeTestExecutable(t, "#!/bin/sh\n/bin/sleep 5\n")
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: slow, Timeout: 100 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.ContextViewContext(context.Background(), testPlanForRoot(t, root)); err == nil {
		t.Fatal("slow instance unexpectedly succeeded")
	} else {
		var timeout *InstanceTimeout
		if !errors.As(err, &timeout) {
			t.Fatalf("timeout error = %T: %v", err, err)
		}
	}
	badResponse := writeTestExecutable(t, "#!/bin/sh\n/bin/echo '{\"schema_version\":1,\"transport_version\":2,\"engine_interface_version\":\"1.0.0\",\"view\":{\"text\":\"x\",\"output_ref\":null,\"output_digest\":null},\"invocation\":null,\"observation\":null,\"recovery\":{\"recovery_ref\":\"input:x\",\"source_ref\":\"source:x\",\"source_digest\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}}'\n")
	client.InstanceBinary = badResponse
	client.Timeout = time.Second
	if _, err := client.ContextView(testPlanForRoot(t, root)); err == nil {
		t.Fatal("incompatible response unexpectedly succeeded")
	} else {
		var compatibility *CompatibilityError
		if !errors.As(err, &compatibility) {
			t.Fatalf("compatibility error = %T: %v", err, err)
		}
	}
}

func TestOptionalRealInstanceV1RoundTrip(t *testing.T) {
	binary := instanceEnv("LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN")
	if binary == "" {
		t.Skip("LEANCTX_INSTANCE_BIN is not set")
	}
	root := testRepositoryRoot(t)
	source, err := NewContextSource("README.md", ContextSourceOptions{ProjectRoot: root})
	if err != nil {
		t.Fatal(err)
	}
	plan, err := NewContextPlan("go-real-engine", "go-real-task", "inspect", *source)
	if err != nil {
		t.Fatal(err)
	}
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: binary, Timeout: 30 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	view, err := client.ContextView(plan)
	if err != nil {
		t.Fatal(err)
	}
	if !view.Verify() {
		t.Fatal("real instance returned unverified evidence")
	}
	// B16: a real Engine on POSIX starts the one-shot behind its session
	// starter and says so on the first line (this file is not built on
	// Windows).
	if got := client.LastSessionContainment(); got != SessionContainmentInstanceStarter {
		t.Fatalf("session containment = %q, want %q", got, SessionContainmentInstanceStarter)
	}
}
