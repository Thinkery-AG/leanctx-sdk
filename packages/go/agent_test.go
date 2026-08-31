//go:build !windows

package leanctx

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func fakeAgentScript(t *testing.T, behavior string) string {
	t.Helper()
	readOnly := `["ctx_compose","ctx_glob","ctx_read","ctx_search","ctx_symbol","ctx_tree"]`
	withWrite := `["ctx_compose","ctx_edit","ctx_fill","ctx_glob","ctx_patch","ctx_read","ctx_search","ctx_symbol","ctx_tree"]`
	full := `["ctx_compose","ctx_edit","ctx_fill","ctx_glob","ctx_patch","ctx_read","ctx_search","ctx_shell","ctx_symbol","ctx_tree"]`
	result := `{"changed":false,"content_blocks":[],"mode":"auto","original_tokens":100,"output_tokens":25,"saved_tokens":75,"shell":null,"text":"ok"}`
	hello := `{"agent_tools_interface_version":"1.0.0","allow_exec":false,"allow_write":false,"capabilities":CAPS,"engine_version":"3.10.1","schema_version":1,"transport_version":1}`
	hello = strings.Replace(hello, "CAPS", readOnly, 1)
	if behavior == "badhello" {
		hello = strings.Replace(hello, `"allow_write":false`, `"allow_write":true`, 1)
	}
	return writeTestExecutable(t, "#!/bin/sh\n"+
		"policy=\"$6\"\n"+
		"hello='"+hello+"'\n"+
		"result='"+result+"'\n"+
		"if /usr/bin/grep -q '\"allow_write\":true' \"$policy\"; then hello='"+strings.Replace(hello, readOnly, withWrite, 1)+"'; fi\n"+"if /usr/bin/grep -q '\"allow_exec\":true' \"$policy\"; then hello='"+strings.Replace(strings.Replace(hello, readOnly, full, 1), `"allow_exec":false`, `"allow_exec":true`, 1)+"'; fi\n"+"while IFS= read -r line; do\n"+"  id=$(/usr/bin/printf '%s' \"$line\" | /usr/bin/sed -n 's/.*\"id\":\"\\([^\"]*\\)\".*/\\1/p')\n"+"  if /usr/bin/printf '%s' \"$line\" | /usr/bin/grep -q '\"op\":\"hello\"'; then\n"+"    /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":%s}\\n' \"$id\" \"$hello\"\n"+"  elif /usr/bin/printf '%s' \"$line\" | /usr/bin/grep -q '\"op\":\"close\"'; then\n"+"    /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":{}}\\n' \"$id\"\n"+"    exit 0\n"+"  elif [ \""+behavior+"\" = sleep ]; then\n"+"    /bin/sleep 5\n"+"    /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":%s}\\n' \"$id\" \"$result\"\n"+"  elif [ \""+behavior+"\" = protocol ]; then\n"+"    /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":{\"unexpected\":true}}\\n' \"$id\"\n"+"  else\n"+"    /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":%s}\\n' \"$id\" \"$result\"\n"+"  fi\n"+"done\n")
}

func fakeAgentFullScript(t *testing.T) string {
	t.Helper()
	return writeTestExecutable(t, `#!/bin/sh
while IFS= read -r line; do
  id=$(/usr/bin/printf '%s' "$line" | /usr/bin/sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
  if /usr/bin/printf '%s' "$line" | /usr/bin/grep -q '"op":"hello"'; then
    /usr/bin/printf '%s\n' '{"id":"'$id'","ok":true,"result":{"agent_tools_interface_version":"1.0.0","allow_exec":true,"allow_write":true,"capabilities":["ctx_compose","ctx_edit","ctx_fill","ctx_glob","ctx_patch","ctx_read","ctx_search","ctx_shell","ctx_symbol","ctx_tree"],"engine_version":"3.10.1","schema_version":1,"transport_version":1}}'
  elif /usr/bin/printf '%s' "$line" | /usr/bin/grep -q '"op":"close"'; then
    /usr/bin/printf '%s\n' '{"id":"'$id'","ok":true,"result":{}}'
    exit 0
  else
    /usr/bin/printf '%s\n' '{"id":"'$id'","ok":true,"result":{"changed":false,"content_blocks":[],"mode":"auto","original_tokens":100,"output_tokens":25,"saved_tokens":75,"shell":null,"text":"ok"}}'
  fi
done
`)
}

