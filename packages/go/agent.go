package leanctx

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	maxAgentRequestBytes  = 1024 * 1024
	maxAgentResponseBytes = 16 * 1024 * 1024
)

var forbiddenEnvironment = map[string]bool{
	"COMSPEC": true, "DYLD_INSERT_LIBRARIES": true, "HOME": true,
	"LD_PRELOAD": true, "PATH": true, "PATHEXT": true,
	"PYTHONPATH": true, "RUSTC_WRAPPER": true, "SHELL": true,
}

var agentReadTools = []string{"ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"}
var agentWriteTools = []string{"ctx_edit", "ctx_fill", "ctx_patch"}
var agentExecuteTools = []string{"ctx_shell"}

// ReadMode selects the context read projection.
type ReadMode string

const (
	ReadModeAuto       ReadMode = "auto"
	ReadModeFull       ReadMode = "full"
	ReadModeRaw        ReadMode = "raw"
	ReadModeSignatures ReadMode = "signatures"
	ReadModeMap        ReadMode = "map"
	ReadModeDiff       ReadMode = "diff"
	ReadModeReference  ReadMode = "reference"
	ReadModeTask       ReadMode = "task"
	ReadModeAnchored   ReadMode = "anchored"
)

// AgentPermissions is the immutable capability request made at startup.
type AgentPermissions struct {
	Write   bool
	Execute bool
}

// ExecutionPolicy is copied and normalized into every AgentContext. Changes to
// the caller's slices after startup cannot change the active policy.
type ExecutionPolicy struct {
	MaxTimeout         time.Duration
	AllowedExecutables []string
	AllowedEnv         []string
}

func defaultExecutionPolicy() ExecutionPolicy {
	return ExecutionPolicy{MaxTimeout: 30 * time.Second}
}

func normalizeExecutionPolicy(policy ExecutionPolicy) (ExecutionPolicy, error) {
	if policy.MaxTimeout == 0 {
		policy.MaxTimeout = 30 * time.Second
	}
	if policy.MaxTimeout < 100*time.Millisecond || policy.MaxTimeout > 120*time.Second {
		return ExecutionPolicy{}, NewValidationError("max_timeout must be between 0.1 and 120 seconds")
	}
	executables := append([]string(nil), policy.AllowedExecutables...)
	for _, executable := range executables {
		if !validExecutableName(executable) {
			return ExecutionPolicy{}, NewValidationError("allowed_executables must contain executable basenames")
		}
	}
	environment := append([]string(nil), policy.AllowedEnv...)
	for _, name := range environment {
		if !validEnvironmentName(name) {
			return ExecutionPolicy{}, NewValidationError("allowed_env must contain environment variable names")
		}
	}
	sort.Strings(executables)
	sort.Strings(environment)
	return ExecutionPolicy{MaxTimeout: policy.MaxTimeout, AllowedExecutables: uniqueStrings(executables), AllowedEnv: uniqueStrings(environment)}, nil
}

func validExecutableName(value string) bool {
	if value == "" || strings.ContainsAny(value, `/\\`) {
		return false
	}
	for _, r := range value {
		if !(r >= 'A' && r <= 'Z') && !(r >= 'a' && r <= 'z') && !(r >= '0' && r <= '9') && !strings.ContainsRune("._+-", r) {
			return false
		}
	}
	return true
}

func validEnvironmentName(value string) bool {
	if value == "" || forbiddenEnvironment[strings.ToUpper(value)] {
		return false
	}
	for i, r := range value {
		if i == 0 {
			if !(r == '_' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z') {
				return false
			}
			continue
		}
		if !(r == '_' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9') {
			return false
		}
	}
	return true
}

func uniqueStrings(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	result := values[:0]
	for _, value := range values {
		if len(result) == 0 || result[len(result)-1] != value {
			result = append(result, value)
		}
	}
	return result
}

// ToolResult is one typed Agent Tools response.
type ToolResult struct {
	Tool           string
	Text           string
	ContentBlocks  []map[string]any
	OriginalTokens int64
	OutputTokens   int64
	SavedTokens    int64
	Mode           *string
	Changed        bool
	Shell          map[string]any
}

func (r ToolResult) SavedRatio() float64 {
	if r.OriginalTokens == 0 {
		return 0
	}
	denominator := r.OriginalTokens
	saved := r.SavedTokens
	if saved > denominator {
		saved = denominator
	}
	return float64(saved) / float64(denominator)
}

// AgentMetrics is a snapshot of successful tool-result accounting.
type AgentMetrics struct {
	ToolCalls      int64
	OriginalTokens int64
	OutputTokens   int64
	SavedTokens    int64
}

func (m AgentMetrics) SavedRatio() float64 {
	if m.OriginalTokens == 0 {
		return 0
	}
	saved := m.SavedTokens
	if saved > m.OriginalTokens {
		saved = m.OriginalTokens
	}
	return float64(saved) / float64(m.OriginalTokens)
}

