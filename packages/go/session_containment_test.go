//go:build !windows

package leanctx

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

// Session containment (Q13): what the SDK asks of the Engine binary and what
// it reports. The stand-in Instances are shell scripts; nothing here starts a
// LeanCTX | OSS.

const containmentPayload = "{\"payload\":\"é\",\"n\":1}\n"

// oneShotStandIn writes a stand-in that prints, optionally, the session
// starter's acknowledgement, then a fixed payload, and exits with code.
func oneShotStandIn(t *testing.T, ack bool, code int) (*SubprocessInstanceClient, string) {
	t.Helper()
	root := t.TempDir()
	script := "#!/bin/sh\n"
	if ack {
		script += "printf '%s\\n' '{\"leanctx_session_containment\":1}'\n"
	}
	script += "printf '%s' '" + containmentPayload[:len(containmentPayload)-1] + "'\necho\n"
	if code != 0 {
		script += "echo 'engine: nonzero_exit' >&2\n"
	}
	script += "exit " + strconv.Itoa(code) + "\n"
	path := filepath.Join(root, "one-shot-engine")
	if err := os.WriteFile(path, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: path, Timeout: 10 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	return client, root
}

func runOneShot(client *SubprocessInstanceClient, root string) ([]byte, error) {
	return client.run(context.Background(), "context-view", root, filepath.Join(root, "request.json"))
}

func TestSessionContainmentOneShotWithoutAcknowledgementLeavesTheBytesUnchanged(t *testing.T) {
	client, root := oneShotStandIn(t, false, 0)
	if got := client.LastSessionContainment(); got != SessionContainmentUnavailable {
		t.Fatalf("initial state = %q", got)
	}
	output, err := runOneShot(client, root)
	if err != nil {
		t.Fatal(err)
	}
	if string(output) != containmentPayload {
		t.Fatalf("output = %q", output)
	}
	if got := client.LastSessionContainment(); got != SessionContainmentUnavailable {
		t.Fatalf("state = %q", got)
	}
}

func TestSessionContainmentOneShotAcknowledgementIsStrippedAndReported(t *testing.T) {
	client, root := oneShotStandIn(t, true, 0)
	output, err := runOneShot(client, root)
	if err != nil {
		t.Fatal(err)
	}
	if string(output) != containmentPayload {
		t.Fatalf("output = %q, want the payload without the acknowledgement", output)
	}
	if got := client.LastSessionContainment(); got != SessionContainmentInstanceStarter {
		t.Fatalf("state = %q", got)
	}
}

func TestSessionContainmentOneShotFailedRunStillRecordsTheAcknowledgement(t *testing.T) {
	client, root := oneShotStandIn(t, true, 3)
	if _, err := runOneShot(client, root); err == nil {
		t.Fatal("a non-zero exit must be an error")
	}
	if got := client.LastSessionContainment(); got != SessionContainmentInstanceStarter {
		t.Fatalf("state = %q", got)
	}
}

// Agent client end to end. Stand-in convention: fakeAgentScript in
// agent_test.go (a /bin/sh Engine). The acknowledging stand-in prints the
// session starter's line and then becomes that same script.
func openContainmentAgent(t *testing.T, ack bool) *AgentContext {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "source.txt"), []byte("source\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	binary := fakeAgentScript(t, "ok")
	if ack {
		binary = writeTestExecutable(t, "#!/bin/sh\n"+
			"/usr/bin/printf '%s\\n' '{\"leanctx_session_containment\":1}'\n"+
			"exec "+shellQuote(binary)+" \"$@\"\n")
	}
	client, err := OpenAgentContext(context.Background(), root, agentOptions(binary, AgentPermissions{}))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Close() })
	return client
}

func requireSameAgentResult(t *testing.T, client *AgentContext) {
	t.Helper()
	result, err := client.Read("source.txt", ReadOptions{Mode: ReadModeFull})
	if err != nil {
		t.Fatal(err)
	}
	// The stand-in's fixed result, byte for byte, whether or not an
	// acknowledgement preceded the session.
	if result.Tool != "ctx_read" || result.Text != "ok" || result.SavedTokens != 75 {
		t.Fatalf("result = %#v", result)
	}
}

func TestSessionContainmentAgentWithoutAcknowledgementIsUnavailable(t *testing.T) {
	client := openContainmentAgent(t, false)
	if got := client.SessionContainment(); got != SessionContainmentUnavailable {
		t.Fatalf("state = %q", got)
	}
	requireSameAgentResult(t, client)
}

func TestSessionContainmentAgentAcknowledgementIsReportedAndIsNotAResponse(t *testing.T) {
	client := openContainmentAgent(t, true)
	if got := client.SessionContainment(); got != SessionContainmentInstanceStarter {
		t.Fatalf("state = %q", got)
	}
	requireSameAgentResult(t, client)
}

// Real Engine, same convention as TestOptionalRealInstanceV1RoundTrip
// (engine_test.go): skipped unless LEANCTX_INSTANCE_BIN is set.
func TestOptionalRealInstanceSessionStarterAcknowledges(t *testing.T) {
	engine := instanceEnv("LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN")
	if engine == "" {
		t.Skip("LEANCTX_INSTANCE_BIN is not set")
	}
	root := t.TempDir()
	// The stand-in is started through the real binary's session starter.
	binary := writeTestExecutable(t, "#!/bin/sh\n"+
		"exec "+shellQuote(engine)+" __session-starter --ack --watch-parent -- "+
		shellQuote(fakeAgentScript(t, "ok"))+" \"$@\"\n")
	options := agentOptions(binary, AgentPermissions{})
	options.Timeout = 10 * time.Second
	client, err := OpenAgentContext(context.Background(), root, options)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if got := client.SessionContainment(); got != SessionContainmentInstanceStarter {
		t.Fatalf("state = %q", got)
	}
}

// Only the very first line of an Engine start can be the acknowledgement.
func TestSessionContainmentOnlyTheFirstLineCanAcknowledge(t *testing.T) {
	var state containmentState
	if state.value() != SessionContainmentUnavailable {
		t.Fatal("the initial state must be unavailable")
	}
	if state.firstLine([]byte("{\"id\":\"1\",\"ok\":true,\"result\":{}}\n")) {
		t.Fatal("a response is not an acknowledgement")
	}
	if state.firstLine(sessionContainmentAck) {
		t.Fatal("a later line must not be taken for the acknowledgement")
	}
	if state.value() != SessionContainmentUnavailable {
		t.Fatal("a later acknowledgement line must not change the state")
	}
	state.reset()
	if !state.firstLine(sessionContainmentAck) {
		t.Fatal("the first line is the acknowledgement")
	}
	if state.firstLine(sessionContainmentAck) {
		t.Fatal("it is answered once")
	}
	if state.value() != SessionContainmentInstanceStarter {
		t.Fatal("the state must be engine-starter")
	}
}
