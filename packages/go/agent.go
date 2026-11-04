package leanctx

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
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
	"unicode"
	"unicode/utf8"
)

const (
	maxAgentRequestBytes  = 1024 * 1024
	maxAgentResponseBytes = 16 * 1024 * 1024
)

var forbiddenEnvironment = map[string]bool{
	"COMSPEC": true, "DYLD_INSERT_LIBRARIES": true, "HOME": true,
	"DYLD_LIBRARY_PATH": true,
	"LD_LIBRARY_PATH":   true,
	"LD_PRELOAD":        true, "PATH": true, "PATHEXT": true,
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
		// Engine policy lists are JSON arrays, including an empty deny-all list.
		return []string{}
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
	InstanceBinary  string
	// Deprecated: use InstanceBinary. Used only when InstanceBinary is empty.
	EngineBinary string
	Timeout      time.Duration
	GitLabSource *GitLabSource
}

// GitLabSource selects a host-owned GitLab CLI source for one Agent Tools session.
type GitLabSource struct {
	Host      string
	Project   int64
	Namespace string
	Glab      string
	ConfigDir string
}

const maxSafeGitLabProject int64 = 9007199254740991

func copyGitLabSource(source *GitLabSource) (*GitLabSource, error) {
	if source == nil {
		return nil, nil
	}
	snapshot := *source
	for _, field := range []struct{ name, value string }{{"host", snapshot.Host}, {"namespace", snapshot.Namespace}} {
		name, value := field.name, field.value
		if value == "" || !utf8.ValidString(value) || strings.IndexFunc(value, unicode.IsControl) >= 0 {
			return nil, NewValidationError("GitLabSource " + name + " must be a non-empty string without control characters")
		}
	}
	if snapshot.Project < 1 || snapshot.Project > maxSafeGitLabProject {
		return nil, NewValidationError("GitLabSource project must be a safe positive integer")
	}
	for _, field := range []struct{ name, value string }{{"glab", snapshot.Glab}, {"config_dir", snapshot.ConfigDir}} {
		name, value := field.name, field.value
		if name == "config_dir" && value == "" {
			continue
		}
		if value == "" || !utf8.ValidString(value) || strings.IndexFunc(value, unicode.IsControl) >= 0 || !filepath.IsAbs(value) {
			return nil, NewValidationError("GitLabSource " + name + " must be an absolute path without control characters")
		}
	}
	return &snapshot, nil
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
	InstanceBinary  string
	// Deprecated: use InstanceBinary. Mirrors InstanceBinary as opened.
	EngineBinary string
	Timeout      time.Duration
	gitlabSource *GitLabSource

	// exchangeSlot admits one exchange at a time. It is a channel, not a
	// mutex, so that a waiting call still honours its own context and deadline.
	exchangeSlot chan struct{}
	mu           sync.Mutex
	command      *exec.Cmd
	// The context's own ends of the Engine pipes. Closing them is what ends
	// the transport and stderr goroutines when the Engine cannot be reaped.
	pipes          []io.Closer
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
	// Shutdown has exactly one owner. shutdownDone closes when that owner has
	// finished; processFailure is its stored result and never changes afterwards.
	shutdownStarted bool
	shutdownDone    chan struct{}
	processFailure  error
	containment     containmentState
}

// SessionContainment says how the Engine's own process tree is contained.
//
// SessionContainmentInstanceStarter: the process the SDK started is the Engine's
// session starter. It runs the Engine in a process group of its own, ends that
// group when the Engine exits, when the SDK ends the starter or when the SDK's
// process is gone, and exits only afterwards.
//
// SessionContainmentUnavailable is reduced containment: the Engine binary has
// no such mode, or the platform is Windows. The SDK's own process-group
// handling is then all there is.
type SessionContainment string

const (
	SessionContainmentInstanceStarter SessionContainment = "engine-starter"
	SessionContainmentUnavailable     SessionContainment = "unavailable"
)

const sessionContainmentEnv = "LEAN_CTX_SESSION_CONTAINMENT"