// AgentContextOptions configures one persistent Agent Tools child process.
type AgentContextOptions struct {
	Task            string
	Permissions     AgentPermissions
	ExecutionPolicy ExecutionPolicy
	EngineBinary    string
	Timeout         time.Duration
}

// ReadOptions configures Read.
type ReadOptions struct {
	Mode  ReadMode
	Fresh bool
}

// SearchOptions configures Search.
type SearchOptions struct {
	Path       string
	MaxResults int
	Include    string
}

// GlobOptions configures Glob.
type GlobOptions struct {
	Path       string
	MaxResults int
}

// TreeOptions configures Tree.
type TreeOptions struct {
	Path       string
	Depth      int
	ShowHidden bool
}

// RunOptions is a structured argv execution request.
type RunOptions struct {
	CWD     string
	Env     map[string]string
	Timeout time.Duration
}

// AgentContext is a project-jailed persistent Agent Tools session.
type AgentContext struct {
	ProjectRoot     string
	Task            string
	Permissions     AgentPermissions
	ExecutionPolicy ExecutionPolicy
	EngineBinary    string
	Timeout         time.Duration

	mu             sync.Mutex
	command        *exec.Cmd
	stdin          *bufio.Writer
	stdout         *bufio.Reader
	stderrDone     chan struct{}
	stderr         bytes.Buffer
	stderrOverflow bool
	closed         bool
	helloAccepted  bool
	nextID         uint64
	capabilities   []string
	metrics        AgentMetrics
	policyDir      string
}

// OpenAgentContext starts the child and completes the exact hello handshake.
func OpenAgentContext(ctx context.Context, projectRoot string, options ...AgentContextOptions) (*AgentContext, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if len(options) > 1 {
		return nil, NewConfigurationError("at most one AgentContextOptions value is allowed")
	}
	option := AgentContextOptions{}
	if len(options) == 1 {
		option = options[0]
	}
	root, err := validateAgentRoot(projectRoot)
	if err != nil {
		return nil, err
	}
	if option.Task != "" {
		if err := boundedText(option.Task, "task", maxTaskBytes, false); err != nil {
			return nil, err
		}
	}
	policy := option.ExecutionPolicy
	if policy.MaxTimeout == 0 && policy.AllowedExecutables == nil && policy.AllowedEnv == nil {
		policy = defaultExecutionPolicy()
	}
	policy, err = normalizeExecutionPolicy(policy)
	if err != nil {
		return nil, err
	}
	if option.Permissions.Execute && len(policy.AllowedExecutables) == 0 {
		return nil, NewConfigurationError("execute permission requires at least one allowed executable")
	}
	timeout := option.Timeout
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	if timeout < 100*time.Millisecond || timeout > 120*time.Second {
		return nil, NewConfigurationError("timeout must be between 0.1 and 120 seconds")
	}
	binary := option.EngineBinary
	if binary == "" {
		binary = "lean-ctx"
	}
	client := &AgentContext{ProjectRoot: root, Task: option.Task, Permissions: option.Permissions, ExecutionPolicy: policy, EngineBinary: binary, Timeout: timeout, stderrDone: make(chan struct{})}
	if err := client.start(ctx); err != nil {
		client.terminate()
		return nil, err
	}
	return client, nil
}

// NewAgentContext is the synchronous convenience constructor.
func NewAgentContext(projectRoot string, options ...AgentContextOptions) (*AgentContext, error) {
	return OpenAgentContext(context.Background(), projectRoot, options...)
}

func validateAgentRoot(projectRoot string) (string, error) {
	if projectRoot == "" || strings.IndexByte(projectRoot, 0) >= 0 || len([]byte(projectRoot)) > maxPathBytes {
		return "", NewConfigurationError("project_root must be a directory")
	}
	root, err := filepath.Abs(filepath.Clean(projectRoot))
	if err != nil {
		return "", NewConfigurationError("project_root must be a directory")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", NewConfigurationError("project_root must be a directory")
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return "", NewConfigurationError("project_root must be a directory")
	}
	return root, nil
}