func agentOptions(binary string, permissions AgentPermissions) AgentContextOptions {
	return AgentContextOptions{
		Task:         "inspect",
		Permissions:  permissions,
		EngineBinary: binary,
		Timeout:      2 * time.Second,
	}
}

func TestAgentContextNegotiatesImmutableReadPolicyAndMetrics(t *testing.T) {
	root := t.TempDir()
	binary := fakeAgentScript(t, "ok")
	executables := []string{"echo"}
	environment := []string{"FOO"}
	options := agentOptions(binary, AgentPermissions{})
	options.ExecutionPolicy = ExecutionPolicy{MaxTimeout: time.Second, AllowedExecutables: executables, AllowedEnv: environment}
	client, err := OpenAgentContext(context.Background(), root, options)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	executables[0], environment[0] = "changed", "BAR"
	if got := client.Capabilities(); len(got) != len(agentReadTools) || !equalStrings(got, agentReadTools) {
		t.Fatalf("capabilities = %#v", got)
	}
	result, err := client.Read("fixture/source.txt", ReadOptions{Mode: ReadModeFull, Fresh: true})
	if err != nil {
		t.Fatal(err)
	}
	if result.Tool != "ctx_read" || result.Text != "ok" || result.SavedTokens != 75 || result.SavedRatio() != 0.75 {
		t.Fatalf("unexpected read result: %#v", result)
	}
	metrics := client.Metrics()
	if metrics.ToolCalls != 1 || metrics.OriginalTokens != 100 || metrics.OutputTokens != 25 || metrics.SavedTokens != 75 || metrics.SavedRatio() != 0.75 {
		t.Fatalf("unexpected metrics: %#v", metrics)
	}
	if _, err := client.Run([]string{"echo", "hello"}); err == nil {
		t.Fatal("execute permission unexpectedly granted")
	} else {
		var permission *AgentPermissionError
		if !errors.As(err, &permission) {
			t.Fatalf("permission error = %T", err)
		}
	}
	if _, err := client.Call("ctx_shell"); err == nil {
		t.Fatal("generic call unexpectedly accepted execution tool")
	}
}

func TestAgentContextWriteAndStructuredExecuteAreExplicit(t *testing.T) {
	root := t.TempDir()
	options := agentOptions(fakeAgentFullScript(t), AgentPermissions{Write: true, Execute: true})
	options.ExecutionPolicy = ExecutionPolicy{MaxTimeout: time.Second, AllowedExecutables: []string{"echo"}, AllowedEnv: []string{"FOO"}}
	client, err := OpenAgentContext(context.Background(), root, options)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if got := client.Capabilities(); !equalStrings(got, []string{"ctx_compose", "ctx_edit", "ctx_fill", "ctx_glob", "ctx_patch", "ctx_read", "ctx_search", "ctx_shell", "ctx_symbol", "ctx_tree"}) {
		t.Fatalf("full capabilities = %#v", got)
	}
	if result, err := client.CreateFile("new.txt", "text"); err != nil || result.Tool != "ctx_patch" {
		t.Fatalf("create file = %#v, %v", result, err)
	}
	if result, err := client.Run([]string{"echo", "hello"}, RunOptions{CWD: ".", Env: map[string]string{"FOO": "bar"}, Timeout: 100 * time.Millisecond}); err != nil || result.Tool != "ctx_shell" {
		t.Fatalf("structured run = %#v, %v", result, err)
	}
	if _, err := client.Run([]string{"printf"}); err == nil {
		t.Fatal("unallowed executable unexpectedly accepted")
	}
	if _, err := client.Run([]string{"echo"}, RunOptions{CWD: "../escape"}); err == nil {
		t.Fatal("cwd escape unexpectedly accepted")
	}
	if _, err := client.Run([]string{"echo"}, RunOptions{Env: map[string]string{"PATH": "/tmp"}}); err == nil {
		t.Fatal("forbidden environment unexpectedly accepted")
	}
}