// The starter writes this line to standard output before the Engine exists.
var sessionContainmentAck = []byte("{\"leanctx_session_containment\":1}\n")

// containmentState records whether the first output line of an Engine start
// was the starter's acknowledgement. Only the very first line can be.
type containmentState struct {
	mu       sync.Mutex
	seen     bool
	contains bool
}

func (s *containmentState) reset() {
	s.mu.Lock()
	s.seen, s.contains = false, false
	s.mu.Unlock()
}

// firstLine reports whether line is the acknowledgement. It answers true at
// most once per start, and only for the first line.
func (s *containmentState) firstLine(line []byte) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.seen {
		return false
	}
	s.seen = true
	s.contains = bytes.Equal(line, sessionContainmentAck)
	return s.contains
}

func (s *containmentState) value() SessionContainment {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.contains {
		return SessionContainmentInstanceStarter
	}
	return SessionContainmentUnavailable
}

// SessionContainment reports how the Engine's process tree is contained. It is
// final once the context is open.
func (a *AgentContext) SessionContainment() SessionContainment {
	return a.containment.value()
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
	selectedGitLab, err := copyGitLabSource(option.GitLabSource)
	if err != nil {
		return nil, err
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
	binary := option.InstanceBinary
	if binary == "" {
		binary = option.EngineBinary
	}
	if binary == "" {
		binary = "lean-ctx"
	}
	client := &AgentContext{ProjectRoot: root, Task: option.Task, Permissions: option.Permissions, ExecutionPolicy: policy, InstanceBinary: binary, EngineBinary: binary, Timeout: timeout, gitlabSource: selectedGitLab, stderrDone: make(chan struct{}), exchangeSlot: make(chan struct{}, 1), shutdownDone: make(chan struct{})}
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
		return NewInstanceUnavailable("Agent Tools policy directory could not be created")
	}
	a.policyDir = policyDir
	if err := os.Chmod(policyDir, 0700); err != nil {
		return NewInstanceUnavailable("Agent Tools policy directory could not be secured")
	}
	policyPath := filepath.Join(policyDir, "policy.json")
	policy := map[string]any{"allow_exec": a.Permissions.Execute, "allow_write": a.Permissions.Write, "allowed_env": a.ExecutionPolicy.AllowedEnv, "allowed_executables": a.ExecutionPolicy.AllowedExecutables, "max_timeout_ms": int64(a.ExecutionPolicy.MaxTimeout / time.Millisecond), "schema_version": int64(AgentToolsSchemaVersion)}
	if a.gitlabSource != nil {
		selected := map[string]any{"host": a.gitlabSource.Host, "project": a.gitlabSource.Project, "namespace": a.gitlabSource.Namespace, "glab": a.gitlabSource.Glab}
		if a.gitlabSource.ConfigDir != "" {
			selected["config_dir"] = a.gitlabSource.ConfigDir
		}
		policy["selected_gitlab"] = selected
	}
	payload, err := canonicalJSON(policy)
	if err != nil {
		return NewInstanceUnavailable("Agent Tools policy could not be encoded")
	}
	file, err := os.OpenFile(policyPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return NewInstanceUnavailable("Agent Tools policy could not be created")
	}
	if _, err = file.Write(payload); err == nil {
		err = file.Sync()
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return NewInstanceUnavailable("Agent Tools policy could not be written")
	}
	binary, err := resolveExecutable(a.InstanceBinary)
	if err != nil {
		return err
	}
	command := exec.Command(binary, "engine", "tool-session", "--project-root", a.ProjectRoot, "--policy-file", policyPath)
	command.Dir = a.ProjectRoot
	command.Env = []string{"LC_ALL=C", "LANG=C", "TZ=UTC", "PYTHONHASHSEED=0"}
	// Host-selected Engine stores/privacy only; shell execution keeps its own policy.
	for _, name := range []string{"LEAN_CTX_CONFIG_DIR", "LEAN_CTX_DATA_DIR", "LEAN_CTX_STATE_DIR", "LEAN_CTX_CACHE_DIR", "DO_NOT_TRACK"} {
		if value, ok := os.LookupEnv(name); ok {
			command.Env = append(command.Env, name+"="+value)
		}
	}
	// Executable resolution needs a search path; forward it only under the execute permission.
	if a.Permissions.Execute {
		for _, name := range []string{"PATH", "TMPDIR", "TEMP", "TMP"} {
			if value, ok := os.LookupEnv(name); ok {
				command.Env = append(command.Env, name+"="+value)
			}
		}
	}
	configureProcessGroup(command)
	requestSessionContainment(command)
	stdin, err := command.StdinPipe()
	if err != nil {
		return NewInstanceUnavailable("Agent Tools stdin could not be opened")
	}
	stdout, err := command.StdoutPipe()
	if err != nil {
		return NewInstanceUnavailable("Agent Tools stdout could not be opened")
	}
	stderr, err := command.StderrPipe()
	if err != nil {
		return NewInstanceUnavailable("Agent Tools stderr could not be opened")
	}
	if err := command.Start(); err != nil {
		return NewInstanceUnavailable("Agent Tools instance could not be started")
	}
	a.command = command
	a.pipes = []io.Closer{stdin, stdout, stderr}
	a.stdin = bufio.NewWriter(stdin)
	a.stdout = bufio.NewReaderSize(stdout, 64*1024)
	a.containment.reset()
	go a.collectStderr(stderr)
	result, err := a.exchange(ctx, map[string]any{"op": "hello", "schema_version": int64(AgentToolsSchemaVersion), "transport_version": int64(AgentToolsTransportVersion), "agent_tools_interface_version": AgentToolsInterfaceVersion, "sdk_version": Version}, a.Timeout)
	if err != nil {
		return err
	}
	if err := a.acceptHello(result); err != nil {
		return err
	}
	return a.removePolicy()
}

