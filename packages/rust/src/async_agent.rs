// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

use std::collections::VecDeque;
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::task::{Context, Poll, Waker};
use std::thread;
use std::time::Duration;

use serde_json::Value;

use crate::agent::{
    AgentContext, AgentMetrics, AgentPermissions, AgentTermination, ExecutionPolicy, GitLabSource,
    ReadMode, ToolResult,
};
use crate::errors::{boxed, EngineExecutionError, SdkResult};

#[derive(Debug)]
pub struct AsyncAgentContext {
    context: Arc<AgentContext>,
}

impl AsyncAgentContext {
    pub async fn open(project_root: impl AsRef<Path>) -> SdkResult<Self> {
        Self::open_with_configuration(
            project_root.as_ref().to_owned(),
            String::new(),
            AgentPermissions::default(),
            ExecutionPolicy::default(),
            None,
            Duration::from_secs(30),
        )
        .await
    }

    pub async fn open_with_gitlab_source(
        project_root: impl AsRef<Path>,
        gitlab_source: GitLabSource,
    ) -> SdkResult<Self> {
        Self::open_with_configuration_and_gitlab_source(
            project_root.as_ref().to_owned(),
            String::new(),
            AgentPermissions::default(),
            ExecutionPolicy::default(),
            None,
            Duration::from_secs(30),
            Some(gitlab_source),
        )
        .await
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn open_with_policy_and_gitlab_source(
        project_root: impl AsRef<Path>,
        task: impl AsRef<str>,
        permissions: AgentPermissions,
        execution_policy: ExecutionPolicy,
        engine_binary: Option<PathBuf>,
        timeout: Duration,
        gitlab_source: GitLabSource,
    ) -> SdkResult<Self> {
        Self::open_with_configuration_and_gitlab_source(
            project_root.as_ref().to_owned(),
            task.as_ref().to_owned(),
            permissions,
            execution_policy,
            engine_binary,
            timeout,
            Some(gitlab_source),
        )
        .await
    }

    pub fn from_context(context: AgentContext) -> Self {
        Self {
            context: Arc::new(context),
        }
    }

    pub fn context(&self) -> &AgentContext {
        self.context.as_ref()
    }

    pub fn capabilities(&self) -> &[String] {
        self.context.capabilities()
    }

    pub fn metrics(&self) -> AgentMetrics {
        self.context.metrics()
    }

    pub async fn call(&self, tool: &str, arguments: Value) -> SdkResult<ToolResult> {
        let tool = tool.to_owned();
        context_operation(Arc::clone(&self.context), move |context| {
            context.call(&tool, arguments)
        })
        .await
    }

    pub async fn read(
        &self,
        path: impl AsRef<str>,
        mode: ReadMode,
        fresh: bool,
    ) -> SdkResult<ToolResult> {
        let path = path.as_ref().to_owned();
        context_operation(Arc::clone(&self.context), move |context| {
            context.read(path, mode, fresh)
        })
        .await
    }

    pub async fn run<I, S>(&self, argv: I) -> SdkResult<ToolResult>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let argv: Vec<String> = argv
            .into_iter()
            .map(|value| value.as_ref().to_owned())
            .collect();
        context_operation(Arc::clone(&self.context), move |context| context.run(argv)).await
    }

    /// Cancels the context using one bounded async operation slot.
    ///
    /// If all four slots are occupied, this returns the typed capacity error; drop the
    /// pending operation futures to request nonblocking cancellation without admission.
    pub async fn cancel(&self) -> SdkResult<()> {
        context_operation(Arc::clone(&self.context), AgentContext::cancel).await
    }

    /// Closes the context using one bounded async operation slot.
    ///
    /// If all four slots are occupied, this returns the typed capacity error; drop the
    /// pending operation futures to request nonblocking cancellation without admission.
    pub async fn close(&self) -> SdkResult<()> {
        context_operation(Arc::clone(&self.context), AgentContext::close).await
    }