func TestAgentContextStartupIsTransactionalAndProtocolErrorsTerminal(t *testing.T) {
	root := t.TempDir()
	bad, err := OpenAgentContext(context.Background(), root, agentOptions(fakeAgentScript(t, "badhello"), AgentPermissions{}))
	if err == nil || bad != nil {
		t.Fatalf("bad hello = %#v, %v", bad, err)
	}
	if entries, _ := filepath.Glob(filepath.Join(root, ".leanctx-agent-*")); len(entries) != 0 {
		t.Fatalf("startup policy artifacts leaked: %v", entries)
	}
	client, err := OpenAgentContext(context.Background(), root, agentOptions(fakeAgentScript(t, "protocol"), AgentPermissions{}))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Read("file"); err == nil {
		t.Fatal("malformed result unexpectedly accepted")
	} else {
		var protocol *EngineProtocolError
		if !errors.As(err, &protocol) {
			t.Fatalf("protocol error = %T: %v", err, err)
		}
	}
	if _, err := client.Read("file"); err == nil {
		t.Fatal("terminal AgentContext unexpectedly accepted another call")
	}
	if err := client.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestAgentContextCancellationReconnectAndAsyncFacade(t *testing.T) {
	root := t.TempDir()
	options := agentOptions(fakeAgentScript(t, "sleep"), AgentPermissions{})
	client, err := OpenAgentContext(context.Background(), root, options)
	if err != nil {
		t.Fatal(err)
	}
	deadline, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if _, err := client.ReadContext(deadline, "file"); err == nil {
		t.Fatal("cancelled Agent Tools call unexpectedly succeeded")
	} else {
		var timeout *EngineTimeout
		if !errors.As(err, &timeout) && !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("cancel error = %T: %v", err, err)
		}
	}
	if _, err := client.Reconnect(context.Background()); err != nil {
		t.Fatal(err)
	}
	_ = client.Close()

	asyncOptions := agentOptions(fakeAgentScript(t, "ok"), AgentPermissions{})
	async, err := NewAsyncAgentContext(root, asyncOptions)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := async.Read(context.Background(), "file"); err == nil {
		t.Fatal("async read before open unexpectedly succeeded")
	}
	if err := async.Open(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := async.Read(context.Background(), "file"); err != nil {
		t.Fatal(err)
	}
	if metrics, err := async.Metrics(); err != nil || metrics.ToolCalls != 1 {
		t.Fatalf("async metrics = %#v, %v", metrics, err)
	}
	if err := async.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestExecutionPolicyValidationAndToolResultMetrics(t *testing.T) {
	if _, err := normalizeExecutionPolicy(ExecutionPolicy{MaxTimeout: 99 * time.Millisecond}); err == nil {
		t.Fatal("short policy unexpectedly accepted")
	}
	if _, err := normalizeExecutionPolicy(ExecutionPolicy{AllowedExecutables: []string{"/bin/sh"}}); err == nil {
		t.Fatal("path executable unexpectedly accepted")
	}
	if _, err := normalizeExecutionPolicy(ExecutionPolicy{AllowedEnv: []string{"PATH"}}); err == nil {
		t.Fatal("dangerous environment unexpectedly accepted")
	}
	result, err := parseToolResult("ctx_read", map[string]any{
		"text": "ok", "content_blocks": []any{}, "original_tokens": int64(2), "output_tokens": int64(1), "saved_tokens": int64(1), "mode": nil, "changed": false, "shell": nil,
	})
	if err != nil || result.SavedRatio() != 0.5 {
		t.Fatalf("tool result = %#v, %v", result, err)
	}
	if _, err := parseToolResult("ctx_read", map[string]any{
		"text": "ok", "content_blocks": []any{}, "original_tokens": int64(2), "output_tokens": int64(2), "saved_tokens": int64(1), "mode": nil, "changed": false, "shell": nil,
	}); err == nil {
		t.Fatal("inconsistent metrics unexpectedly accepted")
	}
}
