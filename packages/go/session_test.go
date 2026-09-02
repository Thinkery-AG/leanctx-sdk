package leanctx

import (
	"errors"
	"testing"
)

type fakeEngine struct {
	view         *ContextView
	contextCalls int
	recover      *RecoveredSource
	recoverErr   error
	contextErr   error
}

func (e *fakeEngine) ContextView(plan *ContextPlan) (*ContextView, error) {
	e.contextCalls++
	if e.contextErr != nil {
		return e.view, e.contextErr
	}
	return e.view, nil
}

func (e *fakeEngine) Recover(projectRoot, path, recoveryRef, sourceRef, sourceDigest string) (*RecoveredSource, error) {
	if e.recoverErr != nil {
		return nil, e.recoverErr
	}
	return e.recover, nil
}

func TestSessionLifecycleIsIdempotentAndTruthful(t *testing.T) {
	root := t.TempDir()
	source := testSource(t, root)
	view := testFixtureView(t, *source)
	fake := &fakeEngine{view: view}
	session, err := NewContextSession("inspect", SessionOptions{ProjectRoot: root, SessionID: "session-fixed", TaskID: "task-fixed", Engine: fake})
	if err != nil {
		t.Fatal(err)
	}
	plan, err := session.Plan(source)
	if err != nil {
		t.Fatal(err)
	}
	if repeated, err := session.PlanFor(source); err != nil || repeated != plan {
		t.Fatalf("repeated plan = %p, %v; want %p", repeated, err, plan)
	}
	prepared, err := session.Prepare(nil)
	if err != nil || prepared != view {
		t.Fatalf("prepare = %p, %v", prepared, err)
	}
	if _, err := session.Prepare(nil); err != nil || fake.contextCalls != 1 {
		t.Fatalf("idempotent prepare = %v, calls=%d", err, fake.contextCalls)
	}
	recoveredText := "fresh synthetic source\n"
	recovered, err := NewRecoveredSource(recoveredText, view.SourceRef, view.SourceDigest, view.RecoveryRef)
	if err != nil {
		t.Fatal(err)
	}
	fake.recover = recovered
	if got, err := session.Recover(); err != nil || got != recovered {
		t.Fatalf("recover = %p, %v", got, err)
	}
	receipt, err := session.Complete(map[string]any{"opaque": true}, CompletionOptions{Outcome: HostOutcomeCompleted, Usage: map[string]any{"requests": int64(1)}})
	if err != nil {
		t.Fatal(err)
	}
	if !receipt.Sealed() || !receipt.Verify() || receipt.HostResult == nil {
		t.Fatalf("receipt not sealed/verifiable: %#v", receipt)
	}
	if repeated, err := session.Complete(map[string]any{"opaque": true}, HostOutcomeCompleted, map[string]any{"requests": int64(1)}); err != nil || repeated != receipt {
		t.Fatalf("repeated complete = %p, %v", repeated, err)
	}
	if _, err := session.Complete(map[string]any{"different": true}, HostOutcomeCompleted); err == nil {
		t.Fatal("conflicting complete unexpectedly succeeded")
	}
	if session.State() != SessionStateCompleted {
		t.Fatalf("state = %s", session.State())
	}
	if err := session.Close(); err != nil || session.State() != SessionStateClosed {
		t.Fatalf("close = %v, state=%s", err, session.State())
	}
	if err := session.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestSessionRequiresLifecycleOrdering(t *testing.T) {
	session, err := NewContextSession("task", SessionOptions{SessionID: "s", TaskID: "t", Engine: &fakeEngine{}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := session.Prepare(nil); err == nil {
		t.Fatal("prepare without source unexpectedly succeeded")
	}
	if _, err := session.Complete(nil); err == nil {
		t.Fatal("complete before prepare unexpectedly succeeded")
	}
	if err := session.Close(); err == nil {
		t.Fatal("close before terminal receipt unexpectedly succeeded")
	}
	if _, err := session.Plan(nil); err == nil {
		t.Fatal("nil source unexpectedly accepted")
	}
	if _, err := session.Plan(testSource(t, t.TempDir()), ContextPlanOptions{Mode: "full"}); err == nil {
		t.Fatal("unsupported mode unexpectedly accepted")
	}
}

func TestSessionFailOpenProducesUnsealedReceipt(t *testing.T) {
	root := t.TempDir()
	session, err := NewContextSession("inspect", SessionOptions{ProjectRoot: root, SessionID: "s", TaskID: "t", FailOpen: true, Engine: &fakeEngine{contextErr: NewEngineTimeout("deadline")}})
	if err != nil {
		t.Fatal(err)
	}
	view, err := session.Prepare(testSource(t, root))
	if err != nil || view != nil {
		t.Fatalf("fail-open prepare = %p, %v", view, err)
	}
	if got := session.Degradations(); len(got) != 1 || got[0] != "engine:engine_timeout" {
		t.Fatalf("degradations = %#v", got)
	}
	receipt, err := session.Complete(nil)
	if err != nil {
		t.Fatal(err)
	}
	if receipt.Sealed() || receipt.Verify() || receipt.IntegrityStatus != IntegrityUnsealed {
		t.Fatalf("fail-open receipt unexpectedly sealed: %#v", receipt)
	}
	if err := receipt.RequireVerified(); err == nil {
		t.Fatal("unsealed receipt unexpectedly verified")
	}
}

func TestSessionEngineFailureAbortsWithEvidence(t *testing.T) {
	root := t.TempDir()
	source := testSource(t, root)
	view := testFixtureView(t, *source)
	fake := &fakeEngine{view: view, contextErr: NewEngineExecutionError("failed", nil, view)}
	session, err := NewContextSession("inspect", SessionOptions{ProjectRoot: root, SessionID: "s", TaskID: "t", Engine: fake})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := session.Prepare(source); err == nil {
		t.Fatal("failed Engine unexpectedly succeeded")
	} else {
		var execution *EngineExecutionError
		if !errors.As(err, &execution) {
			t.Fatalf("error type = %T", err)
		}
	}
	if session.State() != SessionStateAborted || session.Receipt() == nil || session.View() != view {
		t.Fatalf("aborted session lost evidence: state=%s receipt=%p view=%p", session.State(), session.Receipt(), session.View())
	}
	if _, err := session.Complete(nil); err == nil {
		t.Fatal("complete after abort unexpectedly succeeded")
	}
}

func TestSessionRejectsUnboundRecovery(t *testing.T) {
	root := t.TempDir()
	source := testSource(t, root)
	view := testFixtureView(t, *source)
	fake := &fakeEngine{view: view}
	session, err := NewContextSession("inspect", SessionOptions{SessionID: "s", TaskID: "t", Engine: fake})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := session.Recover(); err == nil {
		t.Fatal("recovery before prepare unexpectedly succeeded")
	}
	if _, err := session.Prepare(source); err != nil {
		t.Fatal(err)
	}
	other := *view
	other.RecoveryRef = "input:other"
	if _, err := session.Recover(&other); err == nil {
		t.Fatal("unbound recovery view unexpectedly accepted")
	}
	fake.recover = nil
	if _, err := session.Recover(); err == nil {
		t.Fatal("nil recovery unexpectedly accepted")
	}
}

func TestSessionAbortKeepsHostExceptionPrivate(t *testing.T) {
	root := t.TempDir()
	session, err := NewContextSession("inspect", SessionOptions{ProjectRoot: root, SessionID: "s", TaskID: "t", Engine: &fakeEngine{view: testFixtureView(t, *testSource(t, root))}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := session.Prepare(testSource(t, root)); err != nil {
		t.Fatal(err)
	}
	secret := errors.New("secret host detail")
	receipt, err := session.Abort(secret)
	if err != nil {
		t.Fatal(err)
	}
	if receipt.HostException != secret || receipt.HostExceptionType == "" {
		t.Fatalf("host exception identity was not retained")
	}
	payload := string(mustJSON(receipt.ToDict()))
	if stringsContains(payload, "secret host detail") {
		t.Fatal("serialized receipt exposed host exception message")
	}
}

func stringsContains(value, wanted string) bool {
	for index := 0; index+len(wanted) <= len(value); index++ {
		if value[index:index+len(wanted)] == wanted {
			return true
		}
	}
	return false
}
