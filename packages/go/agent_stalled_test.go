//go:build !windows

package leanctx

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

// stalledAgentScript answers the hello and then never reads stdin again. It
// keeps one descendant in its process group and records both identifiers.
func stalledAgentScript(t *testing.T, leaderPath, descendantPath string) string {
	t.Helper()
	return standInAgentScript(t, leaderPath, descendantPath, "/bin/sleep 30\n")
}

// standInAgentScript answers the hello, keeps one descendant in its process
// group, records both identifiers and then runs afterHello.
func standInAgentScript(t *testing.T, leaderPath, descendantPath, afterHello string) string {
	t.Helper()
	hello := `{"agent_tools_interface_version":"1.0.0","allow_exec":false,"allow_write":false,"capabilities":["ctx_compose","ctx_glob","ctx_read","ctx_search","ctx_symbol","ctx_tree"],"engine_version":"3.11.0","schema_version":1,"transport_version":1}`
	return writeTestExecutable(t, "#!/bin/sh\n"+
		"IFS= read -r line\n"+
		"id=$(/usr/bin/printf '%s' \"$line\" | /usr/bin/sed -n 's/.*\"id\":\"\\([^\"]*\\)\".*/\\1/p')\n"+
		"/usr/bin/printf '%s\\n' \"$$\" > "+shellQuote(leaderPath)+"\n"+
		"/bin/sleep 30 >/dev/null 2>&1 &\n"+
		"/usr/bin/printf '%s\\n' \"$!\" > "+shellQuote(descendantPath)+"\n"+
		"/usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":%s}\\n' \"$id\" '"+hello+"'\n"+
		afterHello)
}

func policyDirectories(t *testing.T, root string) []string {
	t.Helper()
	entries, err := filepath.Glob(filepath.Join(root, ".leanctx-agent-*"))
	if err != nil {
		t.Fatal(err)
	}
	return entries
}