func (a *AgentContext) start(ctx context.Context) error {
	policyDir, err := os.MkdirTemp(a.ProjectRoot, ".leanctx-agent-")
	if err != nil {
		return NewEngineUnavailable("Agent Tools policy directory could not be created")
	}
	a.policyDir = policyDir
	if err := os.Chmod(policyDir, 0700); err != nil {
		return NewEngineUnavailable("Agent Tools policy directory could not be secured")
	}
	policyPath := filepath.Join(policyDir, "policy.json")
	policy := map[string]any{"allow_exec": a.Permissions.Execute, "allow_write": a.Permissions.Write, "allowed_env": a.ExecutionPolicy.AllowedEnv, "allowed_executables": a.ExecutionPolicy.AllowedExecutables, "max_timeout_ms": int64(a.ExecutionPolicy.MaxTimeout / time.Millisecond), "schema_version": int64(AgentToolsSchemaVersion)}
	payload, err := canonicalJSON(policy)
	if err != nil {
		return NewEngineUnavailable("Agent Tools policy could not be encoded")
	}
	file, err := os.OpenFile(policyPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return NewEngineUnavailable("Agent Tools policy could not be created")
	}
	if _, err = file.Write(payload); err == nil {
		err = file.Sync()
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return NewEngineUnavailable("Agent Tools policy could not be written")
	}
	binary, err := resolveExecutable(a.EngineBinary)
	if err != nil {
		return err
	}
	command := exec.Command(binary, "engine", "tool-session", "--project-root", a.ProjectRoot, "--policy-file", policyPath)
	command.Dir = a.ProjectRoot
	command.Env = []string{"LC_ALL=C", "LANG=C", "TZ=UTC", "PYTHONHASHSEED=0"}
	configureProcessGroup(command)
	stdin, err := command.StdinPipe()
	if err != nil {
		return NewEngineUnavailable("Agent Tools stdin could not be opened")
	}
	stdout, err := command.StdoutPipe()
	if err != nil {
		return NewEngineUnavailable("Agent Tools stdout could not be opened")
	}
	stderr, err := command.StderrPipe()
	if err != nil {
		return NewEngineUnavailable("Agent Tools stderr could not be opened")
	}
	if err := command.Start(); err != nil {
		return NewEngineUnavailable("Agent Tools Engine could not be started")
	}
	a.command = command
	a.stdin = bufio.NewWriter(stdin)
	a.stdout = bufio.NewReaderSize(stdout, 64*1024)
	go a.collectStderr(stderr)
	result, err := a.exchange(ctx, map[string]any{"op": "hello", "schema_version": int64(AgentToolsSchemaVersion), "transport_version": int64(AgentToolsTransportVersion), "agent_tools_interface_version": AgentToolsInterfaceVersion, "sdk_version": Version}, a.Timeout)
	if err != nil {
		return err
	}
	if err := a.acceptHello(result); err != nil {
		return err
	}
	a.removePolicy()
	return nil
}

func (a *AgentContext) removePolicy() {
	if a.policyDir == "" {
		return
	}
	_ = os.Remove(filepath.Join(a.policyDir, "policy.json"))
	_ = os.Remove(a.policyDir)
	a.policyDir = ""
}

func (a *AgentContext) collectStderr(reader io.Reader) {
	defer close(a.stderrDone)
	buffer := make([]byte, 32*1024)
	for {
		count, err := reader.Read(buffer)
		if count > 0 {
			a.mu.Lock()
			if a.stderr.Len()+count <= maxStderrBytes {
				_, _ = a.stderr.Write(buffer[:count])
			} else {
				a.stderrOverflow = true
				remaining := maxStderrBytes - a.stderr.Len()
				if remaining > 0 {
					_, _ = a.stderr.Write(buffer[:remaining])
				}
			}
			a.mu.Unlock()
		}
		if err != nil {
			return
		}
	}
}

func (a *AgentContext) nextRequestID() string {
	a.nextID++
	return strconv.FormatUint(a.nextID, 10)
}

type agentReadResult struct {
	line []byte
	err  error
}

func readAgentLine(reader *bufio.Reader) ([]byte, error) {
	var line []byte
	for {
		part, err := reader.ReadSlice('\n')
		line = append(line, part...)
		if len(line) > maxAgentResponseBytes {
			return nil, fmt.Errorf("response too large")
		}
		if err == nil {
			return line, nil
		}
		if errors.Is(err, bufio.ErrBufferFull) {
			continue
		}
		return line, err
	}
}