// removePolicy removes exactly the temporary policy directory this context
// created: a direct child of the project root named ".leanctx-agent-*" that
// holds at most policy.json. It is idempotent; a directory that cannot be
// removed stays recorded for the next attempt and is reported.
func (a *AgentContext) removePolicy() error {
	a.mu.Lock()
	policyDir := a.policyDir
	a.mu.Unlock()
	if policyDir == "" {
		return nil
	}
	if filepath.Dir(policyDir) != a.ProjectRoot || !strings.HasPrefix(filepath.Base(policyDir), ".leanctx-agent-") {
		return NewInstanceExecutionError("Agent Tools policy path is not owned by this context", nil, nil)
	}
	fileErr := os.Remove(filepath.Join(policyDir, "policy.json"))
	dirErr := os.Remove(policyDir)
	for _, err := range []error{fileErr, dirErr} {
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return NewInstanceExecutionError("Agent Tools policy could not be removed", nil, nil)
		}
	}
	a.mu.Lock()
	if a.policyDir == policyDir {
		a.policyDir = ""
	}
	a.mu.Unlock()
	return nil
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
	line  []byte
	err   error
	write bool
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
	// One deadline covers waiting for the exchange slot, the request write and
	// the response.
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	// exchangeSlot serializes whole exchanges. a.mu guards state only and is
	// never held across pipe I/O, so Cancel, Close and the stderr collector
	// cannot be blocked by an Engine that stopped reading or answering. A call
	// that gives up while waiting for the slot never touched the wire: it
	// returns its own error and leaves the session and the running call intact.
	if err := ctx.Err(); err != nil {
		return nil, agentContextFailure(err)
	}
	select {
	case a.exchangeSlot <- struct{}{}:
	case <-ctx.Done():
		return nil, agentContextFailure(ctx.Err())
	case <-deadline.C:
		return nil, NewInstanceTimeout("Agent Tools request exceeded its deadline while another call was running")
	}
	defer func() { <-a.exchangeSlot }()
	a.mu.Lock()
	if a.closed || a.command == nil || a.command.ProcessState != nil {
		message := a.crashMessageLocked()
		a.mu.Unlock()
		return nil, NewInstanceCrashed(message)
	}
	requestID := a.nextRequestID()
	stdin, stdout := a.stdin, a.stdout
	a.mu.Unlock()
	envelope := make(map[string]any, len(request)+1)
	for key, value := range request {
		envelope[key] = value
	}
	envelope["id"] = requestID
	payload, err := canonicalJSON(envelope)
	if err != nil {
		return nil, NewInstanceProtocolError("Agent Tools request is not deterministic JSON")
	}
	payload = append(payload, '\n')
	if len(payload) > maxAgentRequestBytes {
		return nil, NewInstanceProtocolError("Agent Tools request exceeds its bound")
	}
	// The write and the read run on a goroutine that termination unblocks by
	// closing this context's own pipe ends.
	readResult := make(chan agentReadResult, 1)
	go func() {
		if _, err := stdin.Write(payload); err != nil {
			readResult <- agentReadResult{err: err, write: true}
			return
		}
		if err := stdin.Flush(); err != nil {
			readResult <- agentReadResult{err: err, write: true}
			return
		}
		line, err := readAgentLine(stdout)
		if err == nil && a.containment.firstLine(line) {
			// The session starter's acknowledgement is not a response.
			line, err = readAgentLine(stdout)
		}
		readResult <- agentReadResult{line: line, err: err}
	}()
	select {
	case <-ctx.Done():
		failure := a.terminalError(agentContextFailure(ctx.Err()))
		return nil, joinTransport(failure, readResult)
	case <-deadline.C:
		failure := a.terminalError(NewInstanceTimeout("Agent Tools response exceeded its deadline"))
		return nil, joinTransport(failure, readResult)
	case result := <-readResult:
		if result.err != nil {
			if !result.write && errors.Is(result.err, bufio.ErrBufferFull) {
				return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response exceeds its bound"))
			}
			return nil, a.terminalError(NewInstanceCrashed(a.crashMessage()))
		}
		if len(result.line) == 0 || result.line[len(result.line)-1] != '\n' {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response is not newline terminated"))
		}
		decoded, err := strictJSONLoads(bytes.TrimSuffix(result.line, []byte{'\n'}), "Agent Tools response")
		if err != nil {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response is invalid JSON"))
		}
		response, ok := decoded.(map[string]any)
		if !ok {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response envelope is invalid"))
		}
		id, ok := response["id"].(string)
		if !ok || id != requestID {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response id is unexpected"))
		}
		okValue, ok := response["ok"].(bool)
		if !okValue && !ok {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response envelope is invalid"))
		}
		if okValue {
			if len(response) != 3 {
				return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response envelope fields are invalid"))
			}
			result, ok := response["result"].(map[string]any)
			if !ok {
				return nil, a.terminalError(NewInstanceProtocolError("Agent Tools response omitted result"))
			}
			return result, nil
		}
		if len(response) != 3 {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools error envelope fields are invalid"))
		}
		errorValue, ok := response["error"].(map[string]any)
		if !ok || len(errorValue) != 2 {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools error envelope is invalid"))
		}
		code, codeOK := errorValue["code"].(string)
		message, messageOK := errorValue["message"].(string)
		if !codeOK || !messageOK {
			return nil, a.terminalError(NewInstanceProtocolError("Agent Tools error envelope is invalid"))
		}
		return nil, agentErrorFromWire(code, message)
	}
}