    pub async fn reconnect(&self) -> SdkResult<Self> {
        let project_root = self.context.project_root().to_owned();
        let task = self.context.task().to_owned();
        let permissions = self.context.permissions();
        let execution_policy = self.context.execution_policy().clone();
        let engine_binary = self.context.engine_binary().to_owned();
        let timeout = self.context.timeout();
        let gitlab_source = self.context.gitlab_source().cloned();
        self.close().await?;
        Self::open_with_configuration_and_gitlab_source(
            project_root,
            task,
            permissions,
            execution_policy,
            Some(engine_binary),
            timeout,
            gitlab_source,
        )
        .await
    }

    async fn open_with_configuration(
        project_root: PathBuf,
        task: String,
        permissions: AgentPermissions,
        execution_policy: ExecutionPolicy,
        engine_binary: Option<PathBuf>,
        timeout: Duration,
    ) -> SdkResult<Self> {
        Self::open_with_configuration_and_gitlab_source(
            project_root,
            task,
            permissions,
            execution_policy,
            engine_binary,
            timeout,
            None,
        )
        .await
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) async fn open_with_configuration_and_gitlab_source(
        project_root: PathBuf,
        task: String,
        permissions: AgentPermissions,
        execution_policy: ExecutionPolicy,
        engine_binary: Option<PathBuf>,
        timeout: Duration,
        gitlab_source: Option<GitLabSource>,
    ) -> SdkResult<Self> {
        open_operation_with_gitlab_source(
            project_root,
            task,
            permissions,
            execution_policy,
            engine_binary,
            timeout,
            gitlab_source,
        )
        .await
    }
}

fn context_operation<T, F>(context: Arc<AgentContext>, operation: F) -> AsyncOperation<T>
where
    T: Send + 'static,
    F: FnOnce(&AgentContext) -> SdkResult<T> + Send + 'static,
{
    let control = Arc::new(OperationControl::with_termination(
        context.termination_handle(),
    ));
    AsyncOperation::new(control, move |control| {
        if control.is_cancelled() {
            return Err(cancellation_error());
        }
        operation(&context)
    })
}

#[allow(clippy::too_many_arguments)]
fn open_operation_with_gitlab_source(
    project_root: PathBuf,
    task: String,
    permissions: AgentPermissions,
    execution_policy: ExecutionPolicy,
    engine_binary: Option<PathBuf>,
    timeout: Duration,
    gitlab_source: Option<GitLabSource>,
) -> AsyncOperation<AsyncAgentContext> {
    let control = Arc::new(OperationControl::new());
    AsyncOperation::new(control, move |control| {
        let hook_control = Arc::clone(&control);
        let context = AgentContext::open_with_gitlab_source_hook(
            &project_root,
            &task,
            permissions,
            execution_policy,
            engine_binary,
            timeout,
            gitlab_source,
            move |context| hook_control.install_termination(context.termination_handle()),
        )?;
        if control.is_cancelled() {
            control.cancel();
            return Err(cancellation_error());
        }
        Ok(AsyncAgentContext {
            context: Arc::new(context),
        })
    })
}

const MAX_ASYNC_WORKERS: usize = 4;

// A slot is acquired on first poll and held until the result is consumed by poll or its
// deferred drop/termination job completes; this bounds workers and undelivered-result cleanup.

static ASYNC_WORKER_COUNT: OnceLock<Mutex<usize>> = OnceLock::new();
static CLEANUP_QUEUE: OnceLock<Option<Arc<CleanupQueue>>> = OnceLock::new();

#[derive(Debug)]
struct AsyncSlot;

impl Drop for AsyncSlot {
    fn drop(&mut self) {
        if let Some(counter) = ASYNC_WORKER_COUNT.get() {
            if let Ok(mut count) = counter.lock() {
                *count = count.saturating_sub(1);
            }
        }
    }
}

fn try_acquire_slot() -> Option<Arc<AsyncSlot>> {
    // Do not admit an operation unless the single bounded cleanup worker exists; this
    // keeps every admitted slot paired with an eventual asynchronous cleanup path.
    cleanup_queue()?;
    let counter = ASYNC_WORKER_COUNT.get_or_init(|| Mutex::new(0));
    let mut count = counter.lock().ok()?;
    if *count >= MAX_ASYNC_WORKERS {
        return None;
    }
    *count += 1;
    Some(Arc::new(AsyncSlot))
}

type DeferredDrop = Box<dyn FnOnce() + Send + 'static>;

