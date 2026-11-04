// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

// Source resolution: private containment and cleanup tests retained.
// Paired with LeanCTX 3.12.1 (the matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5 and 3.12.0).

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
use crate::errors::{boxed, InstanceExecutionError, SdkResult};

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
        instance_binary: Option<PathBuf>,
        timeout: Duration,
        gitlab_source: GitLabSource,
    ) -> SdkResult<Self> {
        Self::open_with_configuration_and_gitlab_source(
            project_root.as_ref().to_owned(),
            task.as_ref().to_owned(),
            permissions,
            execution_policy,
            instance_binary,
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

    pub fn session_containment(&self) -> crate::agent::SessionContainment {
        self.context.session_containment()
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
        let instance_binary = self.context.instance_binary().to_owned();
        let timeout = self.context.timeout();
        let gitlab_source = self.context.gitlab_source().cloned();
        self.close().await?;
        Self::open_with_configuration_and_gitlab_source(
            project_root,
            task,
            permissions,
            execution_policy,
            Some(instance_binary),
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
        instance_binary: Option<PathBuf>,
        timeout: Duration,
    ) -> SdkResult<Self> {
        Self::open_with_configuration_and_gitlab_source(
            project_root,
            task,
            permissions,
            execution_policy,
            instance_binary,
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
        instance_binary: Option<PathBuf>,
        timeout: Duration,
        gitlab_source: Option<GitLabSource>,
    ) -> SdkResult<Self> {
        open_operation_with_gitlab_source(
            project_root,
            task,
            permissions,
            execution_policy,
            instance_binary,
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
    instance_binary: Option<PathBuf>,
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
            instance_binary,
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
                    return Poll::Ready(Err(boxed(InstanceExecutionError::new(
                        "Agent Tools async operation state is poisoned",
                    ))));
                }
            };
            if let Some(result) = state.result.take() {
                state.result_consumed = true;
                (None, Some(result))
            } else if state.finished {
                return Poll::Ready(Err(boxed(InstanceExecutionError::new(
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
                    return Poll::Ready(Err(boxed(InstanceExecutionError::new(
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
                    Err(boxed(InstanceExecutionError::new(
                        "Agent Tools async worker capacity is exhausted",
                    ))),
                );
                return Poll::Pending;
            };
            if !control.install_slot(slot) {
                complete_operation(
                    &state,
                    &control,
                    Err(boxed(InstanceExecutionError::new(
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
                    Err(boxed(InstanceExecutionError::new(
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
    boxed(InstanceExecutionError::new(
        "Agent Tools async operation was cancelled",
    ))
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

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
            "engine_version": "3.12.1",
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
        policy_path_capture: &Path,
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
            "engine_version": "3.12.1",
            "schema_version": 1,
            "transport_version": 1
        });
        let hello = shell_quote(&serde_json::to_string(&hello)?);
        let descendant_path = shell_quote(&descendant_path.to_string_lossy());
        let policy_path_capture = shell_quote(&policy_path_capture.to_string_lossy());
        let body = format!(
            r#"printf '%s\n' "$6" > {};
while IFS= read -r line; do
  id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
  case "$line" in
    *'"op":"hello"'*) sleep 30 & child=$!; printf '%s\n' "$child" > {}; printf '{{"id":"%s","ok":true,"result":%s}}\n' "$id" {}; sleep 30 ;;
  esac
done"#,
            policy_path_capture, descendant_path, hello
        );
        write_executable(root, &body)
    }

    fn open_context(
        root: &TempRoot,
        instance_binary: PathBuf,
    ) -> Result<AsyncAgentContext, Box<dyn Error + Send + Sync>> {
        block_on(AsyncAgentContext::open_with_configuration(
            root.path.clone(),
            "async-agent-test".to_owned(),
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(instance_binary),
            Duration::from_secs(2),
        ))
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

    fn wait_for_policy_path(path: &Path) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
        for _ in 0..200 {
            if let Ok(contents) = fs::read_to_string(path) {
                let policy_path = PathBuf::from(contents.trim());
                if policy_path.file_name().and_then(|name| name.to_str()) == Some("policy.json") {
                    return Ok(policy_path);
                }
            }
            thread::sleep(Duration::from_millis(5));
        }
        Err("fixture did not publish its policy path".into())
    }

    fn policy_path_removed(path: &Path) -> bool {
        !path.exists() && path.parent().is_some_and(|directory| !directory.exists())
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
            .downcast_ref::<crate::errors::InstanceExecutionError>()
            .is_some());
        assert!(cancel_error.to_string().contains("capacity is exhausted"));
        let close_error = block_on(context.close()).unwrap_err();
        assert!(close_error
            .downcast_ref::<crate::errors::InstanceExecutionError>()
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
        for _ in 0..200 {
            if worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(worker_count(), 0);
        let root = TempRoot::new()?;
        let descendant_path = root.path.join("descendant.pid");
        let policy_path_capture = root.path.join("policy-path.txt");
        let script = write_executable(
            &root,
            &format!(
                r#"printf '%s\n' "$6" > {};
while IFS= read -r line; do
  case "$line" in
    *'"op":"hello"'*) sleep 30 & child=$!; printf '%s\n' "$child" > {}; sleep 30 ;;
  esac
done"#,
                shell_quote(&policy_path_capture.to_string_lossy()),
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
        let policy_path = wait_for_policy_path(&policy_path_capture)?;
        let dropped_at = Instant::now();
        drop(pending);
        assert!(dropped_at.elapsed() < Duration::from_millis(200));
        for _ in 0..200 {
            if !process_exists(pid) && policy_path_removed(&policy_path) {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(!process_exists(pid));
        assert!(
            policy_path_removed(&policy_path),
            "cancelled open left its own policy path behind"
        );

        let race_root = TempRoot::new()?;
        let race_pid_path = race_root.path.join("descendant.pid");
        let race_policy_capture = race_root.path.join("policy-path.txt");
        let race_script = write_executable(
            &race_root,
            &format!(
                r#"printf '%s\n' "$6" > {};
while IFS= read -r line; do
  case "$line" in
    *'"op":"hello"'*) sleep 30 & child=$!; printf '%s\n' "$child" > {}; sleep 30 ;;
  esac
done"#,
                shell_quote(&race_policy_capture.to_string_lossy()),
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
        for _ in 0..200 {
            if worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(worker_count(), 0, "cancelled open cleanup did not finish");
        if race_pid_path.is_file() {
            let pid = wait_for_pid(&race_pid_path)?;
            for _ in 0..200 {
                if !process_exists(pid) {
                    break;
                }
                thread::sleep(Duration::from_millis(10));
            }
            assert!(!process_exists(pid));
        }
        if race_policy_capture.is_file() {
            let policy_path = wait_for_policy_path(&race_policy_capture)?;
            for _ in 0..200 {
                if policy_path_removed(&policy_path) {
                    break;
                }
                thread::sleep(Duration::from_millis(10));
            }
            assert!(
                policy_path_removed(&policy_path),
                "cancel-before-spawn left its own policy path behind"
            );
        }
        Ok(())
    }

    #[test]
    fn dropping_completed_unconsumed_open_is_quick_and_cleans_after_barrier(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        for _ in 0..200 {
            if worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(worker_count(), 0);
        let root = TempRoot::new()?;
        let descendant_path = root.path.join("completed-descendant.pid");
        let policy_path_capture = root.path.join("policy-path.txt");
        let script = completed_open_script(&root, &descendant_path, &policy_path_capture)?;
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
        let policy_path = wait_for_policy_path(&policy_path_capture)?;
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
            if !process_exists(pid) && policy_path_removed(&policy_path) && worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(!process_exists(pid));
        assert!(
            policy_path_removed(&policy_path),
            "dropped completed open left its own policy path behind"
        );
        assert_eq!(worker_count(), 0);
        Ok(())
    }

    /// Stand-in instance that answers the hello and then never reads stdin again,
    /// with one descendant in its process group.
    fn stalled_stdin_script(
        root: &TempRoot,
        leader_path: &Path,
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
            "engine_version": "3.12.1",
            "schema_version": 1,
            "transport_version": 1
        });
        let body = format!(
            r#"IFS= read -r line
id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
printf '%s\n' "$$" > {}
sleep 30 &
printf '%s\n' "$!" > {}
printf '{{"id":"%s","ok":true,"result":%s}}\n' "$id" {}
sleep 30"#,
            shell_quote(&leader_path.to_string_lossy()),
            shell_quote(&descendant_path.to_string_lossy()),
            shell_quote(&serde_json::to_string(&hello)?),
        );
        write_executable(root, &body)
    }

    /// Stand-in instance that answers the hello, keeps one descendant in its
    /// process group, records its policy path, then runs `after_hello`.
    fn terminal_script(
        root: &TempRoot,
        after_hello: &str,
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
            "engine_version": "3.12.1",
            "schema_version": 1,
            "transport_version": 1
        });
        let body = format!(
            r#"printf '%s\n' "$6" > {}
IFS= read -r line
id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
printf '%s\n' "$$" > {}
sleep 30 >/dev/null 2>&1 &
printf '%s\n' "$!" > {}
printf '{{"id":"%s","ok":true,"result":%s}}\n' "$id" {}
{}"#,
            shell_quote(&root.path.join("policy-path.txt").to_string_lossy()),
            shell_quote(&root.path.join("leader.pid").to_string_lossy()),
            shell_quote(&root.path.join("descendant.pid").to_string_lossy()),
            shell_quote(&serde_json::to_string(&hello)?),
            after_hello,
        );
        write_executable(root, &body)
    }

    /// Runs one terminal condition and checks everything it must leave behind.
    fn assert_terminal_condition<E: Error + 'static>(
        after_hello: &str,
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        wait_for_no_writers();
        let root = TempRoot::new()?;
        let engine = terminal_script(&root, after_hello)?;
        let context = AgentContext::open_with_policy(
            &root.path,
            "terminal-condition",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_secs(5),
        )?;
        let leader = wait_for_pid(&root.path.join("leader.pid"))?;
        let descendant = wait_for_pid(&root.path.join("descendant.pid"))?;
        let policy_path = wait_for_policy_path(&root.path.join("policy-path.txt"))?;
        assert!(group_members(leader).contains(&descendant));

        let started = Instant::now();
        let error = context
            .read("fixture/source.txt", ReadMode::Full, false)
            .expect_err("a terminal condition must not succeed");
        assert!(
            error.downcast_ref::<E>().is_some(),
            "unexpected error: {error}"
        );
        assert!(started.elapsed() < Duration::from_secs(3));
        // Terminal: the tree, the writer and the exact policy path are gone,
        // and a later call fails typed instead of reusing the session.
        assert_tree_gone(leader, descendant);
        assert!(
            policy_path_removed(&policy_path),
            "terminal condition left its own policy path behind"
        );
        wait_for_no_writers();
        assert!(context
            .read("fixture/source.txt", ReadMode::Full, false)
            .expect_err("a terminated context is terminal")
            .downcast_ref::<crate::errors::InstanceCrashed>()
            .is_some());
        context.close()?;
        context.close()?;
        Ok(())
    }

    #[test]
    fn closed_stdout_with_live_engine_and_descendant_is_terminal(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        assert_terminal_condition::<crate::errors::InstanceCrashed>("exec 1>&-\nsleep 30")
    }

    #[test]
    fn invalid_tool_result_in_valid_envelope_is_terminal(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        assert_terminal_condition::<crate::errors::InstanceProtocolError>(
            r#"while IFS= read -r line; do
  id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
  printf '{"id":"%s","ok":true,"result":{"extra":true}}\n' "$id"
done"#,
        )
    }

    /// Positive control for the single cleanup owner: repeated and parallel
    /// shutdowns all report success and the tree is gone afterwards.
    #[test]
    fn shutdown_outcome_is_shared_by_repeated_and_parallel_callers(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        wait_for_no_writers();
        let root = TempRoot::new()?;
        let engine = terminal_script(&root, "sleep 30")?;
        let context = AgentContext::open_with_policy(
            &root.path,
            "shared-shutdown",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_secs(30),
        )?;
        let leader = wait_for_pid(&root.path.join("leader.pid"))?;
        let descendant = wait_for_pid(&root.path.join("descendant.pid"))?;
        let outcomes: Vec<SdkResult<()>> = thread::scope(|scope| {
            let callers: Vec<_> = (0..8)
                .map(|index| {
                    let context = &context;
                    scope.spawn(move || {
                        if index % 2 == 0 {
                            context.cancel()
                        } else {
                            context.close()
                        }
                    })
                })
                .collect();
            callers
                .into_iter()
                .map(|caller| caller.join().expect("shutdown caller"))
                .collect()
        });
        for outcome in outcomes {
            outcome?;
        }
        context.cancel()?;
        context.close()?;
        assert_tree_gone(leader, descendant);
        wait_for_no_writers();
        Ok(())
    }

    /// True while the process runs; an unreaped zombie does not count.
    fn running(pid: u32) -> bool {
        Command::new("ps")
            .args(["-o", "stat=", "-p", &pid.to_string()])
            .output()
            .map(|output| {
                let state = String::from_utf8_lossy(&output.stdout).trim().to_owned();
                !state.is_empty() && !state.starts_with('Z')
            })
            .unwrap_or(false)
    }

    /// Negative control for the single cleanup owner: the tree kill fails (an
    /// injected failure that signals nothing, as an unkillable child would).
    /// The failure is stored and reported to every caller; a parallel caller
    /// waits for the owner, bounded, and never reports success.
    #[test]
    fn failed_cleanup_is_stored_shared_and_waiters_are_bounded(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        wait_for_no_writers();
        let root = TempRoot::new()?;
        let engine = terminal_script(&root, "sleep 30")?;
        let context = AgentContext::open_with_policy(
            &root.path,
            "failed-cleanup",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_secs(30),
        )?;
        let leader = wait_for_pid(&root.path.join("leader.pid"))?;
        let descendant = wait_for_pid(&root.path.join("descendant.pid"))?;

        crate::agent::test_hooks::fail_tree_kill_after(Some(Duration::from_millis(600)));
        let (owner, parallel, parallel_elapsed) = thread::scope(|scope| {
            let owner = scope.spawn(|| context.cancel());
            thread::sleep(Duration::from_millis(150));
            let started = Instant::now();
            let parallel = context.close();
            let elapsed = started.elapsed();
            (owner.join().expect("cleanup owner"), parallel, elapsed)
        });
        crate::agent::test_hooks::fail_tree_kill_after(None);

        let owner_error = owner
            .expect_err("a failed tree kill must not report success")
            .to_string();
        assert!(owner_error.contains("injected"), "{owner_error}");
        let parallel_error = parallel
            .expect_err("a parallel close must not report success for a failed cleanup")
            .to_string();
        assert_eq!(parallel_error, owner_error);
        assert!(
            parallel_elapsed >= Duration::from_millis(300),
            "the parallel close returned before the owner finished"
        );
        assert!(parallel_elapsed < Duration::from_secs(5));
        // Stored: every later cancel and close reports the same failure.
        for _ in 0..2 {
            assert_eq!(
                context.cancel().expect_err("stored failure").to_string(),
                owner_error
            );
            assert_eq!(
                context.close().expect_err("stored failure").to_string(),
                owner_error
            );
        }
        assert!(context
            .read("fixture/source.txt", ReadMode::Full, false)
            .expect_err("a context whose cleanup failed is terminal")
            .downcast_ref::<crate::errors::InstanceCrashed>()
            .is_some());

        // The stand-in survived by design. It is this test's own child: end it.
        assert!(running(leader), "the injected failure must not kill");
        Command::new("/bin/kill")
            .args(["-9", &leader.to_string(), &descendant.to_string()])
            .status()?;
        for _ in 0..300 {
            if !running(leader) && !running(descendant) {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(!running(leader) && !running(descendant));
        wait_for_no_writers();
        Ok(())
    }

    /// Negative control for the bounded reap: nothing is signalled, so the
    /// child really keeps running. The owner's reap gives up at its budget,
    /// the failure is stored, and every later caller gets the same failure.
    #[test]
    fn unreapable_engine_is_a_bounded_stored_cleanup_failure(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        wait_for_no_writers();
        let root = TempRoot::new()?;
        let engine = terminal_script(&root, "sleep 30")?;
        let context = AgentContext::open_with_policy(
            &root.path,
            "unreapable",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_secs(30),
        )?;
        let leader = wait_for_pid(&root.path.join("leader.pid"))?;
        let descendant = wait_for_pid(&root.path.join("descendant.pid"))?;

        crate::agent::test_hooks::skip_kill_and_reap_within(Some(Duration::from_millis(400)));
        let started = Instant::now();
        let first = context.cancel();
        let elapsed = started.elapsed();
        crate::agent::test_hooks::skip_kill_and_reap_within(None);

        let failure = first
            .expect_err("an instance that was not reaped must not report success")
            .to_string();
        assert!(
            failure.contains("could not be reaped within its budget"),
            "{failure}"
        );
        assert!(
            elapsed >= Duration::from_millis(300),
            "the reap did not wait"
        );
        assert!(elapsed < Duration::from_secs(3), "the reap was not bounded");
        assert!(running(leader), "the stand-in was expected to survive");
        for _ in 0..2 {
            assert_eq!(
                context.cancel().expect_err("stored failure").to_string(),
                failure
            );
            assert_eq!(
                context.close().expect_err("stored failure").to_string(),
                failure
            );
        }

        // The survivor is this test's own child: end it.
        Command::new("/bin/kill")
            .args(["-9", &leader.to_string(), &descendant.to_string()])
            .status()?;
        for _ in 0..300 {
            if !running(leader) && !running(descendant) {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(!running(leader) && !running(descendant));
        wait_for_no_writers();
        Ok(())
    }

    // --- Group ownership at the last signal --------------------------------
    // The instance's group is addressed by the leader's pid, which is this
    // client's own only while the leader is unreaped. `process.rs` records
    // every group signal together with that fact (test builds only).

    fn signals_for(leader: u32) -> Vec<(i32, bool)> {
        crate::process::group_signal_log::SENT
            .lock()
            .map(|sent| {
                sent.iter()
                    .copied()
                    .filter(|(pgid, _)| *pgid == leader as i32)
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Positive control and the negative after the reap: every group signal
    /// was sent while the leader was unreaped, the tree is gone, and no later
    /// cancel or close signals that number again.
    #[test]
    fn ownership_group_is_signalled_only_while_the_leader_is_unreaped(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let root = TempRoot::new()?;
        let engine = terminal_script(&root, "sleep 30")?;
        let context = AgentContext::open_with_policy(
            &root.path,
            "ownership",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_secs(30),
        )?;
        let leader = wait_for_pid(&root.path.join("leader.pid"))?;
        let descendant = wait_for_pid(&root.path.join("descendant.pid"))?;
        context.cancel()?;
        assert_tree_gone(leader, descendant);
        let sent = signals_for(leader);
        assert!(!sent.is_empty(), "no group signal was sent");
        assert!(
            sent.iter().all(|(_, unreaped)| *unreaped),
            "a group signal was sent after the leader had been reaped: {sent:?}"
        );
        context.cancel()?;
        context.close()?;
        context.cancel()?;
        assert_eq!(
            signals_for(leader).len(),
            sent.len(),
            "the group number was signalled after the reap"
        );
        Ok(())
    }

    /// Leader exits first and leaves a child that holds the instance's stdout
    /// and ignores SIGTERM. The exited leader stays unreaped until cleanup,
    /// so its group is still this context's own and the child is ended.
    #[test]
    fn ownership_leader_exits_first_with_a_pipe_holding_child(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let root = TempRoot::new()?;
        let holder_path = root.path.join("holder.pid");
        let engine = terminal_script(
            &root,
            &format!(
                "IFS= read -r line\n(trap '' TERM; exec sleep 30) &\nprintf '%s\\n' \"$!\" > {}\nexit 7",
                shell_quote(&holder_path.to_string_lossy())
            ),
        )?;
        let context = AgentContext::open_with_policy(
            &root.path,
            "leader-exits-first",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_millis(800),
        )?;
        let leader = wait_for_pid(&root.path.join("leader.pid"))?;
        let descendant = wait_for_pid(&root.path.join("descendant.pid"))?;
        let error = context
            .read("fixture/source.txt", ReadMode::Full, false)
            .expect_err("a call to an instance that exited must fail");
        assert!(
            error
                .downcast_ref::<crate::errors::InstanceTimeout>()
                .is_some()
                || error
                    .downcast_ref::<crate::errors::InstanceCrashed>()
                    .is_some(),
            "unexpected error: {error}"
        );
        let holder = wait_for_pid(&holder_path)?;
        assert_tree_gone(leader, holder);
        assert_tree_gone(leader, descendant);
        let sent = signals_for(leader);
        assert!(!sent.is_empty());
        assert!(sent.iter().all(|(_, unreaped)| *unreaped), "{sent:?}");
        context.close()?;
        Ok(())
    }

    /// Larger than any OS pipe buffer, inside the 1 MiB request bound.
    fn oversized_arguments() -> Value {
        json!({ "padding": "x".repeat(600_000) })
    }

    /// Process identifiers that are still members of `group`, from the process table.
    fn group_members(group: u32) -> Vec<u32> {
        let output = Command::new("ps")
            .args(["-A", "-o", "pid=", "-o", "pgid="])
            .output()
            .expect("list the process table");
        String::from_utf8_lossy(&output.stdout)
            .lines()
            .filter_map(|line| {
                let mut fields = line.split_whitespace();
                let pid: u32 = fields.next()?.parse().ok()?;
                let pgid: u32 = fields.next()?.parse().ok()?;
                (pgid == group).then_some(pid)
            })
            .collect()
    }

    fn active_writers() -> usize {
        crate::agent_io::ACTIVE_WRITERS.load(Ordering::Acquire)
    }

    fn wait_for_no_writers() {
        for _ in 0..300 {
            if active_writers() == 0 {
                return;
            }
            thread::sleep(Duration::from_millis(10));
        }
        panic!("a stdin writer thread is still alive");
    }

    fn assert_tree_gone(leader: u32, descendant: u32) {
        for _ in 0..300 {
            if !process_exists(leader)
                && !process_exists(descendant)
                && group_members(leader).is_empty()
            {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(!process_exists(leader), "LeanCTX instance leader survived");
        assert!(
            !process_exists(descendant),
            "LeanCTX instance descendant survived"
        );
        assert_eq!(group_members(leader), Vec::<u32>::new());
    }

    #[test]
    fn stalled_stdin_write_is_bounded_by_the_deadline_and_typed(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        wait_for_no_writers();
        let root = TempRoot::new()?;
        let leader_path = root.path.join("leader.pid");
        let descendant_path = root.path.join("descendant.pid");
        let engine = stalled_stdin_script(&root, &leader_path, &descendant_path)?;
        let context = AgentContext::open_with_policy(
            &root.path,
            "stalled-write-timeout",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_millis(1500),
        )?;
        let leader = wait_for_pid(&leader_path)?;
        let descendant = wait_for_pid(&descendant_path)?;
        assert!(group_members(leader).contains(&descendant));

        let started = Instant::now();
        let error = context
            .call("ctx_read", oversized_arguments())
            .expect_err("a request the instance never reads must not succeed");
        assert!(
            error
                .downcast_ref::<crate::errors::InstanceTimeout>()
                .is_some(),
            "unexpected error: {error}"
        );
        assert!(started.elapsed() < Duration::from_secs(4));
        assert_tree_gone(leader, descendant);
        assert!(context
            .call("ctx_read", json!({}))
            .expect_err("a timed-out context is terminal")
            .downcast_ref::<crate::errors::InstanceCrashed>()
            .is_some());
        context.close()?;
        wait_for_no_writers();
        Ok(())
    }

    #[test]
    fn cancel_during_stalled_stdin_write_returns_within_a_bound(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        wait_for_no_writers();
        let root = TempRoot::new()?;
        let leader_path = root.path.join("leader.pid");
        let descendant_path = root.path.join("descendant.pid");
        let engine = stalled_stdin_script(&root, &leader_path, &descendant_path)?;
        // The deadline is far away: only cancellation may end this call.
        let context = AgentContext::open_with_policy(
            &root.path,
            "stalled-write-cancel",
            AgentPermissions::read_only(),
            ExecutionPolicy::default(),
            Some(engine),
            Duration::from_secs(30),
        )?;
        let leader = wait_for_pid(&leader_path)?;
        let descendant = wait_for_pid(&descendant_path)?;

        let (cancelled, cancel_elapsed, call_result, join_elapsed) = thread::scope(|scope| {
            let call = scope.spawn(|| context.call("ctx_read", oversized_arguments()));
            thread::sleep(Duration::from_millis(300));
            let cancel_started = Instant::now();
            let cancelled = context.cancel();
            let cancel_elapsed = cancel_started.elapsed();
            let join_started = Instant::now();
            let call_result = call.join().expect("blocked call thread");
            (
                cancelled,
                cancel_elapsed,
                call_result,
                join_started.elapsed(),
            )
        });
        cancelled?;
        assert!(cancel_elapsed < Duration::from_secs(2));
        assert!(join_elapsed < Duration::from_secs(2));
        let error = call_result.expect_err("a cancelled call must not succeed");
        assert!(
            error
                .downcast_ref::<crate::errors::InstanceCrashed>()
                .is_some(),
            "unexpected error: {error}"
        );
        assert_tree_gone(leader, descendant);
        wait_for_no_writers();
        Ok(())
    }

    #[test]
    fn async_stalled_stdin_write_times_out_and_cancels_within_bounds(
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let _guard = TEST_LOCK
            .get_or_init(|| StdMutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        wait_for_no_writers();

        // Deadline path: `open_context` uses a two-second deadline.
        let root = TempRoot::new()?;
        let leader_path = root.path.join("leader.pid");
        let descendant_path = root.path.join("descendant.pid");
        let engine = stalled_stdin_script(&root, &leader_path, &descendant_path)?;
        let context = open_context(&root, engine)?;
        let leader = wait_for_pid(&leader_path)?;
        let descendant = wait_for_pid(&descendant_path)?;
        let started = Instant::now();
        let error = block_on(context.call("ctx_read", oversized_arguments()))
            .expect_err("a request the instance never reads must not succeed");
        assert!(
            error
                .downcast_ref::<crate::errors::InstanceTimeout>()
                .is_some(),
            "unexpected error: {error}"
        );
        assert!(started.elapsed() < Duration::from_secs(5));
        assert_tree_gone(leader, descendant);
        block_on(context.close())?;

        // Cancellation path: cancel long before the deadline.
        let cancel_root = TempRoot::new()?;
        let cancel_leader_path = cancel_root.path.join("leader.pid");
        let cancel_descendant_path = cancel_root.path.join("descendant.pid");
        let cancel_engine =
            stalled_stdin_script(&cancel_root, &cancel_leader_path, &cancel_descendant_path)?;
        let cancel_context = open_context(&cancel_root, cancel_engine)?;
        let cancel_leader = wait_for_pid(&cancel_leader_path)?;
        let cancel_descendant = wait_for_pid(&cancel_descendant_path)?;
        let mut pending = Box::pin(cancel_context.call("ctx_read", oversized_arguments()));
        assert!(matches!(poll_once(pending.as_mut()), Poll::Pending));
        thread::sleep(Duration::from_millis(300));
        let cancel_started = Instant::now();
        block_on(cancel_context.cancel())?;
        assert!(cancel_started.elapsed() < Duration::from_secs(1));
        assert!(block_on(pending).is_err());
        assert_tree_gone(cancel_leader, cancel_descendant);
        block_on(cancel_context.close())?;

        wait_for_no_writers();
        for _ in 0..200 {
            if worker_count() == 0 {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(worker_count(), 0);
        Ok(())
    }
}