// terminalError ends the session through the one cleanup routine and returns
// failure. A cleanup error is never dropped: it is joined to failure, so
// errors.As and errors.Is still find the primary error.
func (a *AgentContext) terminalError(failure error) error {
	cleanupErr := a.shutdown()
	if cleanupErr == nil {
		return failure
	}
	return errors.Join(failure, cleanupErr)
}

// agentContextFailure maps a caller context error to the SDK's typed timeout
// where it is a deadline; a plain cancellation stays the caller's own error.
func agentContextFailure(err error) error {
	if errors.Is(err, context.DeadlineExceeded) {
		return NewInstanceTimeout("Agent Tools response exceeded its deadline")
	}
	return err
}

// Budgets for ending a session. They are variables so that tests can prove the
// bounded paths without waiting for the production values.
var (
	agentReapBudget      = 5 * time.Second
	agentTransportBudget = 2 * time.Second
	agentTerminate       = terminateProcess
)

var errAgentReapBudget = errors.New("process was not reaped within its budget")

// terminateAndReap ends the process tree, closes this context's own pipe ends
// so that its transport and stderr goroutines end even if the Engine survives,
// and reaps the leader within agentReapBudget. Nothing here can wait forever:
// if the Engine cannot be reaped, waitErr is errAgentReapBudget and the reaping
// goroutine stays parked until the operating system releases the process.
//
// Order matters: every signal is sent by agentTerminate while the leader is
// still unreaped, which is what makes its group number ours. Wait is called
// only afterwards, here and nowhere else for an agent's command, and nothing
// is signalled once it has been called.
func terminateAndReap(command *exec.Cmd, pipes []io.Closer) (killErr error, waitErr error) {
	killErr = agentTerminate(command)
	for _, pipe := range pipes {
		_ = pipe.Close()
	}
	waited := make(chan error, 1)
	go func() { waited <- command.Wait() }()
	budget := time.NewTimer(agentReapBudget)
	defer budget.Stop()
	select {
	case waitErr = <-waited:
	case <-budget.C:
		return killErr, errAgentReapBudget
	}
	return killErr, waitErr
}