func (a *AgentContext) exchange(ctx context.Context, request map[string]any, timeout time.Duration) (map[string]any, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed || a.command == nil || a.command.ProcessState != nil {
		return nil, NewEngineCrashed(a.crashMessageLocked())
	}
	requestID := a.nextRequestID()
	envelope := make(map[string]any, len(request)+1)
	for key, value := range request {
		envelope[key] = value
	}
	envelope["id"] = requestID
	payload, err := canonicalJSON(envelope)
	if err != nil {
		return nil, NewEngineProtocolError("Agent Tools request is not deterministic JSON")
	}
	payload = append(payload, '\n')
	if len(payload) > maxAgentRequestBytes {
		return nil, NewEngineProtocolError("Agent Tools request exceeds its bound")
	}
	if _, err := a.stdin.Write(payload); err != nil {
		a.closed = true
		return nil, NewEngineCrashed(a.crashMessageLocked())
	}
	if err := a.stdin.Flush(); err != nil {
		a.closed = true
		return nil, NewEngineCrashed(a.crashMessageLocked())
	}
	readResult := make(chan agentReadResult, 1)
	go func() {
		line, err := readAgentLine(a.stdout)
		readResult <- agentReadResult{line: line, err: err}
	}()
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	select {
	case <-ctx.Done():
		a.closed = true
		terminateProcess(a.command)
		<-readResult
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, NewEngineTimeout("Agent Tools response exceeded its deadline")
		}
		return nil, ctx.Err()
	case <-deadline.C:
		a.closed = true
		terminateProcess(a.command)
		<-readResult
		return nil, NewEngineTimeout("Agent Tools response exceeded its deadline")
	case result := <-readResult:
		if result.err != nil {
			a.closed = true
			if errors.Is(result.err, bufio.ErrBufferFull) {
				return nil, NewEngineProtocolError("Agent Tools response exceeds its bound")
			}
			return nil, NewEngineCrashed(a.crashMessageLocked())
		}
		if len(result.line) == 0 || result.line[len(result.line)-1] != '\n' {
			a.closed = true
			return nil, NewEngineProtocolError("Agent Tools response is not newline terminated")
		}
		decoded, err := strictJSONLoads(bytes.TrimSuffix(result.line, []byte{'\n'}), "Agent Tools response")
		if err != nil {
			a.closed = true
			terminateProcess(a.command)
			return nil, NewEngineProtocolError("Agent Tools response is invalid JSON")
		}
		response, ok := decoded.(map[string]any)
		if !ok {
			a.closed = true
			terminateProcess(a.command)
			return nil, NewEngineProtocolError("Agent Tools response envelope is invalid")
		}
		id, ok := response["id"].(string)
		if !ok || id != requestID {
			a.closed = true
			terminateProcess(a.command)
			return nil, NewEngineProtocolError("Agent Tools response id is unexpected")
		}
		okValue, ok := response["ok"].(bool)
		if !okValue && !ok {
			a.closed = true
			terminateProcess(a.command)
			return nil, NewEngineProtocolError("Agent Tools response envelope is invalid")
		}
		if okValue {
			if len(response) != 3 {
				a.closed = true
				terminateProcess(a.command)
				return nil, NewEngineProtocolError("Agent Tools response envelope fields are invalid")
			}
			result, ok := response["result"].(map[string]any)
			if !ok {
				a.closed = true
				terminateProcess(a.command)
				return nil, NewEngineProtocolError("Agent Tools response omitted result")
			}
			return result, nil
		}
		if len(response) != 3 {
			a.closed = true
			terminateProcess(a.command)
			return nil, NewEngineProtocolError("Agent Tools error envelope fields are invalid")
		}
		errorValue, ok := response["error"].(map[string]any)
		if !ok || len(errorValue) != 2 {
			a.closed = true
			terminateProcess(a.command)
			return nil, NewEngineProtocolError("Agent Tools error envelope is invalid")
		}
		code, codeOK := errorValue["code"].(string)
		message, messageOK := errorValue["message"].(string)
		if !codeOK || !messageOK {
			a.closed = true
			terminateProcess(a.command)
			return nil, NewEngineProtocolError("Agent Tools error envelope is invalid")
		}
		return nil, agentErrorFromWire(code, message)
	}
}

func agentErrorFromWire(code, message string) error {
	switch code {
	case "permission_denied":
		return NewAgentPermissionError(message)
	case "unsupported_capability":
		return NewUnsupportedCapabilityError(message)
	case "invalid_request", "invalid_state", "unsupported_interface":
		return NewEngineProtocolError(message)
	default:
		return NewEngineExecutionError(message, nil, nil)
	}
}

func (a *AgentContext) acceptHello(result map[string]any) error {
	expected := map[string]bool{"agent_tools_interface_version": true, "allow_exec": true, "allow_write": true, "capabilities": true, "engine_version": true, "schema_version": true, "transport_version": true}
	if err := exactObjectKeys(result, expected, "Agent Tools hello"); err != nil {
		return NewEngineProtocolError("Agent Tools hello is incompatible")
	}
	if result["agent_tools_interface_version"] != AgentToolsInterfaceVersion || result["engine_version"] != SupportedAgentToolsEngineVersion || result["allow_exec"] != a.Permissions.Execute || result["allow_write"] != a.Permissions.Write {
		return NewEngineProtocolError("Agent Tools hello is incompatible")
	}
	if schema, err := protocolInt(result["schema_version"], "hello.schema_version"); err != nil || schema != AgentToolsSchemaVersion {
		return NewEngineProtocolError("Agent Tools hello is incompatible")
	}
	if transport, err := protocolInt(result["transport_version"], "hello.transport_version"); err != nil || transport != AgentToolsTransportVersion {
		return NewEngineProtocolError("Agent Tools hello is incompatible")
	}
	values, ok := result["capabilities"].([]any)
	if !ok {
		return NewEngineProtocolError("Agent Tools capabilities are invalid")
	}
	capabilities := make([]string, len(values))
	for i, value := range values {
		capabilities[i], ok = value.(string)
		if !ok {
			return NewEngineProtocolError("Agent Tools capabilities are invalid")
		}
	}
	sorted := append([]string(nil), capabilities...)
	sort.Strings(sorted)
	if !equalStrings(capabilities, uniqueStrings(sorted)) {
		return NewEngineProtocolError("Agent Tools capabilities are not canonical")
	}
	expectedCapabilities := append([]string(nil), agentReadTools...)
	if a.Permissions.Write {
		expectedCapabilities = append(expectedCapabilities, agentWriteTools...)
	}
	if a.Permissions.Execute {
		expectedCapabilities = append(expectedCapabilities, agentExecuteTools...)
	}
	sort.Strings(expectedCapabilities)
	if !equalStrings(capabilities, uniqueStrings(expectedCapabilities)) {
		return NewEngineProtocolError("Agent Tools capabilities do not match policy")
	}
	a.capabilities = append([]string(nil), capabilities...)
	a.helloAccepted = true
	return nil
}