struct CleanupState {
    result_ready: AtomicBool,
    result_signal: Condvar,
    result_lock: Mutex<()>,
    deferred_drop: Mutex<Option<DeferredDrop>>,
}

impl CleanupState {
    fn new() -> Self {
        Self {
            result_ready: AtomicBool::new(false),
            result_signal: Condvar::new(),
            result_lock: Mutex::new(()),
            deferred_drop: Mutex::new(None),
        }
    }

    fn defer_result<T>(&self, result: SdkResult<T>)
    where
        T: Send + 'static,
    {
        let mut deferred_drop = self
            .deferred_drop
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *deferred_drop = Some(Box::new(move || drop(result)));
    }

    fn mark_result_ready(&self) {
        // Pair publication with the waiter's predicate lock: an atomic flag alone
        // cannot prevent a notification being lost between its check and wait.
        let _guard = self
            .result_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        self.result_ready.store(true, Ordering::Release);
        self.result_signal.notify_all();
    }

    fn wait_for_result(&self) {
        if self.result_ready.load(Ordering::Acquire) {
            return;
        }
        let mut guard = self
            .result_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        while !self.result_ready.load(Ordering::Acquire) {
            guard = self
                .result_signal
                .wait(guard)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
    }

    fn take_deferred_result(&self) -> Option<DeferredDrop> {
        self.deferred_drop
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take()
    }
}

struct CleanupJob {
    termination: Option<AgentTermination>,
    slot: Arc<AsyncSlot>,
    state: Arc<CleanupState>,
}

struct CleanupQueue {
    jobs: Mutex<VecDeque<CleanupJob>>,
    wake: Condvar,
}

fn cleanup_queue() -> Option<Arc<CleanupQueue>> {
    CLEANUP_QUEUE
        .get_or_init(|| {
            let queue = Arc::new(CleanupQueue {
                jobs: Mutex::new(VecDeque::new()),
                wake: Condvar::new(),
            });
            let worker_queue = Arc::clone(&queue);
            if thread::Builder::new()
                .name("leanctx-sdk-cleanup".to_owned())
                .spawn(move || cleanup_worker(worker_queue))
                .is_err()
            {
                return None;
            }
            Some(queue)
        })
        .clone()
}

fn cleanup_worker(queue: Arc<CleanupQueue>) {
    loop {
        let job = {
            let mut jobs = queue
                .jobs
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            while jobs.is_empty() {
                jobs = queue
                    .wake
                    .wait(jobs)
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
            }
            jobs.pop_front()
        };
        if let Some(job) = job {
            if let Some(termination) = job.termination {
                let _ = termination.terminate();
            }
            job.state.wait_for_result();
            if let Some(deferred_drop) = job.state.take_deferred_result() {
                deferred_drop();
            }
            drop(job.slot);
        }
    }
}

fn enqueue_cleanup(job: CleanupJob) -> bool {
    let Some(queue) = cleanup_queue() else {
        return false;
    };
    let mut jobs = queue
        .jobs
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    debug_assert!(jobs.len() < MAX_ASYNC_WORKERS);
    jobs.push_back(job);
    queue.wake.notify_one();
    true
}

struct OperationControl {
    cancelled: AtomicBool,
    cleanup_requested: AtomicBool,
    cleanup_enqueued: AtomicBool,
    termination: Mutex<Option<AgentTermination>>,
    slot: Mutex<Option<Arc<AsyncSlot>>>,
    cleanup_state: Arc<CleanupState>,
}

impl OperationControl {
    fn new() -> Self {
        Self {
            cancelled: AtomicBool::new(false),
            cleanup_requested: AtomicBool::new(false),
            cleanup_enqueued: AtomicBool::new(false),
            termination: Mutex::new(None),
            slot: Mutex::new(None),
            cleanup_state: Arc::new(CleanupState::new()),
        }
    }

    fn with_termination(termination: AgentTermination) -> Self {
        let control = Self::new();
        if let Ok(mut current) = control.termination.lock() {
            *current = Some(termination);
        }
        control
    }

    fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    fn install_slot(&self, slot: Arc<AsyncSlot>) -> bool {
        match self.slot.lock() {
            Ok(mut current) => {
                *current = Some(slot);
                true
            }
            Err(_) => false,
        }
    }