// joinTransport waits, within agentTransportBudget, for the goroutine that owns
// the pipe I/O of one exchange. A goroutine that does not end is reported with
// the terminal error instead of being waited for.
func joinTransport(failure error, done <-chan agentReadResult) error {
	budget := time.NewTimer(agentTransportBudget)
	defer budget.Stop()
	select {
	case <-done:
		return failure
	case <-budget.C:
		return errors.Join(failure, NewInstanceExecutionError("Agent Tools transport did not stop within its budget", nil, nil))
	}
}

func (a *AgentContext) crashMessage() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.crashMessageLocked()
}

// shutdown is the single cleanup routine behind every terminal path: protocol
// and transport failures, deadlines, caller cancellation, Cancel and Close.
//
// Exactly one caller owns the cleanup of the process. Every other caller,
// concurrent or later, waits within a budget for that owner and then reports
// the owner's stored result: a failed cleanup stays failed for everybody, and
// nobody reports success before the cleanup is confirmed. A caller whose
// budget runs out first gets a typed "still pending" error, never nil. Once
// shutdown has started the session cannot become live again. a.mu is held
// only to claim ownership; killing, reaping and file removal run unlocked.
func (a *AgentContext) shutdown() error {
	a.mu.Lock()
	owner := !a.shutdownStarted
	a.shutdownStarted = true
	a.closed = true
	command := a.command
	a.command = nil
	a.mu.Unlock()
	if !owner {
		budget := time.NewTimer(agentReapBudget + agentTransportBudget + 3*time.Second)
		defer budget.Stop()
		select {
		case <-a.shutdownDone:
			return a.cleanupResult()
		case <-budget.C:
			return NewInstanceExecutionError("Agent Tools cleanup is still pending", nil, nil)
		}
	}
	processFailure := reapAgentProcess(command, a.pipes, a.stderrDone)
	a.mu.Lock()
	a.processFailure = processFailure
	a.mu.Unlock()
	close(a.shutdownDone)
	return a.cleanupResult()
}

// cleanupResult is what every caller of a finished shutdown reports: the
// owner's stored process cleanup failure, joined with the state of the policy
// directory. Removing the policy directory is retried on each call because it
// is a plain file operation that can be confirmed later; a failed process
// cleanup is never retried and never forgotten.
func (a *AgentContext) cleanupResult() error {
	a.mu.Lock()
	processFailure := a.processFailure
	a.mu.Unlock()
	policyFailure := a.removePolicy()
	if processFailure != nil && policyFailure != nil {
		return errors.Join(processFailure, policyFailure)
	}
	if processFailure != nil {
		return processFailure
	}
	return policyFailure
}