func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}

func (a *AgentContext) crashMessageLocked() string {
	detail := strings.TrimSpace(a.stderr.String())
	if detail == "" {
		return "Agent Tools Engine exited"
	}
	if len(detail) > 4096 {
		detail = detail[:4096]
	}
	return "Agent Tools Engine exited: " + detail
}

func (a *AgentContext) callToolContext(ctx context.Context, tool string, arguments map[string]any, timeout time.Duration) (*ToolResult, error) {
	a.mu.Lock()
	capable := contains(a.capabilities, tool)
	a.mu.Unlock()
	if !capable {
		return nil, NewUnsupportedCapabilityError("Engine did not negotiate capability: " + tool)
	}
	canonical, err := canonicalJSON(arguments)
	if err != nil {
		return nil, NewValidationError("arguments must be deterministic JSON data")
	}
	if len(canonical) > maxAgentRequestBytes {
		return nil, NewValidationError("arguments exceed the request bound")
	}
	value, err := a.exchange(ctx, map[string]any{"op": "call", "tool": tool, "arguments": arguments}, timeout)
	if err != nil {
		return nil, err
	}
	result, err := parseToolResult(tool, value)
	if err != nil {
		a.terminalProtocol(err)
		return nil, err
	}
	a.mu.Lock()
	a.metrics.ToolCalls++
	a.metrics.OriginalTokens += result.OriginalTokens
	a.metrics.OutputTokens += result.OutputTokens
	a.metrics.SavedTokens += result.SavedTokens
	a.mu.Unlock()
	return result, nil
}

func parseToolResult(tool string, value map[string]any) (*ToolResult, error) {
	expected := map[string]bool{"text": true, "content_blocks": true, "original_tokens": true, "output_tokens": true, "saved_tokens": true, "mode": true, "changed": true, "shell": true}
	if err := exactObjectKeys(value, expected, "Agent Tools result"); err != nil {
		return nil, err
	}
	text, ok := value["text"].(string)
	if !ok || len([]byte(text)) > maxTextBytes {
		return nil, NewEngineProtocolError("Agent Tools text is invalid")
	}
	var mode *string
	if value["mode"] != nil {
		modeValue, ok := value["mode"].(string)
		if !ok {
			return nil, NewEngineProtocolError("Agent Tools mode is invalid")
		}
		mode = &modeValue
	}
	original, err := protocolInt(value["original_tokens"], "Agent Tools original_tokens")
	if err != nil || original < 0 {
		return nil, NewEngineProtocolError("Agent Tools token metrics are invalid")
	}
	output, err := protocolInt(value["output_tokens"], "Agent Tools output_tokens")
	if err != nil || output < 0 {
		return nil, NewEngineProtocolError("Agent Tools token metrics are invalid")
	}
	saved, err := protocolInt(value["saved_tokens"], "Agent Tools saved_tokens")
	if err != nil || saved < 0 || output+saved != original {
		return nil, NewEngineProtocolError("Agent Tools token metrics are invalid")
	}
	changed, ok := value["changed"].(bool)
	if !ok {
		return nil, NewEngineProtocolError("Agent Tools status metadata is invalid")
	}
	blocksValue, ok := value["content_blocks"].([]any)
	if !ok {
		return nil, NewEngineProtocolError("Agent Tools content blocks are invalid")
	}
	blocks := make([]map[string]any, len(blocksValue))
	for i, blockValue := range blocksValue {
		block, ok := blockValue.(map[string]any)
		if !ok {
			return nil, NewEngineProtocolError("Agent Tools content blocks are invalid")
		}
		blocks[i], err = cloneMap(block)
		if err != nil {
			return nil, NewEngineProtocolError("Agent Tools content blocks are invalid")
		}
	}
	var shell map[string]any
	if value["shell"] != nil {
		item, ok := value["shell"].(map[string]any)
		if !ok {
			return nil, NewEngineProtocolError("Agent Tools shell metadata is invalid")
		}
		shell, err = cloneMap(item)
		if err != nil {
			return nil, NewEngineProtocolError("Agent Tools shell metadata is invalid")
		}
	}
	return &ToolResult{Tool: tool, Text: text, ContentBlocks: blocks, OriginalTokens: original, OutputTokens: output, SavedTokens: saved, Mode: mode, Changed: changed, Shell: shell}, nil
}