// TestAgentContextTerminalConditionsCleanUp drives every terminal path that a
// misbehaving Engine can trigger and checks what each must leave behind: the
// typed error, a terminal context, no process-group member, no policy directory.
func TestAgentContextTerminalConditionsCleanUp(t *testing.T) {
	invalidResult := "while IFS= read -r line; do\n" +
		"  id=$(/usr/bin/printf '%s' \"$line\" | /usr/bin/sed -n 's/.*\"id\":\"\\([^\"]*\\)\".*/\\1/p')\n" +
		"  /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":{\"extra\":true}}\\n' \"$id\"\n" +
		"done\n"
	cases := []struct {
		name       string
		afterHello string
		check      func(error) bool
	}{
		{"closed stdout with live instance", "exec 1>&-\n/bin/sleep 30\n", func(err error) bool { var target *InstanceCrashed; return errors.As(err, &target) }},
		{"LeanCTX instance exit with surviving descendant", "IFS= read -r line\nexit 7\n", func(err error) bool { var target *InstanceCrashed; return errors.As(err, &target) }},
		{"invalid ToolResult in valid envelope", invalidResult, func(err error) bool { var target *InstanceProtocolError; return errors.As(err, &target) }},
		{"unexpected response id", "IFS= read -r line\n/usr/bin/printf '{\"id\":\"unexpected\",\"ok\":true,\"result\":{}}\\n'\n/bin/sleep 30\n", func(err error) bool { var target *InstanceProtocolError; return errors.As(err, &target) }},
		{"missing response", "/bin/sleep 30\n", func(err error) bool { var target *InstanceTimeout; return errors.As(err, &target) }},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			leaderPath := filepath.Join(root, "leader.pid")
			descendantPath := filepath.Join(root, "descendant.pid")
			options := agentOptions(standInAgentScript(t, leaderPath, descendantPath, test.afterHello), AgentPermissions{})
			options.Timeout = 700 * time.Millisecond
			client, err := OpenAgentContext(context.Background(), root, options)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = client.Cancel() })
			leader := readPIDFile(t, leaderPath)
			descendant := readPIDFile(t, descendantPath)
			if _, err := client.Read("file"); !test.check(err) {
				t.Fatalf("terminal error = %T: %v", err, err)
			}
			requireTreeGone(t, leader, descendant)
			if entries := policyDirectories(t, root); len(entries) != 0 {
				t.Fatalf("policy directories left behind: %v", entries)
			}
			var crashed *InstanceCrashed
			if _, err := client.Read("file"); !errors.As(err, &crashed) {
				t.Fatalf("call after terminal condition = %T: %v", err, err)
			}
			if err := client.Close(); err != nil {
				t.Fatal(err)
			}
			if err := client.Close(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

// TestAgentContextWaitingCallHonoursItsOwnContext holds one call inside the
// stand-in Engine and proves that a second call gives up on its own cancelled
// or expired context without waiting for the first, without ending the
// session, and that the same agent keeps serving calls afterwards.
func TestAgentContextWaitingCallHonoursItsOwnContext(t *testing.T) {
	root := t.TempDir()
	result := `{"changed":false,"content_blocks":[],"mode":"auto","original_tokens":100,"output_tokens":25,"saved_tokens":75,"shell":null,"text":"ok"}`
	slowCalls := "while IFS= read -r line; do\n" +
		"  id=$(/usr/bin/printf '%s' \"$line\" | /usr/bin/sed -n 's/.*\"id\":\"\\([^\"]*\\)\".*/\\1/p')\n" +
		"  case \"$line\" in\n" +
		"    *'\"op\":\"close\"'*) /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":{}}\\n' \"$id\"; exit 0 ;;\n" +
		"    *) /bin/sleep 1; /usr/bin/printf '{\"id\":\"%s\",\"ok\":true,\"result\":%s}\\n' \"$id\" '" + result + "' ;;\n" +
		"  esac\n" +
		"done\n"
	options := agentOptions(standInAgentScript(t, filepath.Join(root, "leader.pid"), filepath.Join(root, "descendant.pid"), slowCalls), AgentPermissions{})
	options.Timeout = 10 * time.Second
	client, err := OpenAgentContext(context.Background(), root, options)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Cancel() })

	firstDone := make(chan error, 1)
	go func() {
		_, err := client.Read("file")
		firstDone <- err
	}()
	time.Sleep(200 * time.Millisecond)

	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	started := time.Now()
	if _, err := client.ReadContext(cancelled, "file"); !errors.Is(err, context.Canceled) {
		t.Fatalf("waiting call with a cancelled context = %T: %v", err, err)
	}
	expiring, release := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer release()
	var timeout *InstanceTimeout
	if _, err := client.ReadContext(expiring, "file"); !errors.As(err, &timeout) {
		t.Fatalf("waiting call with an expired context = %T: %v", err, err)
	}
	if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
		t.Fatalf("waiting calls returned after %s; they waited for the running call", elapsed)
	}
	select {
	case err := <-firstDone:
		t.Fatalf("the running call ended early: %v", err)
	default:
	}
	// Neither waiting call touched the wire: the running call and the session are intact.
	select {
	case err := <-firstDone:
		if err != nil {
			t.Fatalf("running call = %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("running call did not complete")
	}
	if result, err := client.Read("file"); err != nil || result.Text != "ok" {
		t.Fatalf("sequential call afterwards = %#v, %v", result, err)
	}
	if metrics := client.Metrics(); metrics.ToolCalls != 2 {
		t.Fatalf("tool calls = %d, want the two that reached the instance", metrics.ToolCalls)
	}
	if err := client.Close(); err != nil {
		t.Fatal(err)
	}
}

// TestAgentContextCleanupIsBoundedWhenTheInstanceCannotBeTerminated replaces the
// kill with a no-op, which is the closest a test can get to a child that
// survives termination: SIGKILL cannot be ignored by a process we may create.
// It proves that Cancel and the blocked call return within their budgets, that
// the failure is reported, and that the transport goroutine ended because the
// context closed its own pipes. The surviving stand-in is this test's own
// child and is killed by the test afterwards.
func TestAgentContextCleanupIsBoundedWhenTheInstanceCannotBeTerminated(t *testing.T) {
	client, leader, descendant := openStalled(t, 30*time.Second)
	previousTerminate, previousReap, previousTransport := agentTerminate, agentReapBudget, agentTransportBudget
	agentTerminate = func(*exec.Cmd) error { return nil }
	agentReapBudget, agentTransportBudget = 300*time.Millisecond, 300*time.Millisecond
	restore := func() {
		agentTerminate, agentReapBudget, agentTransportBudget = previousTerminate, previousReap, previousTransport
	}
	t.Cleanup(func() {
		restore()
		_ = syscall.Kill(-leader, syscall.SIGKILL)
	})

	callErr := make(chan error, 1)
	go func() {
		_, err := client.Call("ctx_read", map[string]any{})
		callErr <- err
	}()
	time.Sleep(200 * time.Millisecond)
	const reapFailure = "could not be reaped within its budget"
	var execution *InstanceExecutionError
	started := time.Now()
	ownerErr := make(chan error, 1)
	go func() { ownerErr <- client.Cancel() }()
	// A parallel Close while the owner is still reaping must not report
	// success, must not return before the owner can have finished, and must
	// return within a bound.
	time.Sleep(100 * time.Millisecond)
	parallelErr := client.Close()
	parallelElapsed := time.Since(started)
	if parallelElapsed < agentReapBudget {
		t.Fatalf("parallel Close returned after %s, before the owner's %s reap budget", parallelElapsed, agentReapBudget)
	}
	if parallelElapsed > 4*time.Second {
		t.Fatalf("parallel Close returned after %s", parallelElapsed)
	}
	if !errors.As(parallelErr, &execution) || !strings.Contains(parallelErr.Error(), reapFailure) {
		t.Fatalf("parallel Close during a failing cleanup = %T: %v", parallelErr, parallelErr)
	}
	var err error
	select {
	case err = <-ownerErr:
	case <-time.After(2 * time.Second):
		t.Fatal("the owning Cancel did not return although a waiter already saw its result")
	}
	if !errors.As(err, &execution) || !strings.Contains(err.Error(), reapFailure) {
		t.Fatalf("unreapable instance = %T: %v", err, err)
	}
	// The failure is stored: every later Cancel and Close reports it, never nil.
	for attempt := 0; attempt < 2; attempt++ {
		for name, again := range map[string]func() error{"Cancel": client.Cancel, "Close": client.Close} {
			repeated := again()
			if !errors.As(repeated, &execution) || !strings.Contains(repeated.Error(), reapFailure) {
				t.Fatalf("repeated %s after a failed cleanup = %T: %v", name, repeated, repeated)
			}
		}
	}
	select {
	case err := <-callErr:
		var crashed *InstanceCrashed
		if !errors.As(err, &crashed) {
			t.Fatalf("blocked call = %T: %v", err, err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("blocked call did not return: the transport goroutine still holds the pipes")
	}
	if !processAlive(leader) {
		t.Fatal("the stand-in was expected to survive the no-op termination")
	}
	var crashed *InstanceCrashed
	if _, err := client.Call("ctx_read", map[string]any{}); !errors.As(err, &crashed) {
		t.Fatalf("call after failed cleanup = %T: %v", err, err)
	}

	// End the test's own child for real and prove nothing is left.
	restore()
	if err := syscall.Kill(-leader, syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	requireTreeGone(t, leader, descendant)
}

// TestAgentContextShutdownHasOneOwnerAndSharedSuccess is the positive control:
// a normal shutdown called repeatedly and in parallel succeeds for every
// caller and terminates the process exactly once.
func TestAgentContextShutdownHasOneOwnerAndSharedSuccess(t *testing.T) {
	client, leader, descendant := openStalled(t, 30*time.Second)
	var terminations atomic.Int32
	previous := agentTerminate
	agentTerminate = func(command *exec.Cmd) error {
		terminations.Add(1)
		return previous(command)
	}
	t.Cleanup(func() { agentTerminate = previous })

	results := make(chan error, 8)
	for caller := 0; caller < 8; caller++ {
		shutdown := client.Cancel
		if caller%2 == 1 {
			shutdown = client.Close
		}
		go func() { results <- shutdown() }()
	}
	for caller := 0; caller < 8; caller++ {
		select {
		case err := <-results:
			if err != nil {
				t.Fatalf("parallel shutdown %d = %v", caller, err)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("a parallel shutdown did not return")
		}
	}
	for _, again := range []func() error{client.Cancel, client.Close, client.Cancel} {
		if err := again(); err != nil {
			t.Fatalf("repeated shutdown = %v", err)
		}
	}
	if count := terminations.Load(); count != 1 {
		t.Fatalf("the process was terminated %d times, want exactly once", count)
	}
	requireTreeGone(t, leader, descendant)
	var crashed *InstanceCrashed
	if _, err := client.Call("ctx_read", map[string]any{}); !errors.As(err, &crashed) {
		t.Fatalf("call after shutdown = %T: %v", err, err)
	}
}

// TestSubprocessInstanceClientCleanupIsBoundedAndReported is the one-shot
// client's negative control: with the kill replaced by a no-op the timed-out
// call still returns within the reap budget, keeps its typed timeout and
// carries the cleanup failure. The surviving stand-in is this test's own child.
func TestSubprocessInstanceClientCleanupIsBoundedAndReported(t *testing.T) {
	root := t.TempDir()
	pidPath := filepath.Join(root, "one-shot.pid")
	slow := writeTestExecutable(t, "#!/bin/sh\n/usr/bin/printf '%s\\n' \"$$\" > "+shellQuote(pidPath)+"\nexec /bin/sleep 30\n")
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: slow, Timeout: 200 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	previousTerminate, previousReap := agentTerminate, agentReapBudget
	agentTerminate = func(*exec.Cmd) error { return nil }
	agentReapBudget = 300 * time.Millisecond
	restore := func() { agentTerminate, agentReapBudget = previousTerminate, previousReap }
	t.Cleanup(restore)

	started := time.Now()
	_, err = client.ContextView(testPlanForRoot(t, root))
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Fatalf("one-shot call returned after %s although its instance could not be reaped", elapsed)
	}
	var timeout *InstanceTimeout
	var execution *InstanceExecutionError
	if !errors.As(err, &timeout) || !errors.As(err, &execution) || !strings.Contains(err.Error(), "could not be reaped within its budget") {
		t.Fatalf("unreapable one-shot instance = %T: %v", err, err)
	}
	survivor := readPIDFile(t, pidPath)
	if !processAlive(survivor) {
		t.Fatal("the stand-in was expected to survive the no-op termination")
	}
	restore()
	if err := syscall.Kill(-survivor, syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for processAlive(survivor) && len(groupMembers(t, survivor)) > 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if members := groupMembers(t, survivor); len(members) != 0 {
		t.Fatalf("one-shot stand-in survived: %v", members)
	}

	// Positive control: a real kill within the budget leaves only the typed timeout.
	_, err = client.ContextView(testPlanForRoot(t, root))
	if !errors.As(err, &timeout) || errors.As(err, &execution) {
		t.Fatalf("reapable one-shot instance = %T: %v", err, err)
	}
}

func TestAgentContextFailedStartupRemovesItsPolicyDirectory(t *testing.T) {
	root := t.TempDir()
	silent := writeTestExecutable(t, "#!/bin/sh\n/bin/sleep 30\n")
	options := agentOptions(silent, AgentPermissions{})
	options.Timeout = 300 * time.Millisecond
	client, err := OpenAgentContext(context.Background(), root, options)
	var timeout *InstanceTimeout
	if client != nil || !errors.As(err, &timeout) {
		t.Fatalf("silent instance = %#v, %T: %v", client, err, err)
	}
	if entries := policyDirectories(t, root); len(entries) != 0 {
		t.Fatalf("failed startup left policy directories behind: %v", entries)
	}
}

func TestAgentContextPolicyCleanupIsExactIdempotentAndReported(t *testing.T) {
	root := t.TempDir()
	client, err := OpenAgentContext(context.Background(), root, agentOptions(fakeAgentScript(t, "ok"), AgentPermissions{}))
	if err != nil {
		t.Fatal(err)
	}
	if entries := policyDirectories(t, client.ProjectRoot); len(entries) != 0 {
		t.Fatalf("policy directory outlived the hello: %v", entries)
	}

	// A directory this context owns but cannot remove: the failure is reported,
	// the path stays recorded, and a later attempt completes the cleanup.
	owned := filepath.Join(client.ProjectRoot, ".leanctx-agent-undeletable")
	if err := os.Mkdir(owned, 0700); err != nil {
		t.Fatal(err)
	}
	blocker := filepath.Join(owned, "not-created-by-the-sdk")
	if err := os.WriteFile(blocker, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	client.mu.Lock()
	client.policyDir = owned
	client.mu.Unlock()
	var execution *InstanceExecutionError
	if err := client.Close(); !errors.As(err, &execution) || !strings.Contains(err.Error(), "policy could not be removed") {
		t.Fatalf("undeletable policy directory = %T: %v", err, err)
	}
	if _, err := os.Stat(blocker); err != nil {
		t.Fatalf("cleanup removed a file it did not create: %v", err)
	}
	if err := client.Cancel(); !errors.As(err, &execution) {
		t.Fatalf("repeated cleanup hid the failure: %T: %v", err, err)
	}
	if err := os.Remove(blocker); err != nil {
		t.Fatal(err)
	}
	if err := client.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(owned); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("policy directory still present: %v", err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("double Close = %v", err)
	}

	// A path this context did not create is refused and left untouched.
	foreign := t.TempDir()
	client.mu.Lock()
	client.policyDir = foreign
	client.mu.Unlock()
	if err := client.Close(); !errors.As(err, &execution) || !strings.Contains(err.Error(), "not owned") {
		t.Fatalf("foreign policy path = %T: %v", err, err)
	}
	if info, err := os.Stat(foreign); err != nil || !info.IsDir() {
		t.Fatalf("foreign directory was touched: %v", err)
	}
	client.mu.Lock()
	client.policyDir = ""
	client.mu.Unlock()
}

func readPIDFile(t *testing.T, path string) int {
	t.Helper()
	for attempt := 0; attempt < 200; attempt++ {
		if raw, err := os.ReadFile(path); err == nil {
			if pid, err := strconv.Atoi(strings.TrimSpace(string(raw))); err == nil && pid > 0 {
				return pid
			}
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("stand-in instance did not publish %s", path)
	return 0
}

func processAlive(pid int) bool {
	return syscall.Kill(pid, 0) == nil
}

// groupMembers lists the process table and returns the members of group.
func groupMembers(t *testing.T, group int) []int {
	t.Helper()
	output, err := exec.Command("ps", "-A", "-o", "pid=", "-o", "pgid=").Output()
	if err != nil {
		t.Fatalf("process table: %v", err)
	}
	var members []int
	for _, line := range strings.Split(string(output), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		pid, pidErr := strconv.Atoi(fields[0])
		pgid, groupErr := strconv.Atoi(fields[1])
		if pidErr == nil && groupErr == nil && pgid == group {
			members = append(members, pid)
		}
	}
	return members
}

func requireTreeGone(t *testing.T, leader, descendant int) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if !processAlive(leader) && !processAlive(descendant) && len(groupMembers(t, leader)) == 0 {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("process tree survived: leader alive=%v descendant alive=%v group=%v", processAlive(leader), processAlive(descendant), groupMembers(t, leader))
}

// oversizedArguments exceeds any OS pipe buffer and stays inside the 1 MiB request bound.
func oversizedArguments() map[string]any {
	return map[string]any{"padding": strings.Repeat("x", 600_000)}
}

func openStalled(t *testing.T, timeout time.Duration) (*AgentContext, int, int) {
	t.Helper()
	root := t.TempDir()
	leaderPath := filepath.Join(root, "leader.pid")
	descendantPath := filepath.Join(root, "descendant.pid")
	options := agentOptions(stalledAgentScript(t, leaderPath, descendantPath), AgentPermissions{})
	options.Timeout = timeout
	client, err := OpenAgentContext(context.Background(), root, options)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Cancel() })
	leader := readPIDFile(t, leaderPath)
	descendant := readPIDFile(t, descendantPath)
	found := false
	for _, member := range groupMembers(t, leader) {
		found = found || member == descendant
	}
	if !found {
		t.Fatalf("descendant %d is not in the instance process group %d", descendant, leader)
	}
	return client, leader, descendant
}

func TestAgentContextStalledWriteIsBoundedByTheDeadline(t *testing.T) {
	client, leader, descendant := openStalled(t, 700*time.Millisecond)
	started := time.Now()
	_, err := client.Call("ctx_read", oversizedArguments())
	var timeout *InstanceTimeout
	if !errors.As(err, &timeout) {
		t.Fatalf("stalled write error = %T: %v", err, err)
	}
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Fatalf("stalled write returned after %s", elapsed)
	}
	requireTreeGone(t, leader, descendant)
	var crashed *InstanceCrashed
	if _, err := client.Call("ctx_read", map[string]any{}); !errors.As(err, &crashed) {
		t.Fatalf("call after timeout = %T: %v", err, err)
	}
	if err := client.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestAgentContextCancelInterruptsStalledWriteAndPendingResponse(t *testing.T) {
	for name, arguments := range map[string]map[string]any{"stalled write": oversizedArguments(), "pending response": {}} {
		t.Run(name, func(t *testing.T) {
			// The deadline is far away: only Cancel may end this call.
			client, leader, descendant := openStalled(t, 30*time.Second)
			callErr := make(chan error, 1)
			go func() {
				_, err := client.Call("ctx_read", arguments)
				callErr <- err
			}()
			time.Sleep(300 * time.Millisecond)
			started := time.Now()
			if err := client.Cancel(); err != nil {
				t.Fatal(err)
			}
			if elapsed := time.Since(started); elapsed > 2*time.Second {
				t.Fatalf("Cancel returned after %s", elapsed)
			}
			select {
			case err := <-callErr:
				var crashed *InstanceCrashed
				if !errors.As(err, &crashed) {
					t.Fatalf("cancelled call error = %T: %v", err, err)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("cancelled call is still blocked")
			}
			requireTreeGone(t, leader, descendant)
		})
	}
}

func TestAgentContextCallerContextInterruptsStalledWrite(t *testing.T) {
	client, leader, descendant := openStalled(t, 30*time.Second)
	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(300*time.Millisecond, cancel)
	started := time.Now()
	_, err := client.CallContext(ctx, "ctx_read", oversizedArguments())
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("context cancellation error = %T: %v", err, err)
	}
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Fatalf("cancelled call returned after %s", elapsed)
	}
	requireTreeGone(t, leader, descendant)
}