    fn install_termination(&self, termination: AgentTermination) {
        if let Ok(mut slot) = self.termination.lock() {
            *slot = Some(termination);
        }
        self.maybe_enqueue_cleanup();
    }

    fn cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
        self.cleanup_requested.store(true, Ordering::Release);
        self.maybe_enqueue_cleanup();
    }

    fn cancel_with_deferred_result<T>(&self, result: SdkResult<T>)
    where
        T: Send + 'static,
    {
        self.cleanup_state.defer_result(result);
        self.cleanup_state.mark_result_ready();
        self.cancel();
    }

    fn resolve_result(&self) {
        if self.cleanup_requested.load(Ordering::Acquire) {
            self.maybe_enqueue_cleanup();
            return;
        }
        if let Ok(mut slot) = self.slot.lock() {
            slot.take();
        }
    }

    fn maybe_enqueue_cleanup(&self) {
        if !self.cleanup_requested.load(Ordering::Acquire)
            || self.cleanup_enqueued.load(Ordering::Acquire)
        {
            return;
        }
        if self
            .cleanup_enqueued
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return;
        }
        let Some(slot) = self.slot.lock().ok().and_then(|slot| slot.clone()) else {
            self.cleanup_enqueued.store(false, Ordering::Release);
            return;
        };
        let termination = self.termination.lock().ok().and_then(|slot| slot.clone());
        let state = Arc::clone(&self.cleanup_state);
        if enqueue_cleanup(CleanupJob {
            termination,
            slot,
            state,
        }) {
            if let Ok(mut slot) = self.slot.lock() {
                slot.take();
            }
        } else {
            self.cleanup_enqueued.store(false, Ordering::Release);
        }
    }
}

struct AsyncOperation<T: Send + 'static> {
    state: Arc<Mutex<OperationState<T>>>,
    control: Arc<OperationControl>,
    operation: Option<OperationFn<T>>,
}

type OperationFn<T> = Box<dyn FnOnce(Arc<OperationControl>) -> SdkResult<T> + Send>;

impl<T: Send + 'static> Unpin for AsyncOperation<T> {}

struct OperationState<T> {
    started: bool,
    finished: bool,
    result_consumed: bool,
    result: Option<SdkResult<T>>,
    waker: Option<Waker>,
}

impl<T> OperationState<T> {
    fn new() -> Self {
        Self {
            started: false,
            finished: false,
            result_consumed: false,
            result: None,
            waker: None,
        }
    }
}

impl<T: Send + 'static> AsyncOperation<T> {
    fn new<F>(control: Arc<OperationControl>, operation: F) -> Self
    where
        T: Send + 'static,
        F: FnOnce(Arc<OperationControl>) -> SdkResult<T> + Send + 'static,
    {
        Self {
            state: Arc::new(Mutex::new(OperationState::new())),
            control,
            operation: Some(Box::new(operation)),
        }
    }
}

impl<T: Send + 'static> Future for AsyncOperation<T> {
    type Output = SdkResult<T>;

    fn poll(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Self::Output> {
        let (operation, ready_result) = {
            let this = self.as_mut().get_mut();
            let mut state = match this.state.lock() {
                Ok(state) => state,
                Err(_) => {
                    return Poll::Ready(Err(boxed(EngineExecutionError::new(
                        "Agent Tools async operation state is poisoned",
                    ))));
                }
            };
            if let Some(result) = state.result.take() {
                state.result_consumed = true;
                (None, Some(result))
            } else if state.finished {
                return Poll::Ready(Err(boxed(EngineExecutionError::new(
                    "Agent Tools async operation was polled after completion",
                ))));
            } else {
                state.waker = Some(context.waker().clone());
                if state.started {
                    (None, None)
                } else {
                    state.started = true;
                    (this.operation.take(), None)
                }
            }
        };

        if let Some(result) = ready_result {
            self.as_ref().get_ref().control.resolve_result();
            return Poll::Ready(result);
        }

        if let Some(operation) = operation {
            let this = self.as_ref().get_ref();
            let state = match this.state.lock() {
                Ok(_) => Arc::clone(&this.state),
                Err(_) => {
                    return Poll::Ready(Err(boxed(EngineExecutionError::new(
                        "Agent Tools async operation state is poisoned",
                    ))));
                }
            };
            let control = Arc::clone(&this.control);
            let worker_control = Arc::clone(&control);
            let Some(slot) = try_acquire_slot() else {
                complete_operation(
                    &state,
                    &control,
                    Err(boxed(EngineExecutionError::new(
                        "Agent Tools async worker capacity is exhausted",
                    ))),
                );
                return Poll::Pending;
            };
            if !control.install_slot(slot) {
                complete_operation(
                    &state,
                    &control,
                    Err(boxed(EngineExecutionError::new(
                        "Agent Tools async worker admission state is poisoned",
                    ))),
                );
                return Poll::Pending;
            }
            let worker_state = Arc::clone(&state);
            let worker_failed_to_start = thread::Builder::new()
                .name("leanctx-sdk-async".to_owned())
                .spawn(move || {
                    let result = if worker_control.is_cancelled() {
                        Err(cancellation_error())
                    } else {
                        operation(Arc::clone(&worker_control))
                    };
                    complete_operation(&worker_state, &worker_control, result);
                    worker_control.maybe_enqueue_cleanup();
                })
                .is_err();
            if worker_failed_to_start {
                complete_operation(
                    &state,
                    &control,
                    Err(boxed(EngineExecutionError::new(
                        "Agent Tools async worker could not be started",
                    ))),
                );
            }
        }
        Poll::Pending
    }
}