func (a *AgentContext) terminalProtocol(err error) {
	a.mu.Lock()
	a.closed = true
	command := a.command
	a.mu.Unlock()
	if command != nil {
		terminateProcess(command)
	}
}

// Call invokes a negotiated non-execution tool.
func (a *AgentContext) Call(tool string, arguments ...map[string]any) (*ToolResult, error) {
	return a.CallContext(context.Background(), tool, arguments...)
}

func (a *AgentContext) CallContext(ctx context.Context, tool string, arguments ...map[string]any) (*ToolResult, error) {
	if tool == "" {
		return nil, NewValidationError("tool must be a non-empty string")
	}
	if contains(agentExecuteTools, tool) {
		return nil, NewAgentPermissionError("execution tools must use Run")
	}
	if contains(agentWriteTools, tool) && !a.Permissions.Write {
		return nil, NewAgentPermissionError("write permission is disabled")
	}
	var value map[string]any
	if len(arguments) == 1 {
		value = arguments[0]
	} else if len(arguments) == 0 {
		value = map[string]any{}
	} else {
		return nil, NewValidationError("at most one arguments mapping is allowed")
	}
	if value == nil {
		value = map[string]any{}
	}
	return a.callToolContext(ctx, tool, value, a.Timeout)
}

// Read invokes ctx_read. The optional argument may be ReadOptions, ReadMode,
// a mode string, or a bool fresh flag.
func (a *AgentContext) Read(path string, arguments ...any) (*ToolResult, error) {
	return a.ReadContext(context.Background(), path, arguments...)
}

func (a *AgentContext) ReadContext(ctx context.Context, path string, arguments ...any) (*ToolResult, error) {
	options, err := readOptions(arguments...)
	if err != nil {
		return nil, err
	}
	return a.callToolContext(ctx, "ctx_read", map[string]any{"path": path, "mode": string(options.Mode), "fresh": options.Fresh}, a.Timeout)
}

func readOptions(arguments ...any) (ReadOptions, error) {
	options := ReadOptions{Mode: ReadModeAuto}
	for _, argument := range arguments {
		switch value := argument.(type) {
		case ReadOptions:
			options = value
		case ReadMode:
			options.Mode = value
		case string:
			options.Mode = ReadMode(value)
		case bool:
			options.Fresh = value
		default:
			return ReadOptions{}, NewValidationError("invalid Read options")
		}
	}
	if options.Mode == "" {
		options.Mode = ReadModeAuto
	}
	return options, nil
}

func (a *AgentContext) Search(pattern string, options ...SearchOptions) (*ToolResult, error) {
	option := SearchOptions{Path: ".", MaxResults: 50}
	if len(options) > 1 {
		return nil, NewValidationError("at most one SearchOptions value is allowed")
	}
	if len(options) == 1 {
		option = options[0]
		if option.Path == "" {
			option.Path = "."
		}
		if option.MaxResults == 0 {
			option.MaxResults = 50
		}
	}
	args := map[string]any{"path": option.Path, "pattern": pattern, "max_results": int64(option.MaxResults)}
	if option.Include != "" {
		args["include"] = option.Include
	}
	return a.callToolContext(context.Background(), "ctx_search", args, a.Timeout)
}

func (a *AgentContext) Glob(pattern string, options ...GlobOptions) (*ToolResult, error) {
	option := GlobOptions{Path: ".", MaxResults: 200}
	if len(options) > 1 {
		return nil, NewValidationError("at most one GlobOptions value is allowed")
	}
	if len(options) == 1 {
		option = options[0]
		if option.Path == "" {
			option.Path = "."
		}
		if option.MaxResults == 0 {
			option.MaxResults = 200
		}
	}
	return a.callToolContext(context.Background(), "ctx_glob", map[string]any{"path": option.Path, "pattern": pattern, "max_results": int64(option.MaxResults)}, a.Timeout)
}

func (a *AgentContext) Tree(options ...TreeOptions) (*ToolResult, error) {
	option := TreeOptions{Path: ".", Depth: 3}
	if len(options) > 1 {
		return nil, NewValidationError("at most one TreeOptions value is allowed")
	}
	if len(options) == 1 {
		option = options[0]
		if option.Path == "" {
			option.Path = "."
		}
		if option.Depth == 0 {
			option.Depth = 3
		}
	}
	return a.callToolContext(context.Background(), "ctx_tree", map[string]any{"path": option.Path, "depth": int64(option.Depth), "show_hidden": option.ShowHidden}, a.Timeout)
}