// reapAgentProcess ends and reaps the one process this context started and
// reports every failure. It runs once, in the shutdown owner.
func reapAgentProcess(command *exec.Cmd, pipes []io.Closer, stderrDone <-chan struct{}) error {
	var failures []error
	if command != nil {
		killErr, waitErr := terminateAndReap(command, pipes)
		var exitErr *exec.ExitError
		if killErr != nil {
			failures = append(failures, NewInstanceExecutionError("Agent Tools process tree could not be terminated", nil, nil))
		}
		if errors.Is(waitErr, errAgentReapBudget) {
			failures = append(failures, NewInstanceExecutionError("Agent Tools process could not be reaped within its budget", nil, nil))
		} else if killErr == nil && waitErr != nil && !errors.As(waitErr, &exitErr) {
			failures = append(failures, NewInstanceExecutionError("Agent Tools process could not be reaped", nil, nil))
		}
		if stderrDone != nil {
			select {
			case <-stderrDone:
			case <-time.After(2 * time.Second):
			}
		}
	}
	if len(failures) == 1 {
		return failures[0]
	}
	return errors.Join(failures...)
}

func agentErrorFromWire(code, message string) error {
	switch code {
	case "permission_denied":
		return NewAgentPermissionError(message)
	case "unsupported_capability":
		return NewUnsupportedCapabilityError(message)
	case "invalid_request", "invalid_state", "unsupported_interface":
		return NewInstanceProtocolError(message)
	default:
		return NewInstanceExecutionError(message, nil, nil)
	}
}

// The (interface, schema, transport, engine) pairs a hello is accepted for:
// the rows of contracts/agent-tools-compatibility-v1.json, mirrored here and
// held equal to it by scripts/check_language_versions.py. A pair that is not
// a row is refused, whatever the Engine says about itself.
// agent-tools-compatibility: begin
var admittedInstancePairs = []struct {
	iface     string
	schema    int
	transport int
	engine    string
}{
	{"1.0.0", 1, 1, "3.11.0"},
	{"1.0.0", 1, 1, "3.11.3"},
	{"1.0.0", 1, 1, "3.11.4"},
	{"1.0.0", 1, 1, "3.11.5"},
	{"1.0.0", 1, 1, "3.12.0"},
	{"1.0.0", 1, 1, "3.12.1"},
}

// agent-tools-compatibility: end

func admittedInstancePair(engine string) bool {
	for _, pair := range admittedInstancePairs {
		if pair.iface == AgentToolsInterfaceVersion && pair.schema == AgentToolsSchemaVersion && pair.transport == AgentToolsTransportVersion && pair.engine == engine {
			return true
		}
	}
	return false
}

// helloInteger reports whether a hello version is written as the JSON integer
// expected: digits only, as the Engine sent them. The general number reader
// would also take 1.0 and 1e0 for 1; a version field is not such a number.
func helloInteger(value any, expected int) bool {
	number, ok := value.(json.Number)
	return ok && number.String() == strconv.Itoa(expected)
}