impl<T: Send + 'static> Drop for AsyncOperation<T> {
    fn drop(&mut self) {
        let deferred_result = match self.state.lock() {
            Ok(mut state) => {
                if state.result.is_some() && !state.result_consumed {
                    state.result_consumed = true;
                    state.result.take()
                } else {
                    if state.started && !state.finished {
                        // Set cancellation while holding the state lock so the worker cannot
                        // publish an undelivered result after Drop has chosen this path.
                        self.control.cancel();
                    }
                    None
                }
            }
            Err(_) => {
                self.control.cancel();
                None
            }
        };
        if let Some(result) = deferred_result {
            self.control.cancel_with_deferred_result(result);
        }
    }
}

fn complete_operation<T>(
    state: &Arc<Mutex<OperationState<T>>>,
    control: &Arc<OperationControl>,
    result: SdkResult<T>,
) where
    T: Send + 'static,
{
    let waker = match state.lock() {
        Ok(mut state) => {
            if control.is_cancelled() {
                control.cleanup_state.defer_result(result);
                control.cleanup_state.mark_result_ready();
            } else {
                state.result = Some(result);
            }
            state.finished = true;
            state.waker.take()
        }
        Err(_) => None,
    };
    if let Some(waker) = waker {
        waker.wake();
    }
}

