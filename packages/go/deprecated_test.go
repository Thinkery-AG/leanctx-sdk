package leanctx

import (
	"errors"
	"fmt"
	"os"
	"sync"
	"testing"
)

var deprecatedEnvWarned sync.Map

// instanceEnv reads the LeanCTX instance variable first. When only the
// deprecated Engine variable is set it is used, with a one-time warning.
func instanceEnv(name, deprecated string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	value := os.Getenv(deprecated)
	if value != "" {
		if _, seen := deprecatedEnvWarned.LoadOrStore(deprecated, true); !seen {
			fmt.Fprintf(os.Stderr, "leanctx: %s is deprecated and is removed in SDK 2.0; use %s\n", deprecated, name)
		}
	}
	return value
}

func TestDeprecatedEngineNamesStillWork(t *testing.T) {
	var err error = NewInstanceUnavailable("gone")
	var oldUnavailable *EngineUnavailable
	if !errors.As(err, &oldUnavailable) {
		t.Fatalf("errors.As with EngineUnavailable failed for %T", err)
	}
	err = NewEngineTimeout("late")
	var newTimeout *InstanceTimeout
	if !errors.As(err, &newTimeout) {
		t.Fatalf("old constructor returned %T", err)
	}
	switch err.(type) {
	case *EngineTimeout:
	default:
		t.Fatalf("type switch on EngineTimeout missed %T", err)
	}
	var unsupported *UnsupportedEngineError = NewUnsupportedEngineError("nope")
	if unsupported.Error() == "" {
		t.Fatal("empty error message")
	}
	if EngineStatusSucceeded != InstanceStatusSucceeded || SessionContainmentEngineStarter != SessionContainmentInstanceStarter {
		t.Fatal("deprecated constants differ")
	}

	client, err := NewSubprocessEngineClient(SubprocessEngineClientOptions{EngineBinary: "old-binary"})
	if err != nil || client.InstanceBinary != "old-binary" {
		t.Fatalf("deprecated EngineBinary option ignored: %#v, %v", client, err)
	}
	client, err = NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: "new", EngineBinary: "old"})
	if err != nil || client.InstanceBinary != "new" {
		t.Fatalf("InstanceBinary must win: %#v, %v", client, err)
	}
	legacy := &SubprocessEngineClient{EngineBinary: "legacy"}
	if err := legacy.configured(); err != nil || legacy.InstanceBinary != "legacy" {
		t.Fatalf("struct literal with EngineBinary ignored: %#v, %v", legacy, err)
	}

	var engine EngineClient = &fakeInstance{}
	session, err := NewContextSession("task", SessionOptions{SessionID: "s", TaskID: "t", Engine: engine})
	if err != nil || session.engine != engine {
		t.Fatalf("deprecated SessionOptions.Engine ignored: %v", err)
	}
}

func TestInstanceEnvPrefersNewNameAndFallsBack(t *testing.T) {
	t.Setenv("LEANCTX_INSTANCE_BIN", "")
	t.Setenv("LEANCTX_ENGINE_BIN", "/old/lean-ctx")
	if got := instanceEnv("LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN"); got != "/old/lean-ctx" {
		t.Fatalf("fallback = %q", got)
	}
	t.Setenv("LEANCTX_INSTANCE_BIN", "/new/lean-ctx")
	if got := instanceEnv("LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN"); got != "/new/lean-ctx" {
		t.Fatalf("new name = %q", got)
	}
}