func (a *AgentContext) acceptHello(result map[string]any) error {
	expected := map[string]bool{"agent_tools_interface_version": true, "allow_exec": true, "allow_write": true, "capabilities": true, "engine_version": true, "schema_version": true, "transport_version": true}
	if err := exactObjectKeys(result, expected, "Agent Tools hello"); err != nil {
		return NewInstanceProtocolError("Agent Tools hello is incompatible")
	}
	if result["agent_tools_interface_version"] != AgentToolsInterfaceVersion || result["allow_exec"] != a.Permissions.Execute || result["allow_write"] != a.Permissions.Write {
		return NewInstanceProtocolError("Agent Tools hello is incompatible")
	}
	if !helloInteger(result["schema_version"], AgentToolsSchemaVersion) || !helloInteger(result["transport_version"], AgentToolsTransportVersion) {
		return NewInstanceProtocolError("Agent Tools hello is incompatible")
	}
	if engine, ok := result["engine_version"].(string); !ok || !admittedInstancePair(engine) {
		return NewInstanceProtocolError("Agent Tools instance pair is unsupported")
	}
	values, ok := result["capabilities"].([]any)
	if !ok {
		return NewInstanceProtocolError("Agent Tools capabilities are invalid")
	}
	capabilities := make([]string, len(values))
	for i, value := range values {
		capabilities[i], ok = value.(string)
		if !ok {
			return NewInstanceProtocolError("Agent Tools capabilities are invalid")
		}
	}
	sorted := append([]string(nil), capabilities...)
	sort.Strings(sorted)
	if !equalStrings(capabilities, uniqueStrings(sorted)) {
		return NewInstanceProtocolError("Agent Tools capabilities are not canonical")
	}
	expectedCapabilities := append([]string(nil), agentReadTools...)
	if a.Permissions.Write {
		expectedCapabilities = append(expectedCapabilities, agentWriteTools...)
	}
	if a.Permissions.Execute {
		expectedCapabilities = append(expectedCapabilities, agentExecuteTools...)
	}
	if a.gitlabSource != nil {
		expectedCapabilities = append(expectedCapabilities, "ctx_provider")
	}
	sort.Strings(expectedCapabilities)
	if !equalStrings(capabilities, uniqueStrings(expectedCapabilities)) {
		return NewInstanceProtocolError("Agent Tools capabilities do not match policy")
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
		return "Agent Tools instance exited"
	}
	if len(detail) > 4096 {
		detail = detail[:4096]
	}
	return "Agent Tools instance exited: " + detail
}