fn cancellation_error() -> Box<dyn std::error::Error + Send + Sync> {
    boxed(EngineExecutionError::new(
        "Agent Tools async operation was cancelled",
    ))
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    use std::collections::BTreeSet;
    use std::error::Error;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::process::Command;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{Barrier, Mutex as StdMutex, OnceLock};
    use std::task::{Wake, Waker};
    use std::time::Instant;

    use serde_json::json;

    static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);
    static TEST_LOCK: OnceLock<StdMutex<()>> = OnceLock::new();

    struct TempRoot {
        path: PathBuf,
    }

    impl TempRoot {
        fn new() -> Result<Self, Box<dyn Error + Send + Sync>> {
            for _ in 0..100 {
                let number = NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed);
                let path = std::env::temp_dir().join(format!(
                    "leanctx-rust-async-agent-{}-{number}",
                    std::process::id()
                ));
                match fs::create_dir(&path) {
                    Ok(()) => return Ok(Self { path }),
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                    Err(error) => return Err(error.into()),
                }
            }
            Err("could not create an isolated async AgentContext test directory".into())
        }
    }

    impl Drop for TempRoot {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[derive(Debug)]
    struct ThreadWaker {
        thread: thread::Thread,
    }

    impl Wake for ThreadWaker {
        fn wake(self: Arc<Self>) {
            self.thread.unpark();
        }

        fn wake_by_ref(self: &Arc<Self>) {
            self.thread.unpark();
        }
    }

    fn block_on<F: Future>(future: F) -> F::Output {
        let current = thread::current();
        let waker = Waker::from(Arc::new(ThreadWaker {
            thread: current.clone(),
        }));
        let mut future = Box::pin(future);
        let mut context = Context::from_waker(&waker);
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Poll::Ready(result) = future.as_mut().poll(&mut context) {
                return result;
            }
            assert!(
                Instant::now() < deadline,
                "async test operation exceeded bound"
            );
            thread::park_timeout(Duration::from_millis(10));
        }
    }

    fn poll_once<F: Future + ?Sized>(future: Pin<&mut F>) -> Poll<F::Output> {
        let waker = Waker::from(Arc::new(ThreadWaker {
            thread: thread::current(),
        }));
        let mut context = Context::from_waker(&waker);
        future.poll(&mut context)
    }

    fn shell_quote(value: &str) -> String {
        format!("'{}'", value.replace('\'', "'\\''"))
    }

    fn write_executable(
        root: &TempRoot,
        body: &str,
    ) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
        let path = root.path.join("fake-agent-engine.sh");
        fs::write(&path, format!("#!/bin/sh\nset -eu\n{body}\n"))?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
        Ok(path)
    }

    fn agent_result() -> Value {
        json!({
            "text": "ok",
            "content_blocks": [{"type": "text", "text": "ok"}],
            "original_tokens": 10,
            "output_tokens": 4,
            "saved_tokens": 6,
            "mode": "full",
            "changed": false,
            "shell": null
        })
    }

    fn agent_script(
        root: &TempRoot,
        call_delay: Duration,
    ) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
        let hello = json!({
            "agent_tools_interface_version": "1.0.0",
            "allow_exec": false,
            "allow_write": false,
            "capabilities": [
                "ctx_compose",
                "ctx_glob",
                "ctx_read",
                "ctx_search",
                "ctx_symbol",
                "ctx_tree"
            ],
            "engine_version": "3.10.2",
            "schema_version": 1,
            "transport_version": 1
        });
        let hello = shell_quote(&serde_json::to_string(&hello)?);
        let result = shell_quote(&serde_json::to_string(&agent_result())?);
        let empty = shell_quote("{}");
        let body = format!(
            r#"while IFS= read -r line; do
  id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
  case "$line" in
    *'"op":"hello"'*) printf '{{"id":"%s","ok":true,"result":%s}}\n' "$id" {} ;;
    *'"op":"call"'*) sleep {}; printf '{{"id":"%s","ok":true,"result":%s}}\n' "$id" {} ;;
    *'"op":"close"'*) printf '{{"id":"%s","ok":true,"result":%s}}\n' "$id" {} ;;
  esac
done"#,
            hello,
            call_delay.as_secs_f64(),
            result,
            empty,
        );
        write_executable(root, &body)
    }

    fn completed_open_script(
        root: &TempRoot,
        descendant_path: &Path,
    ) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
        let hello = json!({
            "agent_tools_interface_version": "1.0.0",
            "allow_exec": false,
            "allow_write": false,
            "capabilities": [
                "ctx_compose",
                "ctx_glob",
                "ctx_read",
                "ctx_search",
                "ctx_symbol",
                "ctx_tree"
            ],
            "engine_version": "3.10.2",
            "schema_version": 1,
            "transport_version": 1
        });
        let hello = shell_quote(&serde_json::to_string(&hello)?);
        let descendant_path = shell_quote(&descendant_path.to_string_lossy());
        let body = format!(
            r#"while IFS= read -r line; do
  id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
  case "$line" in
    *'"op":"hello"'*) sleep 30 & child=$!; printf '%s\n' "$child" > {}; printf '{{"id":"%s","ok":true,"result":%s}}\n' "$id" {}; sleep 30 ;;
  esac