func (a *AgentContext) Compose(task ...string) (*ToolResult, error) {
	value := a.Task
	if len(task) > 1 {
		return nil, NewValidationError("at most one compose task is allowed")
	}
	if len(task) == 1 {
		value = task[0]
	}
	return a.callToolContext(context.Background(), "ctx_compose", map[string]any{"path": ".", "task": value}, a.Timeout)
}

func (a *AgentContext) Symbol(name string) (*ToolResult, error) {
	return a.callToolContext(context.Background(), "ctx_symbol", map[string]any{"name": name}, a.Timeout)
}

// CreateFile, ReplaceUnique and Patch are write-gated convenience calls.
func (a *AgentContext) CreateFile(path, text string) (*ToolResult, error) {
	return a.writeCall("ctx_patch", map[string]any{"path": path, "op": "create", "new_text": text})
}

func (a *AgentContext) ReplaceUnique(path, oldText, newText string) (*ToolResult, error) {
	return a.writeCall("ctx_patch", map[string]any{"path": path, "op": "replace_unique", "old_text": oldText, "new_text": newText})
}

func (a *AgentContext) Patch(arguments map[string]any) (*ToolResult, error) {
	return a.writeCall("ctx_patch", arguments)
}

func (a *AgentContext) writeCall(tool string, arguments map[string]any) (*ToolResult, error) {
	if !a.Permissions.Write {
		return nil, NewAgentPermissionError("write permission is disabled")
	}
	return a.callToolContext(context.Background(), tool, arguments, a.Timeout)
}

// Run executes one structured argv request through ctx_shell.
func (a *AgentContext) Run(argv []string, options ...RunOptions) (*ToolResult, error) {
	return a.RunContext(context.Background(), argv, options...)
}

func (a *AgentContext) RunContext(ctx context.Context, argv []string, options ...RunOptions) (*ToolResult, error) {
	if !a.Permissions.Execute {
		return nil, NewAgentPermissionError("execute permission is disabled")
	}
	if len(argv) == 0 {
		return nil, NewValidationError("argv must be a non-empty sequence of strings")
	}
	for _, argument := range argv {
		if argument == "" {
			return nil, NewValidationError("argv must be a non-empty sequence of strings")
		}
	}
	executable := argv[0]
	if !validExecutableName(executable) || !contains(a.ExecutionPolicy.AllowedExecutables, executable) {
		return nil, NewAgentPermissionError("executable is not allowed: " + executable)
	}
	option := RunOptions{CWD: "."}
	if len(options) > 1 {
		return nil, NewValidationError("at most one RunOptions value is allowed")
	}
	if len(options) == 1 {
		option = options[0]
		if option.CWD == "" {
			option.CWD = "."
		}
	}
	absoluteCWD, err := filepath.Abs(filepath.Join(a.ProjectRoot, option.CWD))
	if err != nil || !containedPath(absoluteCWD, a.ProjectRoot) {
		return nil, NewAgentPermissionError("cwd escapes project root")
	}
	for key, value := range option.Env {
		if !validEnvironmentName(key) || !contains(a.ExecutionPolicy.AllowedEnv, key) {
			return nil, NewAgentPermissionError("environment variable is not allowed: " + key)
		}
		if !utf8.ValidString(value) || strings.IndexByte(value, 0) >= 0 {
			return nil, NewValidationError("env values must be valid strings")
		}
	}
	timeout := option.Timeout
	if timeout == 0 {
		timeout = a.ExecutionPolicy.MaxTimeout
	}
	if timeout < 100*time.Millisecond || timeout > a.ExecutionPolicy.MaxTimeout {
		return nil, NewValidationError("timeout exceeds ExecutionPolicy")
	}
	environment := make(map[string]any, len(option.Env))
	for key, value := range option.Env {
		environment[key] = value
	}
	request := map[string]any{"argv": append([]string(nil), argv...), "cwd": filepath.ToSlash(option.CWD), "env": environment, "timeout_ms": int64(timeout / time.Millisecond)}
	return a.callToolContext(ctx, "ctx_shell", request, maxDuration(a.Timeout, timeout+2*time.Second))
}

func maxDuration(left, right time.Duration) time.Duration {
	if left > right {
		return left
	}
	return right
}

func (a *AgentContext) Capabilities() []string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]string(nil), a.capabilities...)
}

func (a *AgentContext) Metrics() AgentMetrics {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.metrics
}

