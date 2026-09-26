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
	args := os.Args[1:]
	positional := make([]string, 0, 3)
	projectRoot := ""
	for i := 0; i < len(args); i++ {
		if args[i] == "--project" {
			if i+1 >= len(args) || args[i+1] == "" {
				panic("--project requires a path")
			}
			projectRoot = args[i+1]
			i++
		} else {
			positional = append(positional, args[i])
		}
	}
	if len(positional) != 3 {
		panic("engine, previous engine, output required; optional --project PATH")
	}
	engine, previous, output := positional[0], positional[1], positional[2]
	root := projectRoot
	var err error
	if projectRoot == "" {
		root, err = os.MkdirTemp("", "leanctx-pro-go-reference-")
		must(err)
		defer os.RemoveAll(root)
		must(os.Mkdir(filepath.Join(root, ".git"), 0700))
		must(os.Mkdir(filepath.Join(root, ".lean-ctx"), 0700))
		write(filepath.Join(root, "login.py"), "# authentication retry: refresh the expired session before retrying\ndef authenticate():\n    return \"REFRESH_SESSION_FIRST CUS-1234\"\n")
		write(filepath.Join(root, "private.py"), "# CONFIDENTIAL\ndef authentication_secret():\n    return \"PRIVATE_CANARY\"\n")
		rules := "name=\"installed-reference\"\nversion=\"1.0.0\"\ndescription=\"test\"\n[filters]\nclassification=\"block\"\n[redaction]\ncustomer=\"CUS-[0-9]{4}\"\n"
		write(filepath.Join(root, ".lean-ctx/policy.toml"), rules)
	}
	policy := filepath.Join(root, ".lean-ctx/policy.toml")
	initialPolicy, err := os.ReadFile(policy)
	must(err)
	rules := string(initialPolicy)
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
	if os.Getenv("LEANCTX_REFERENCE_PRO") == "1" {
		checks["pro_context_selection"] = strings.Contains(composed.Text, "Pro context selection:") && !strings.Contains(composed.Text, "Pro context selection unavailable")
	}
	responses["read"], responses["compose"] = read.Text, composed.Text
	checks["useful_masked_read"] = strings.Contains(read.Text, "REFRESH_SESSION_FIRST") && strings.Contains(read.Text, "REDACTED") && !strings.Contains(read.Text, "CUS-1234")
	checks["useful_protected_compose"] = strings.Contains(composed.Text, "REFRESH_SESSION_FIRST") && strings.Contains(composed.Text, "login.py") && !strings.Contains(composed.Text, "CUS-1234") && !strings.Contains(composed.Text, "PRIVATE_CANARY") && !strings.Contains(composed.Text, "private.py")
	temporaryPolicy := rules
	if !strings.HasSuffix(temporaryPolicy, "\n") {
		temporaryPolicy += "\n"
	}
	deniedContext := "[context]\ndeny_tools=[\"ctx_read\"]\n"
	if strings.Contains(temporaryPolicy, "[context]") {
		write(policy, strings.Replace(temporaryPolicy, "[context]", deniedContext, 1))
	} else {
		write(policy, temporaryPolicy+deniedContext)
	}
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
	passed := len(checks) >= 5
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