done"#,
            descendant_path, hello
        );
        write_executable(root, &body)
    }

    fn open_context(
        root: &TempRoot,
        engine_binary: PathBuf,
    ) -> Result<AsyncAgentContext, Box<dyn Error + Send + Sync>> {
        block_on(AsyncAgentContext::open_with_configuration(
            root.path.clone(),
            "async-agent-test".to_owned(),
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine_binary),
            Duration::from_secs(2),
        ))
    }

    fn policy_dirs() -> BTreeSet<PathBuf> {
        let prefix = format!("leanctx-agent-{}-", std::process::id());
        fs::read_dir(std::env::temp_dir())
            .into_iter()
            .flatten()
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .map(|name| name.starts_with(&prefix))
                    .unwrap_or(false)
            })
            .collect()
    }

    fn process_exists(pid: u32) -> bool {
        Command::new("/bin/kill")
            .args(["-0", &pid.to_string()])
            .stderr(std::process::Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }

    fn worker_count() -> usize {
        ASYNC_WORKER_COUNT
            .get()
            .and_then(|count| count.lock().ok().map(|count| *count))
            .unwrap_or(0)
    }

    fn wait_for_pid(path: &Path) -> Result<u32, Box<dyn Error + Send + Sync>> {
        for _ in 0..200 {
            if let Ok(contents) = fs::read_to_string(path) {
                if let Ok(pid) = contents.trim().parse() {
                    return Ok(pid);
                }
            }
            thread::sleep(Duration::from_millis(5));
        }
        Err("fixture did not publish a complete process identity".into())
    }

    #[test]
    fn cleanup_completion_uses_the_waiters_condition_lock() {
        let state = Arc::new(CleanupState::new());
        let guard = state
            .result_lock
            .lock()
            .expect("hold waiter condition lock");
        let worker_state = Arc::clone(&state);
        let (started_tx, started_rx) = std::sync::mpsc::channel();
        let (done_tx, done_rx) = std::sync::mpsc::channel();
        let publisher = thread::spawn(move || {
            started_tx.send(()).expect("signal publisher start");
            worker_state.mark_result_ready();
            done_tx.send(()).expect("signal completion");
        });
        started_rx
            .recv_timeout(Duration::from_secs(1))
            .expect("publisher started");
        let premature = done_rx.recv_timeout(Duration::from_millis(50));
        drop(guard);
        publisher.join().expect("publisher completed");
        assert!(matches!(
            premature,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout)
        ));
        done_rx
            .recv_timeout(Duration::from_secs(1))
            .expect("completion after release");
        state.wait_for_result();
    }

    #[test]
    fn async_operations_are_nonblocking_bounded_and_preserve_lifecycle(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let root = TempRoot::new()?;
        let engine = agent_script(&root, Duration::from_millis(250))?;
        let context = open_context(&root, engine)?;
        assert_eq!(
            context.context().project_root(),
            fs::canonicalize(&root.path)?
        );

        let mut operations: Vec<Pin<Box<dyn Future<Output = SdkResult<ToolResult>>>>> = Vec::new();
        for _ in 0..=MAX_ASYNC_WORKERS {
            operations.push(Box::pin(context.call("ctx_read", json!({}))));
        }
        let started = Instant::now();
        for operation in &mut operations {
            let _ = poll_once(operation.as_mut());
        }
        assert!(started.elapsed() < Duration::from_millis(200));

        // Management uses the same bounded admission; under saturation it preserves the
        // typed capacity error, while dropping pending operation futures remains cancellable.
        let cancel_error = block_on(context.cancel()).unwrap_err();
        assert!(cancel_error
            .downcast_ref::<crate::errors::EngineExecutionError>()
            .is_some());
        assert!(cancel_error.to_string().contains("capacity is exhausted"));
        let close_error = block_on(context.close()).unwrap_err();
        assert!(close_error
            .downcast_ref::<crate::errors::EngineExecutionError>()
            .is_some());
        assert!(close_error.to_string().contains("capacity is exhausted"));

        let mut saturation_errors = 0;
        for operation in operations {
            if let Err(error) = block_on(operation) {
                if error.to_string().contains("capacity is exhausted") {
                    saturation_errors += 1;
                }
            }
        }
        assert_eq!(saturation_errors, 1);

        let validation = block_on(context.call("", json!({}))).unwrap_err();
        assert!(validation
            .downcast_ref::<crate::errors::ValidationError>()
            .is_some());
        block_on(context.close())?;
        block_on(context.close())?;
        let reconnected = block_on(context.reconnect())?;
        assert_eq!(
            reconnected.context().project_root(),
            fs::canonicalize(&root.path)?
        );
        assert_eq!(
            block_on(reconnected.read("fixture/source.txt", ReadMode::Full, false))?.text(),
            "ok"
        );
        let mut pending_call = Box::pin(reconnected.call("ctx_read", json!({})));
        assert!(matches!(poll_once(pending_call.as_mut()), Poll::Pending));
        block_on(reconnected.cancel())?;
        assert!(block_on(pending_call).is_err());
        block_on(reconnected.close())?;
        Ok(())
    }

    #[test]
    fn dropped_open_is_quick_and_cleans_policy_and_descendants_across_race(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let before = policy_dirs();
        for _ in 0..200 {
            if worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(worker_count(), 0);
        let root = TempRoot::new()?;
        let descendant_path = root.path.join("descendant.pid");
        let script = write_executable(
            &root,
            &format!(
                r#"while IFS= read -r line; do
  case "$line" in
    *'"op":"hello"'*) sleep 30 & child=$!; printf '%s\n' "$child" > {}; sleep 30 ;;
  esac
done"#,
                shell_quote(&descendant_path.to_string_lossy())
            ),
        )?;
        let mut pending = Box::pin(AsyncAgentContext::open_with_configuration(
            root.path.clone(),
            "cancel-open".to_owned(),
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(script),
            Duration::from_secs(5),
        ));
        assert!(matches!(poll_once(pending.as_mut()), Poll::Pending));
        let pid = wait_for_pid(&descendant_path)?;
        let dropped_at = Instant::now();
        drop(pending);
        assert!(dropped_at.elapsed() < Duration::from_millis(200));
        for _ in 0..200 {
            if !process_exists(pid) && policy_dirs() == before {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(!process_exists(pid));
        assert_eq!(policy_dirs(), before);

        let race_root = TempRoot::new()?;
        let race_pid_path = race_root.path.join("descendant.pid");
        let race_script = write_executable(
            &race_root,
            &format!(
                r#"while IFS= read -r line; do
  case "$line" in
    *'"op":"hello"'*) sleep 30 & child=$!; printf '%s\n' "$child" > {}; sleep 30 ;;
  esac
done"#,
                shell_quote(&race_pid_path.to_string_lossy())
            ),
        )?;
        let mut raced = Box::pin(AsyncAgentContext::open_with_configuration(
            race_root.path.clone(),
            "cancel-before-spawn".to_owned(),
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(race_script),
            Duration::from_secs(5),
        ));
        assert!(matches!(poll_once(raced.as_mut()), Poll::Pending));
        drop(raced);
        thread::sleep(Duration::from_millis(100));
        if race_pid_path.is_file() {
            assert!(!process_exists(wait_for_pid(&race_pid_path)?));
        }
        assert_eq!(policy_dirs(), before);
        Ok(())
    }

    #[test]
    fn dropping_completed_unconsumed_open_is_quick_and_cleans_after_barrier(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let before = policy_dirs();
        for _ in 0..200 {
            if worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(worker_count(), 0);
        let root = TempRoot::new()?;
        let descendant_path = root.path.join("completed-descendant.pid");
        let script = completed_open_script(&root, &descendant_path)?;
        let mut pending = Box::pin(open_operation_with_gitlab_source(
            root.path.clone(),
            "completed-open".to_owned(),
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(script),
            Duration::from_secs(5),
            None,
        ));
        assert!(matches!(poll_once(pending.as_mut()), Poll::Pending));
        let pid = wait_for_pid(&descendant_path)?;
        let state = Arc::clone(&pending.as_ref().get_ref().state);
        let barrier = Arc::new(Barrier::new(2));
        let observer_barrier = Arc::clone(&barrier);
        let observer = thread::spawn(move || loop {
            let finished_with_result = state
                .lock()
                .map(|state| state.finished && state.result.is_some())
                .unwrap_or(false);
            if finished_with_result {
                observer_barrier.wait();
                break;
            }
            thread::yield_now();
        });
        barrier.wait();
        assert_eq!(worker_count(), 1);
        let dropped_at = Instant::now();
        drop(pending);
        assert!(dropped_at.elapsed() < Duration::from_millis(200));
        observer.join().expect("completion observer should finish");

        for _ in 0..200 {
            if !process_exists(pid) && policy_dirs() == before && worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(!process_exists(pid));
        assert_eq!(policy_dirs(), before);
        assert_eq!(worker_count(), 0);
        Ok(())
    }
}
