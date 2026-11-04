//go:build !windows

package leanctx

// Group ownership at the last signal. A process group is addressed by a
// number that is ours only while its leader is unreaped. These tests record
// every group signal the package sends and prove, for the agent and for the
// one-shot client, that each one was sent while the leader was still in the
// process table and that none is sent once it has been reaped. They never
// rely on a foreign process being hit or spared: the proof is the record.

import (
	"context"
	"errors"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

type recordedGroupSignal struct {
	pgid           int
	leaderUnreaped bool
}

// inProcessTable reports whether pid is still in the process table, running
// or as a zombie. A reaped process is in neither state.
func inProcessTable(pid int) bool {
	output, _ := exec.Command("ps", "-o", "stat=", "-p", strconv.Itoa(pid)).Output()
	return strings.TrimSpace(string(output)) != ""
}

// recordGroupSignals replaces the package's group signal by one that looks at
// the process table first and then sends the real signal.
func recordGroupSignals(t *testing.T) func() []recordedGroupSignal {
	t.Helper()
	var mu sync.Mutex
	var seen []recordedGroupSignal
	previous := groupSignal
	groupSignal = func(pgid int, signal syscall.Signal) error {
		unreaped := inProcessTable(pgid)
		mu.Lock()
		seen = append(seen, recordedGroupSignal{pgid: pgid, leaderUnreaped: unreaped})
		mu.Unlock()
		return previous(pgid, signal)
	}
	t.Cleanup(func() { groupSignal = previous })
	return func() []recordedGroupSignal {
		mu.Lock()
		defer mu.Unlock()
		return append([]recordedGroupSignal(nil), seen...)
	}
}

func requireOwnedSignals(t *testing.T, signals []recordedGroupSignal, leader int) {
	t.Helper()
	if len(signals) == 0 {
		t.Fatal("no group signal was sent")
	}
	for index, signal := range signals {
		if signal.pgid != leader {
			t.Fatalf("signal %d went to group %d, not to the instance's group %d", index, signal.pgid, leader)
		}
		if !signal.leaderUnreaped {
			t.Fatalf("signal %d of %d was sent after the leader had been reaped", index, len(signals))
		}
	}
}

// Positive control, agent: the own group with leader and descendant is ended,
// every signal was sent while the leader was unreaped, and no later Cancel or
// Close sends anything.
func TestOwnershipAgentSignalsOnlyWhileLeaderIsUnreaped(t *testing.T) {
	signals := recordGroupSignals(t)
	client, leader, descendant := openStalled(t, 30*time.Second)
	if err := client.Cancel(); err != nil {
		t.Fatal(err)
	}
	requireTreeGone(t, leader, descendant)
	sent := signals()
	requireOwnedSignals(t, sent, leader)
	if inProcessTable(leader) {
		t.Fatal("the leader was not reaped")
	}
	for _, again := range []func() error{client.Cancel, client.Close, client.Cancel} {
		if err := again(); err != nil {
			t.Fatal(err)
		}
	}
	if after := signals(); len(after) != len(sent) {
		t.Fatalf("%d group signal(s) were sent after the reap", len(after)-len(sent))
	}
}

// The Engine exits by itself on close. On Darwin the group signal then meets a
// group whose only member is the unreaped leader (EPERM). That must be settled
// by looking, before the reap — never by a second signal after it.
func TestOwnershipGracefulExitIsSettledWithoutASignalAfterTheReap(t *testing.T) {
	signals := recordGroupSignals(t)
	client, err := OpenAgentContext(context.Background(), t.TempDir(), agentOptions(fakeAgentScript(t, "ok"), AgentPermissions{}))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Read("file"); err != nil {
		t.Fatal(err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("Close after a graceful instance exit = %v", err)
	}
	sent := signals()
	for index, signal := range sent {
		if !signal.leaderUnreaped {
			t.Fatalf("signal %d was sent after the leader had been reaped", index)
		}
	}
	if err := client.Close(); err != nil {
		t.Fatal(err)
	}
	if after := signals(); len(after) != len(sent) {
		t.Fatal("a group signal was sent after the reap")
	}
}

// Devalued ownership: a command that has been reaped is not signalled at all.
func TestOwnershipReapedCommandIsNeverSignalled(t *testing.T) {
	signals := recordGroupSignals(t)
	command := exec.Command("/bin/sh", "-c", "exit 0")
	configureProcessGroup(command)
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	if err := command.Wait(); err != nil {
		t.Fatal(err)
	}
	if err := terminateProcess(command); err != nil {
		t.Fatalf("terminateProcess on a reaped command = %v", err)
	}
	if killErr, _ := terminateAndReapForTest(command); killErr != nil {
		t.Fatalf("terminateAndReap on a reaped command = %v", killErr)
	}
	if sent := signals(); len(sent) != 0 {
		t.Fatalf("a reaped command was signalled: %+v", sent)
	}
}

func terminateAndReapForTest(command *exec.Cmd) (error, error) {
	// Wait on an already waited command returns an error at once; only the
	// kill result matters here.
	return terminateAndReap(command, nil)
}

// Leader exits first and leaves a child that holds the Engine's stdout. The
// agent sees no end of output, reaches its deadline and ends the group — by a
// number that is still pinned by the exited, unreaped leader.
func TestOwnershipAgentLeaderExitsFirstWithPipeHoldingChild(t *testing.T) {
	signals := recordGroupSignals(t)
	root := t.TempDir()
	leaderPath, childPath := filepath.Join(root, "leader.pid"), filepath.Join(root, "descendant.pid")
	holderPath := filepath.Join(root, "holder.pid")
	afterHello := "IFS= read -r line\n" +
		// Inherits stdout (holds the pipe) and ignores SIGTERM.
		"(trap '' TERM; exec /bin/sleep 30) &\n" +
		"/usr/bin/printf '%s\\n' \"$!\" > " + shellQuote(holderPath) + "\n" +
		"exit 7\n"
	options := agentOptions(standInAgentScript(t, leaderPath, childPath, afterHello), AgentPermissions{})
	options.Timeout = 700 * time.Millisecond
	client, err := OpenAgentContext(context.Background(), root, options)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Cancel() })
	leader := readPIDFile(t, leaderPath)
	if _, err := client.Read("file"); err == nil {
		t.Fatal("a call to an instance that exited unexpectedly succeeded")
	} else {
		var timeout *InstanceTimeout
		var crashed *InstanceCrashed
		if !errors.As(err, &timeout) && !errors.As(err, &crashed) {
			t.Fatalf("leader-exits-first error = %T: %v", err, err)
		}
	}
	holder := readPIDFile(t, holderPath)
	requireTreeGone(t, leader, holder)
	requireTreeGone(t, leader, readPIDFile(t, childPath))
	requireOwnedSignals(t, signals(), leader)
}