func (a *AgentContext) callToolContext(ctx context.Context, tool string, arguments map[string]any, timeout time.Duration) (*ToolResult, error) {
	a.mu.Lock()
	capable := contains(a.capabilities, tool)
	a.mu.Unlock()
	if !capable {
		return nil, NewUnsupportedCapabilityError("LeanCTX instance did not negotiate capability: " + tool)
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
		return nil, a.terminalError(err)
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
		return nil, NewInstanceProtocolError("Agent Tools text is invalid")
	}
	var mode *string
	if value["mode"] != nil {
		modeValue, ok := value["mode"].(string)
		if !ok {
			return nil, NewInstanceProtocolError("Agent Tools mode is invalid")
		}
		mode = &modeValue
	}
	original, err := protocolInt(value["original_tokens"], "Agent Tools original_tokens")
	if err != nil || original < 0 {
		return nil, NewInstanceProtocolError("Agent Tools token metrics are invalid")
	}
	output, err := protocolInt(value["output_tokens"], "Agent Tools output_tokens")
	if err != nil || output < 0 {
		return nil, NewInstanceProtocolError("Agent Tools token metrics are invalid")
	}
	saved, err := protocolInt(value["saved_tokens"], "Agent Tools saved_tokens")
	if err != nil || saved < 0 || output+saved != original {
		return nil, NewInstanceProtocolError("Agent Tools token metrics are invalid")
	}
	changed, ok := value["changed"].(bool)
	if !ok {
		return nil, NewInstanceProtocolError("Agent Tools status metadata is invalid")
	}
	blocksValue, ok := value["content_blocks"].([]any)
	if !ok {
		return nil, NewInstanceProtocolError("Agent Tools content blocks are invalid")
	}
	blocks := make([]map[string]any, len(blocksValue))
	for i, blockValue := range blocksValue {
		block, ok := blockValue.(map[string]any)
		if !ok {
			return nil, NewInstanceProtocolError("Agent Tools content blocks are invalid")
		}
		blocks[i], err = cloneMap(block)
		if err != nil {
			return nil, NewInstanceProtocolError("Agent Tools content blocks are invalid")
		}
	}
	var shell map[string]any
	if value["shell"] != nil {
		item, ok := value["shell"].(map[string]any)
		if !ok {
			return nil, NewInstanceProtocolError("Agent Tools shell metadata is invalid")
		}
		shell, err = cloneMap(item)
		if err != nil {
			return nil, NewInstanceProtocolError("Agent Tools shell metadata is invalid")
		}
	}
	return &ToolResult{Tool: tool, Text: text, ContentBlocks: blocks, OriginalTokens: original, OutputTokens: output, SavedTokens: saved, Mode: mode, Changed: changed, Shell: shell}, nil
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
	realRoot, err := filepath.EvalSymlinks(a.ProjectRoot)
	if err != nil {
		return nil, NewAgentPermissionError("project root is unavailable")
	}
	candidate := option.CWD
	if !filepath.IsAbs(candidate) {
		candidate = filepath.Join(realRoot, candidate)
	}
	realCWD, err := filepath.EvalSymlinks(candidate)
	if err != nil || !containedPath(realCWD, realRoot) {
		return nil, NewAgentPermissionError("cwd escapes project root")
	}
	info, err := os.Stat(realCWD)
	if err != nil || !info.IsDir() {
		return nil, NewAgentPermissionError("cwd is not an existing directory")
	}
	relativeCWD, err := filepath.Rel(realRoot, realCWD)
	if err != nil || relativeCWD == ".." || strings.HasPrefix(relativeCWD, ".."+string(filepath.Separator)) {
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
	request := map[string]any{"argv": append([]string(nil), argv...), "cwd": filepath.ToSlash(relativeCWD), "env": environment, "timeout_ms": int64(timeout / time.Millisecond)}
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
		return a.terminate()
	}
	// The close request is a courtesy to the Engine, as in the other five
	// bindings: whether it was answered does not decide the outcome. Close
	// reports whether the session was cleaned up, which is what terminate
	// confirms. An unanswered request has already ended the session through
	// the terminal path, and its cleanup failure, if any, is the stored result.
	_, _ = a.exchange(context.Background(), map[string]any{"op": "close"}, a.Timeout)
	return a.terminate()
}

func (a *AgentContext) Cancel() error {
	return a.terminate()
}

func (a *AgentContext) terminate() error {
	return a.shutdown()
}

// Reconnect always creates a fresh process and reuses the original policy
// snapshot. A closed context cannot be made live again.
func (a *AgentContext) Reconnect(ctx context.Context) (*AgentContext, error) {
	if err := a.Cancel(); err != nil {
		return nil, err
	}
	return OpenAgentContext(ctx, a.ProjectRoot, AgentContextOptions{Task: a.Task, Permissions: a.Permissions, ExecutionPolicy: a.ExecutionPolicy, InstanceBinary: a.InstanceBinary, Timeout: a.Timeout, GitLabSource: a.gitlabSource})
}

func resolveExecutable(value string) (string, error) {
	if value == "" {
		return "", NewInstanceUnavailable("configured instance binary is unavailable")
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
			return "", NewInstanceUnavailable("configured instance binary is unavailable")
		}
	} else {
		var err error
		candidate, err = filepath.Abs(candidate)
		if err != nil {
			return "", NewInstanceUnavailable("configured instance binary is unavailable")
		}
	}
	resolved, err := filepath.EvalSymlinks(candidate)
	if err != nil || !isExecutableRegular(resolved) {
		return "", NewInstanceUnavailable("configured instance binary is unavailable")
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
	selectedGitLab, err := copyGitLabSource(option.GitLabSource)
	if err != nil {
		return nil, err
	}
	option.GitLabSource = selectedGitLab
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
		return nil, NewInstanceUnavailable("AsyncAgentContext is not open")
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

func (a *AsyncAgentContext) SessionContainment() (SessionContainment, error) {
	current, err := a.context()
	if err != nil {
		return SessionContainmentUnavailable, err
	}
	return current.SessionContainment(), nil
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
