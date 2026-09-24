package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	leanctx "github.com/Thinkery-AG/leanctx-sdk/packages/go"
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}
func write(path, data string) { must(os.WriteFile(path, []byte(data), 0600)) }
func digest(path string) string {
	data, err := os.ReadFile(path)
	must(err)
	value := sha256.Sum256(data)
	return hex.EncodeToString(value[:])
}

func main() {
	if len(os.Args) != 4 {
		panic("engine, previous engine, output required")
	}
	engine, previous, output := os.Args[1], os.Args[2], os.Args[3]
	root, err := os.MkdirTemp("", "leanctx-pro-go-reference-")
	must(err)
	defer os.RemoveAll(root)
	must(os.Mkdir(filepath.Join(root, ".git"), 0700))
	must(os.Mkdir(filepath.Join(root, ".lean-ctx"), 0700))
	write(filepath.Join(root, "login.py"), "# authentication retry: refresh the expired session before retrying\ndef authenticate():\n    return \"REFRESH_SESSION_FIRST CUS-1234\"\n")
	write(filepath.Join(root, "private.py"), "# CONFIDENTIAL\ndef authentication_secret():\n    return \"PRIVATE_CANARY\"\n")
	rules := "name=\"installed-reference\"\nversion=\"1.0.0\"\ndescription=\"test\"\n[filters]\nclassification=\"block\"\n[redaction]\ncustomer=\"CUS-[0-9]{4}\"\n"
	policy := filepath.Join(root, ".lean-ctx/policy.toml")
	write(policy, rules)
	checks := map[string]bool{}
	responses := map[string]string{}
	old, err := leanctx.OpenAgentContext(context.Background(), root, leanctx.AgentContextOptions{EngineBinary: previous})
	if err == nil {
		must(old.Close())
		panic("old Engine unexpectedly admitted")
	}
	var protocol *leanctx.EngineProtocolError
	checks["old_engine_rejected"] = errors.As(err, &protocol) && strings.Contains(err.Error(), "hello is incompatible")
	agent, err := leanctx.OpenAgentContext(context.Background(), root, leanctx.AgentContextOptions{EngineBinary: engine})
	must(err)
	defer agent.Close()
	read, err := agent.Read("login.py", "full")
	must(err)
	composed, err := agent.Compose("investigate authentication retry")
	must(err)
	responses["read"], responses["compose"] = read.Text, composed.Text
	checks["useful_masked_read"] = strings.Contains(read.Text, "REFRESH_SESSION_FIRST") && strings.Contains(read.Text, "REDACTED") && !strings.Contains(read.Text, "CUS-1234")
	checks["useful_protected_compose"] = strings.Contains(composed.Text, "REFRESH_SESSION_FIRST") && strings.Contains(composed.Text, "login.py") && !strings.Contains(composed.Text, "CUS-1234") && !strings.Contains(composed.Text, "PRIVATE_CANARY") && !strings.Contains(composed.Text, "private.py")
	write(policy, rules+"[context]\ndeny_tools=[\"ctx_read\"]\n")
	denied, err := agent.Read("login.py", "full")
	if err != nil {
		var permission *leanctx.AgentPermissionError
		var execution *leanctx.EngineExecutionError
		responses["denied"] = err.Error()
		checks["changed_rule_blocks_read"] = (errors.As(err, &permission) || errors.As(err, &execution)) && strings.Contains(strings.ToLower(err.Error()), "policy")
	} else {
		responses["denied"] = denied.Text
		checks["changed_rule_blocks_read"] = strings.Contains(denied.Text, "POLICY BLOCKED")
	}
	checks["changed_rule_blocks_read"] = checks["changed_rule_blocks_read"] && !strings.Contains(responses["denied"], "REFRESH_SESSION_FIRST") && !strings.Contains(responses["denied"], "CUS-1234")
	write(policy, rules)
	restored, err := agent.Read("login.py", "full")
	must(err)
	responses["restored"] = restored.Text
	checks["same_session_rule_repair"] = strings.Contains(restored.Text, "REFRESH_SESSION_FIRST") && !strings.Contains(restored.Text, "CUS-1234")
	passed := len(checks) == 5
	for _, ok := range checks {
		passed = passed && ok
	}
	result := map[string]any{"passed": passed, "checks": checks, "responses": responses, "engine_sha256": digest(engine), "previous_engine_sha256": digest(previous), "scope": "Installed local Go module archive; actual local Engine output to application, no model/Codex/GitLab qualification."}
	data, err := json.MarshalIndent(result, "", "  ")
	must(err)
	must(os.WriteFile(output, append(data, '\n'), 0600))
	fmt.Println(string(data))
	if !passed {
		panic("reference failed")
	}
}