// One-shot client, leader exits first with a pipe-holding child: the call ends
// at its deadline with the typed timeout and the child is gone, signalled
// while the exited leader was still unreaped.
func TestOwnershipOneShotLeaderExitsFirstWithPipeHoldingChild(t *testing.T) {
	signals := recordGroupSignals(t)
	root := t.TempDir()
	leaderPath, holderPath := filepath.Join(root, "leader.pid"), filepath.Join(root, "holder.pid")
	engine := writeTestExecutable(t, "#!/bin/sh\n"+
		"/usr/bin/printf '%s\\n' \"$$\" > "+shellQuote(leaderPath)+"\n"+
		"(trap '' TERM; exec /bin/sleep 30) &\n"+
		"/usr/bin/printf '%s\\n' \"$!\" > "+shellQuote(holderPath)+"\n"+
		"exit 0\n")
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: engine, Timeout: 1500 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	_, err = client.ContextView(testPlanForRoot(t, root))
	var timeout *InstanceTimeout
	if !errors.As(err, &timeout) {
		t.Fatalf("one-shot with a pipe-holding child = %T: %v", err, err)
	}
	if elapsed := time.Since(started); elapsed > 4*time.Second {
		t.Fatalf("the call returned after %s", elapsed)
	}
	leader, holder := readPIDFile(t, leaderPath), readPIDFile(t, holderPath)
	requireTreeGone(t, leader, holder)
	requireOwnedSignals(t, signals(), leader)
}

// One-shot client, positive control: a timed-out Engine with a descendant is
// ended as a group while its leader is unreaped.
func TestOwnershipOneShotTimeoutEndsOwnGroup(t *testing.T) {
	signals := recordGroupSignals(t)
	root := t.TempDir()
	leaderPath, childPath := filepath.Join(root, "leader.pid"), filepath.Join(root, "child.pid")
	engine := writeTestExecutable(t, "#!/bin/sh\n"+
		"/usr/bin/printf '%s\\n' \"$$\" > "+shellQuote(leaderPath)+"\n"+
		"/bin/sleep 30 >/dev/null 2>&1 &\n"+
		"/usr/bin/printf '%s\\n' \"$!\" > "+shellQuote(childPath)+"\n"+
		"/bin/sleep 30\n")
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: engine, Timeout: 1500 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.ContextView(testPlanForRoot(t, root))
	var timeout *InstanceTimeout
	if !errors.As(err, &timeout) {
		t.Fatalf("one-shot timeout = %T: %v", err, err)
	}
	leader := readPIDFile(t, leaderPath)
	requireTreeGone(t, leader, readPIDFile(t, childPath))
	requireOwnedSignals(t, signals(), leader)
	if inProcessTable(leader) {
		t.Fatal("the leader was not reaped")
	}
}

// One-shot client, devalued ownership: the Engine closes both outputs and
// keeps running, so the client is already waiting for it when the deadline
// arrives. A waiter may reap at any moment; therefore nothing is signalled,
// and the outcome says so instead of claiming a termination.
func TestOwnershipOneShotSendsNoSignalOnceItWaits(t *testing.T) {
	signals := recordGroupSignals(t)
	root := t.TempDir()
	leaderPath := filepath.Join(root, "leader.pid")
	engine := writeTestExecutable(t, "#!/bin/sh\n"+
		"/usr/bin/printf '%s\\n' \"$$\" > "+shellQuote(leaderPath)+"\n"+
		"exec >&- 2>&-\n"+
		"exec /bin/sleep 30\n")
	client, err := NewSubprocessInstanceClient(SubprocessInstanceClientOptions{InstanceBinary: engine, Timeout: 1500 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	_, err = client.ContextView(testPlanForRoot(t, root))
	leader := readPIDFile(t, leaderPath)
	// The survivor is this test's own child; it is ended by its handle-free
	// pid only here, in the test, where nothing else waits for it.
	t.Cleanup(func() { _ = syscall.Kill(leader, syscall.SIGKILL) })
	var timeout *InstanceTimeout
	var execution *InstanceExecutionError
	if !errors.As(err, &timeout) || !errors.As(err, &execution) || !strings.Contains(err.Error(), "termination is unconfirmed") {
		t.Fatalf("awaited one-shot at its deadline = %T: %v", err, err)
	}
	if elapsed := time.Since(started); elapsed > 5*time.Second {
		t.Fatalf("the call returned after %s", elapsed)
	}
	if sent := signals(); len(sent) != 0 {
		t.Fatalf("a signal was sent while the command was being waited for: %+v", sent)
	}
	if !processAlive(leader) {
		t.Fatal("the stand-in was expected to be left running")
	}
}