func (a *AgentContext) Close() error {
	a.mu.Lock()
	closed := a.closed
	a.mu.Unlock()
	if closed {
		a.terminate()
		return nil
	}
	_, err := a.exchange(context.Background(), map[string]any{"op": "close"}, a.Timeout)
	a.terminate()
	return err
}

func (a *AgentContext) Cancel() {
	a.terminate()
}

func (a *AgentContext) terminate() {
	a.mu.Lock()
	if a.closed && a.command == nil {
		a.mu.Unlock()
		return
	}
	a.closed = true
	command := a.command
	policyDir := a.policyDir
	a.command = nil
	a.mu.Unlock()
	if command != nil {
		terminateProcess(command)
		_ = command.Wait()
	}
	if a.stderrDone != nil {
		select {
		case <-a.stderrDone:
		case <-time.After(2 * time.Second):
		}
	}
	if policyDir != "" {
		_ = os.Remove(filepath.Join(policyDir, "policy.json"))
		_ = os.Remove(policyDir)
	}
}

// Reconnect always creates a fresh process and reuses the original policy
// snapshot. A closed context cannot be made live again.
func (a *AgentContext) Reconnect(ctx context.Context) (*AgentContext, error) {
	a.Cancel()
	return OpenAgentContext(ctx, a.ProjectRoot, AgentContextOptions{Task: a.Task, Permissions: a.Permissions, ExecutionPolicy: a.ExecutionPolicy, EngineBinary: a.EngineBinary, Timeout: a.Timeout})
}

func resolveExecutable(value string) (string, error) {
	if value == "" {
		return "", NewEngineUnavailable("configured Engine binary is unavailable")
	}
	candidate := value
	if !filepath.IsAbs(candidate) && !strings.ContainsAny(candidate, `/\\`) {
		candidate = ""
		for _, directory := range filepath.SplitList(os.Getenv("PATH")) {
			if directory == "" {
				continue
			}
			path := filepath.Join(directory, value)
			if isExecutableRegular(path) {
				candidate = path
				break
			}
		}
		if candidate == "" {
			return "", NewEngineUnavailable("configured Engine binary is unavailable")
		}
	} else {
		var err error
		candidate, err = filepath.Abs(candidate)
		if err != nil {
			return "", NewEngineUnavailable("configured Engine binary is unavailable")
		}
	}
	resolved, err := filepath.EvalSymlinks(candidate)
	if err != nil || !isExecutableRegular(resolved) {
		return "", NewEngineUnavailable("configured Engine binary is unavailable")
	}
	return resolved, nil
}

// AsyncAgentContext is a context.Context-driven facade for event-loop hosts.
type AsyncAgentContext struct {
	root    string
	options AgentContextOptions
	current *AgentContext
	mu      sync.Mutex
}

func NewAsyncAgentContext(projectRoot string, options ...AgentContextOptions) (*AsyncAgentContext, error) {
	if len(options) > 1 {
		return nil, NewConfigurationError("at most one AgentContextOptions value is allowed")
	}
	var option AgentContextOptions
	if len(options) == 1 {
		option = options[0]
	}
	return &AsyncAgentContext{root: projectRoot, options: option}, nil
}

func (a *AsyncAgentContext) Open(ctx context.Context) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.current != nil {
		return nil
	}
	current, err := OpenAgentContext(ctx, a.root, a.options)
	if err != nil {
		return err
	}
	a.current = current
	return nil
}

func (a *AsyncAgentContext) context() (*AgentContext, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.current == nil {
		return nil, NewEngineUnavailable("AsyncAgentContext is not open")
	}
	return a.current, nil
}

func (a *AsyncAgentContext) Call(ctx context.Context, tool string, arguments ...map[string]any) (*ToolResult, error) {
	current, err := a.context()
	if err != nil {
		return nil, err
	}
	return current.CallContext(ctx, tool, arguments...)
}

func (a *AsyncAgentContext) Read(ctx context.Context, path string, arguments ...any) (*ToolResult, error) {
	current, err := a.context()
	if err != nil {
		return nil, err
	}
	return current.ReadContext(ctx, path, arguments...)
}

func (a *AsyncAgentContext) Run(ctx context.Context, argv []string, options ...RunOptions) (*ToolResult, error) {
	current, err := a.context()
	if err != nil {
		return nil, err
	}
	return current.RunContext(ctx, argv, options...)
}

func (a *AsyncAgentContext) Metrics() (AgentMetrics, error) {
	current, err := a.context()
	if err != nil {
		return AgentMetrics{}, err
	}
	return current.Metrics(), nil
}

func (a *AsyncAgentContext) Capabilities() ([]string, error) {
	current, err := a.context()
	if err != nil {
		return nil, err
	}
	return current.Capabilities(), nil
}

func (a *AsyncAgentContext) Close() error {
	a.mu.Lock()
	current := a.current
	a.current = nil
	a.mu.Unlock()
	if current == nil {
		return nil
	}
	return current.Close()
}
