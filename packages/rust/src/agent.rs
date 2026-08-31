use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::{Map, Value};

use crate::errors::{
    boxed, AgentPermissionError, ConfigurationError, EngineCrashed, EngineExecutionError,
    EngineProtocolError, EngineTimeout, EngineUnavailable, SdkResult, UnsupportedCapabilityError,
    ValidationError,
};
use crate::protocol::{
    canonical_bytes, contained, existing_directory, json_integer, normalize_path,
    strict_json_loads, MAX_PATH_BYTES, MAX_RESPONSE_BYTES, MAX_TEXT_BYTES,
};

pub const AGENT_TOOLS_INTERFACE_VERSION: &str = "1.0.0";
pub const AGENT_TOOLS_SCHEMA_VERSION: u64 = 1;
pub const AGENT_TOOLS_TRANSPORT_VERSION: u64 = 1;
pub const SUPPORTED_AGENT_TOOLS_ENGINE_VERSION: &str = "3.10.1";
const MAX_REQUEST_BYTES: usize = 1024 * 1024;
const MAX_STDERR_BYTES: usize = 64 * 1024;
const MAX_TASK_BYTES: usize = 16 * 1024;

const READ_TOOLS: &[&str] = &[
    "ctx_compose",
    "ctx_glob",
    "ctx_read",
    "ctx_search",
    "ctx_symbol",
    "ctx_tree",
];
const WRITE_TOOLS: &[&str] = &["ctx_edit", "ctx_fill", "ctx_patch"];
const EXEC_TOOLS: &[&str] = &["ctx_shell"];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReadMode {
    Auto,
    Full,
    Raw,
    Signatures,
    Map,
    Diff,
    Reference,
    Task,
    Anchored,
}

impl ReadMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Full => "full",
            Self::Raw => "raw",
            Self::Signatures => "signatures",
            Self::Map => "map",
            Self::Diff => "diff",
            Self::Reference => "reference",
            Self::Task => "task",
            Self::Anchored => "anchored",
        }
    }
}

impl std::fmt::Display for ReadMode {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct AgentPermissions {
    write: bool,
    execute: bool,
}

impl AgentPermissions {
    pub fn new(write: bool, execute: bool) -> Self {
        Self { write, execute }
    }

    pub fn read_only() -> Self {
        Self::default()
    }

    pub fn write_only() -> Self {
        Self {
            write: true,
            execute: false,
        }
    }

    pub fn allow_write(self) -> bool {
        self.write
    }

    pub fn allow_execute(self) -> bool {
        self.execute
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionPolicy {
    max_timeout: Duration,
    allowed_executables: Vec<String>,
    allowed_env: Vec<String>,
}

impl Default for ExecutionPolicy {
    fn default() -> Self {
        Self {
            max_timeout: Duration::from_secs(30),
            allowed_executables: Vec::new(),
            allowed_env: Vec::new(),
        }
    }
}

impl ExecutionPolicy {
    pub fn new<I, E, J, V>(
        max_timeout: Duration,
        allowed_executables: I,
        allowed_env: J,
    ) -> SdkResult<Self>
    where
        I: IntoIterator<Item = E>,
        E: AsRef<str>,
        J: IntoIterator<Item = V>,
        V: AsRef<str>,
    {
        validate_timeout(max_timeout, "max_timeout")?;
        let mut executables = Vec::new();
        for executable in allowed_executables {
            let executable = executable.as_ref();
            if !valid_executable_name(executable) {
                return Err(boxed(ValidationError::new(
                    "allowed_executables must contain executable basenames",
                )));
            }
            executables.push(executable.to_owned());
        }
        let mut environment = Vec::new();
        for name in allowed_env {
            let name = name.as_ref();
            if !valid_env_name(name) {
                return Err(boxed(ValidationError::new(
                    "allowed_env must contain environment variable names",
                )));
            }
            environment.push(name.to_owned());
        }
        executables.sort();
        executables.dedup();
        environment.sort();
        environment.dedup();
        Ok(Self {
            max_timeout,
            allowed_executables: executables,
            allowed_env: environment,
        })
    }

    pub fn max_timeout(&self) -> Duration {
        self.max_timeout
    }

    pub fn allowed_executables(&self) -> &[String] {
        &self.allowed_executables
    }

    pub fn allowed_env(&self) -> &[String] {
        &self.allowed_env
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolResult {
    tool: String,
    text: String,
    content_blocks: Vec<Value>,
    original_tokens: u64,
    output_tokens: u64,
    saved_tokens: u64,
    mode: Option<String>,
    changed: bool,
    shell: Option<Value>,
}

impl ToolResult {
    pub fn tool(&self) -> &str {
        &self.tool
    }

    pub fn text(&self) -> &str {
        &self.text
    }

    pub fn content_blocks(&self) -> &[Value] {
        &self.content_blocks
    }

    pub fn original_tokens(&self) -> u64 {
        self.original_tokens
    }

    pub fn output_tokens(&self) -> u64 {
        self.output_tokens
    }

    pub fn saved_tokens(&self) -> u64 {
        self.saved_tokens
    }

    pub fn mode(&self) -> Option<&str> {
        self.mode.as_deref()
    }

    pub fn changed(&self) -> bool {
        self.changed
    }

    pub fn shell(&self) -> Option<&Value> {
        self.shell.as_ref()
    }

    pub fn saved_ratio(&self) -> f64 {
        if self.original_tokens == 0 {
            0.0
        } else {
            self.saved_tokens.min(self.original_tokens) as f64 / self.original_tokens as f64
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct AgentMetrics {
    tool_calls: u64,
    original_tokens: u64,
    output_tokens: u64,
    saved_tokens: u64,
}

impl AgentMetrics {
    pub fn tool_calls(&self) -> u64 {
        self.tool_calls
    }

    pub fn original_tokens(&self) -> u64 {
        self.original_tokens
    }

    pub fn output_tokens(&self) -> u64 {
        self.output_tokens
    }

    pub fn saved_tokens(&self) -> u64 {
        self.saved_tokens
    }

    pub fn saved_ratio(&self) -> f64 {
        if self.original_tokens == 0 {
            0.0
        } else {
            self.saved_tokens.min(self.original_tokens) as f64 / self.original_tokens as f64
        }
    }
}

#[derive(Debug)]
pub struct AgentContext {
    project_root: PathBuf,
    task: String,
    permissions: AgentPermissions,
    execution_policy: ExecutionPolicy,
    engine_binary: PathBuf,
    timeout: Duration,
    capabilities: Vec<String>,
    state: Arc<AgentState>,
}

#[derive(Debug)]
struct AgentState {
    exchange: Mutex<ExchangeState>,
    process: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    responses: Mutex<mpsc::Receiver<ReaderMessage>>,
    closed: AtomicBool,
    stderr: Arc<Mutex<Vec<u8>>>,
    policy_path: Mutex<Option<PathBuf>>,
}

#[derive(Debug)]
struct ExchangeState {
    next_id: u64,
    metrics: AgentMetrics,
}

#[derive(Debug)]
enum ReaderMessage {
    Line(Vec<u8>),
    End,
    Overflow,
}

impl AgentContext {
    pub fn open(project_root: impl AsRef<Path>) -> SdkResult<Self> {
        Self::open_with_policy(
            project_root,
            "",
            AgentPermissions::default(),
            ExecutionPolicy::default(),
            None,
            Duration::from_secs(30),
        )
    }

    pub fn new(project_root: impl AsRef<Path>) -> SdkResult<Self> {
        Self::open(project_root)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn open_with_policy(
        project_root: impl AsRef<Path>,
        task: impl AsRef<str>,
        permissions: AgentPermissions,
        execution_policy: ExecutionPolicy,
        engine_binary: Option<PathBuf>,
        timeout: Duration,
    ) -> SdkResult<Self> {
        let root = existing_directory(project_root.as_ref())
            .map_err(|_| boxed(ConfigurationError::new("project_root must be a directory")))?;
        let task = task.as_ref();
        if task.len() > MAX_TASK_BYTES || task.contains('\0') {
            return Err(boxed(ValidationError::new("task must be a bounded string")));
        }
        validate_timeout(timeout, "timeout")?;
        if permissions.execute && execution_policy.allowed_executables.is_empty() {
            return Err(boxed(ConfigurationError::new(
                "execute permission requires at least one allowed executable",
            )));
        }
        let binary = resolve_binary(engine_binary.as_deref())?;
        let policy_path = write_policy(&permissions, &execution_policy)?;
        let mut command = Command::new(&binary);
        command
            .arg("engine")
            .arg("tool-session")
            .arg("--project-root")
            .arg(&root)
            .arg("--policy-file")
            .arg(&policy_path)
            .current_dir(&root)
            .env_clear()
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .env("TZ", "UTC")
            .env("PYTHONHASHSEED", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(_) => {
                remove_policy_path(&policy_path);
                return Err(boxed(EngineUnavailable::new(
                    "Agent Tools Engine could not be started",
                )));
            }
        };
        let stdout = child.stdout.take().ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "Agent Tools stdout is unavailable",
            ))
        })?;
        let stderr = child.stderr.take().ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "Agent Tools stderr is unavailable",
            ))
        })?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| boxed(EngineProtocolError::new("Agent Tools stdin is unavailable")))?;
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || read_agent_stdout(stdout, sender));
        let stderr_store = Arc::new(Mutex::new(Vec::new()));
        let stderr_target = Arc::clone(&stderr_store);
        thread::spawn(move || read_agent_stderr(stderr, stderr_target));
        let state = Arc::new(AgentState {
            exchange: Mutex::new(ExchangeState {
                next_id: 0,
                metrics: AgentMetrics::default(),
            }),
            process: Mutex::new(Some(child)),
            stdin: Mutex::new(Some(stdin)),
            responses: Mutex::new(receiver),
            closed: AtomicBool::new(false),
            stderr: stderr_store,
            policy_path: Mutex::new(Some(policy_path)),
        });
        let mut context = Self {
            project_root: root,
            task: task.to_owned(),
            permissions,
            execution_policy,
            engine_binary: binary,
            timeout,
            capabilities: Vec::new(),
            state,
        };
        let hello = context.exchange(hello_request(), timeout, false);
        let hello = match hello {
            Ok(value) => value,
            Err(error) => {
                context.terminate();
                return Err(error);
            }
        };
        let capabilities = match context.accept_hello(&hello) {
            Ok(value) => value,
            Err(error) => {
                context.terminate();
                return Err(error);
            }
        };
        context.capabilities = capabilities;
        context.remove_policy();
        Ok(context)
    }

    pub fn project_root(&self) -> &Path {
        &self.project_root
    }

    pub fn task(&self) -> &str {
        &self.task
    }

    pub fn permissions(&self) -> AgentPermissions {
        self.permissions
    }

    pub fn execution_policy(&self) -> &ExecutionPolicy {
        &self.execution_policy
    }

    pub fn engine_binary(&self) -> &Path {
        &self.engine_binary
    }

    pub fn timeout(&self) -> Duration {
        self.timeout
    }

    pub fn capabilities(&self) -> &[String] {
        &self.capabilities
    }

    pub fn metrics(&self) -> AgentMetrics {
        self.state
            .exchange
            .lock()
            .map(|state| state.metrics)
            .unwrap_or_default()
    }

    pub fn call(&self, tool: &str, arguments: Value) -> SdkResult<ToolResult> {
        if tool.is_empty() {
            return Err(boxed(ValidationError::new(
                "tool must be a non-empty string",
            )));
        }
        if EXEC_TOOLS.contains(&tool) {
            return Err(boxed(AgentPermissionError::new(
                "execution tools must use run()",
            )));
        }
        if WRITE_TOOLS.contains(&tool) && !self.permissions.write {
            return Err(boxed(AgentPermissionError::new(
                "write permission is disabled",
            )));
        }
        self.call_tool(tool, arguments, self.timeout)
    }

    pub fn call_no_args(&self, tool: &str) -> SdkResult<ToolResult> {
        self.call(tool, Value::Object(Map::new()))
    }

    pub fn read(
        &self,
        path: impl AsRef<str>,
        mode: ReadMode,
        fresh: bool,
    ) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert("path".to_owned(), Value::String(path.as_ref().to_owned()));
        arguments.insert("mode".to_owned(), Value::String(mode.to_string()));
        arguments.insert("fresh".to_owned(), Value::Bool(fresh));
        self.call_tool("ctx_read", Value::Object(arguments), self.timeout)
    }

    pub fn read_auto(&self, path: impl AsRef<str>) -> SdkResult<ToolResult> {
        self.read(path, ReadMode::Auto, false)
    }

    pub fn search(
        &self,
        pattern: impl AsRef<str>,
        path: impl AsRef<str>,
        max_results: u64,
        include: Option<String>,
    ) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert("path".to_owned(), Value::String(path.as_ref().to_owned()));
        arguments.insert(
            "pattern".to_owned(),
            Value::String(pattern.as_ref().to_owned()),
        );
        arguments.insert("max_results".to_owned(), Value::from(max_results));
        if let Some(include) = include {
            arguments.insert("include".to_owned(), Value::String(include));
        }
        self.call_tool("ctx_search", Value::Object(arguments), self.timeout)
    }

    pub fn glob(
        &self,
        pattern: impl AsRef<str>,
        path: impl AsRef<str>,
        max_results: u64,
    ) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert("path".to_owned(), Value::String(path.as_ref().to_owned()));
        arguments.insert(
            "pattern".to_owned(),
            Value::String(pattern.as_ref().to_owned()),
        );
        arguments.insert("max_results".to_owned(), Value::from(max_results));
        self.call_tool("ctx_glob", Value::Object(arguments), self.timeout)
    }

    pub fn tree(
        &self,
        path: impl AsRef<str>,
        depth: u64,
        show_hidden: bool,
    ) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert("path".to_owned(), Value::String(path.as_ref().to_owned()));
        arguments.insert("depth".to_owned(), Value::from(depth));
        arguments.insert("show_hidden".to_owned(), Value::Bool(show_hidden));
        self.call_tool("ctx_tree", Value::Object(arguments), self.timeout)
    }

    pub fn compose(&self, task: impl AsRef<str>, path: impl AsRef<str>) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert("path".to_owned(), Value::String(path.as_ref().to_owned()));
        arguments.insert("task".to_owned(), Value::String(task.as_ref().to_owned()));
        self.call_tool("ctx_compose", Value::Object(arguments), self.timeout)
    }

    pub fn symbol(&self, name: impl AsRef<str>) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert("name".to_owned(), Value::String(name.as_ref().to_owned()));
        self.call_tool("ctx_symbol", Value::Object(arguments), self.timeout)
    }

    pub fn patch(
        &self,
        path: impl AsRef<str>,
        operation: impl AsRef<str>,
        arguments: Value,
    ) -> SdkResult<ToolResult> {
        if !self.permissions.write {
            return Err(boxed(AgentPermissionError::new(
                "write permission is disabled",
            )));
        }
        let mut request = arguments
            .as_object()
            .cloned()
            .ok_or_else(|| boxed(ValidationError::new("arguments must be a JSON object")))?;
        request.insert("path".to_owned(), Value::String(path.as_ref().to_owned()));
        request.insert(
            "op".to_owned(),
            Value::String(operation.as_ref().to_owned()),
        );
        self.call_tool("ctx_patch", Value::Object(request), self.timeout)
    }

    pub fn create_file(
        &self,
        path: impl AsRef<str>,
        text: impl AsRef<str>,
    ) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert(
            "new_text".to_owned(),
            Value::String(text.as_ref().to_owned()),
        );
        self.patch(path, "create", Value::Object(arguments))
    }

    pub fn replace_unique(
        &self,
        path: impl AsRef<str>,
        old_text: impl AsRef<str>,
        new_text: impl AsRef<str>,
    ) -> SdkResult<ToolResult> {
        let mut arguments = Map::new();
        arguments.insert(
            "old_text".to_owned(),
            Value::String(old_text.as_ref().to_owned()),
        );
        arguments.insert(
            "new_text".to_owned(),
            Value::String(new_text.as_ref().to_owned()),
        );
        self.patch(path, "replace_unique", Value::Object(arguments))
    }

    pub fn run<I, S>(&self, argv: I) -> SdkResult<ToolResult>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        self.run_with_options(argv, Path::new("."), &Map::new(), None)
    }

    pub fn run_with_options<I, S>(
        &self,
        argv: I,
        cwd: impl AsRef<Path>,
        env: &Map<String, Value>,
        timeout: Option<Duration>,
    ) -> SdkResult<ToolResult>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        if !self.permissions.execute {
            return Err(boxed(AgentPermissionError::new(
                "execute permission is disabled",
            )));
        }
        let argv: Vec<String> = argv
            .into_iter()
            .map(|value| value.as_ref().to_owned())
            .collect();
        if argv.is_empty() || argv.iter().any(String::is_empty) {
            return Err(boxed(ValidationError::new(
                "argv must be a non-empty sequence of strings",
            )));
        }
        let executable = &argv[0];
        if !valid_executable_name(executable)
            || !self
                .execution_policy
                .allowed_executables
                .iter()
                .any(|value| value == executable)
        {
            return Err(boxed(AgentPermissionError::new(format!(
                "executable is not allowed: {executable}"
            ))));
        }
        let selected_timeout = timeout.unwrap_or(self.execution_policy.max_timeout);
        validate_timeout(selected_timeout, "timeout")?;
        if selected_timeout > self.execution_policy.max_timeout {
            return Err(boxed(ValidationError::new(
                "timeout exceeds ExecutionPolicy",
            )));
        }
        let cwd = safe_cwd(&self.project_root, cwd.as_ref())?;
        let mut encoded_env = Map::new();
        for (key, value) in env {
            let value = value
                .as_str()
                .ok_or_else(|| boxed(ValidationError::new("env must be a string mapping")))?;
            if !self
                .execution_policy
                .allowed_env
                .iter()
                .any(|name| name == key)
            {
                return Err(boxed(AgentPermissionError::new(format!(
                    "environment variable is not allowed: {key}"
                ))));
            }
            if value.contains('\0') {
                return Err(boxed(ValidationError::new(
                    "environment variable contains NUL",
                )));
            }
            encoded_env.insert(key.clone(), Value::String(value.to_owned()));
        }
        let mut arguments = Map::new();
        arguments.insert(
            "argv".to_owned(),
            Value::Array(argv.into_iter().map(Value::String).collect()),
        );
        arguments.insert("cwd".to_owned(), Value::String(cwd));
        arguments.insert("env".to_owned(), Value::Object(encoded_env));
        arguments.insert(
            "timeout_ms".to_owned(),
            Value::from(selected_timeout.as_millis() as u64),
        );
        let response_timeout = self
            .timeout
            .max(selected_timeout.saturating_add(Duration::from_secs(2)));
        self.call_tool("ctx_shell", Value::Object(arguments), response_timeout)
    }

    pub fn close(&self) -> SdkResult<()> {
        if self.state.closed.load(Ordering::Acquire) {
            self.terminate();
            return Ok(());
        }
        let _ = self.exchange(close_request(), self.timeout, true);
        self.terminate();
        Ok(())
    }

    pub fn cancel(&self) -> SdkResult<()> {
        self.terminate();
        Ok(())
    }

    pub fn reconnect(&self) -> SdkResult<Self> {
        self.close()?;
        Self::open_with_policy(
            &self.project_root,
            &self.task,
            self.permissions,
            self.execution_policy.clone(),
            Some(self.engine_binary.clone()),
            self.timeout,
        )
    }

    fn call_tool(&self, tool: &str, arguments: Value, timeout: Duration) -> SdkResult<ToolResult> {
        if !self.capabilities.iter().any(|value| value == tool) {
            return Err(boxed(UnsupportedCapabilityError::new(format!(
                "Engine did not negotiate capability: {tool}"
            ))));
        }
        if !arguments.is_object() {
            return Err(boxed(ValidationError::new(
                "arguments must be a JSON object",
            )));
        }
        if canonical_bytes(&arguments).map_err(boxed)?.len() > MAX_REQUEST_BYTES {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools request exceeds its bound",
            )));
        }
        let mut request = Map::new();
        request.insert("op".to_owned(), Value::String("call".to_owned()));
        request.insert("tool".to_owned(), Value::String(tool.to_owned()));
        request.insert("arguments".to_owned(), arguments);
        let result = self.exchange(Value::Object(request), timeout, false)?;
        let parsed = parse_tool_result(tool, &result)?;
        if let Ok(mut state) = self.state.exchange.lock() {
            state.metrics.tool_calls = state.metrics.tool_calls.saturating_add(1);
            state.metrics.original_tokens = state
                .metrics
                .original_tokens
                .saturating_add(parsed.original_tokens);
            state.metrics.output_tokens = state
                .metrics
                .output_tokens
                .saturating_add(parsed.output_tokens);
            state.metrics.saved_tokens = state
                .metrics
                .saved_tokens
                .saturating_add(parsed.saved_tokens);
        }
        Ok(parsed)
    }

    fn exchange(&self, request: Value, timeout: Duration, allow_closed: bool) -> SdkResult<Value> {
        if !allow_closed && self.state.closed.load(Ordering::Acquire) {
            return Err(boxed(EngineCrashed::new("AgentContext is closed")));
        }
        let mut exchange = self
            .state
            .exchange
            .lock()
            .map_err(|_| boxed(EngineCrashed::new("AgentContext state is poisoned")))?;
        if !allow_closed && self.state.closed.load(Ordering::Acquire) {
            return Err(boxed(EngineCrashed::new("AgentContext is closed")));
        }
        exchange.next_id = exchange.next_id.saturating_add(1);
        let id = exchange.next_id.to_string();
        let mut envelope = request
            .as_object()
            .cloned()
            .ok_or_else(|| boxed(ValidationError::new("Agent Tools request is not an object")))?;
        envelope.insert("id".to_owned(), Value::String(id.clone()));
        let encoded = canonical_bytes(&Value::Object(envelope))
            .map_err(boxed)?
            .into_iter()
            .chain(std::iter::once(b'\n'))
            .collect::<Vec<_>>();
        if encoded.len() > MAX_REQUEST_BYTES {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools request exceeds its bound",
            )));
        }
        let write_result = {
            let mut stdin = self
                .state
                .stdin
                .lock()
                .map_err(|_| boxed(EngineCrashed::new("AgentContext stdin is poisoned")))?;
            let Some(stdin) = stdin.as_mut() else {
                return Err(boxed(EngineCrashed::new("Agent Tools Engine exited")));
            };
            stdin.write_all(&encoded).and_then(|_| stdin.flush())
        };
        if write_result.is_err() {
            self.terminate();
            return Err(boxed(EngineCrashed::new(self.crash_message())));
        }
        let message = self
            .state
            .responses
            .lock()
            .map_err(|_| {
                boxed(EngineCrashed::new(
                    "AgentContext response state is poisoned",
                ))
            })?
            .recv_timeout(timeout);
        let raw = match message {
            Ok(ReaderMessage::Line(raw)) => raw,
            Ok(ReaderMessage::Overflow) => {
                drop(exchange);
                self.protocol_failure("Agent Tools response exceeds its bound")?;
                unreachable!();
            }
            Ok(ReaderMessage::End) | Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(boxed(EngineCrashed::new(self.crash_message())));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                drop(exchange);
                self.terminate();
                return Err(boxed(EngineTimeout::new(
                    "Agent Tools response exceeded its deadline",
                )));
            }
        };
        let response = match strict_json_loads(&raw, "Agent Tools response") {
            Ok(value) => value,
            Err(_) => {
                drop(exchange);
                self.protocol_failure("Agent Tools response is invalid JSON")?;
                unreachable!();
            }
        };
        let Some(response_object) = response.as_object() else {
            drop(exchange);
            self.protocol_failure("Agent Tools response envelope is invalid")?;
            unreachable!();
        };
        if response_object.get("id").and_then(Value::as_str) != Some(id.as_str())
            || response_object.get("ok").and_then(Value::as_bool).is_none()
        {
            drop(exchange);
            self.protocol_failure("Agent Tools response envelope is invalid")?;
            unreachable!();
        }
        let ok = response_object
            .get("ok")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if ok {
            if response_object.len() != 3
                || !response_object.contains_key("result")
                || !response_object["result"].is_object()
            {
                drop(exchange);
                self.protocol_failure("Agent Tools response omitted result")?;
                unreachable!();
            }
            return Ok(response_object["result"].clone());
        }
        if response_object.len() != 3
            || !response_object.contains_key("error")
            || !response_object["error"].is_object()
        {
            drop(exchange);
            self.protocol_failure("Agent Tools error envelope is invalid")?;
            unreachable!();
        }
        let error = &response_object["error"];
        let Some(error_object) = error.as_object() else {
            drop(exchange);
            self.protocol_failure("Agent Tools error envelope is invalid")?;
            unreachable!();
        };
        if error_object.len() != 2
            || error_object.get("code").and_then(Value::as_str).is_none()
            || error_object
                .get("message")
                .and_then(Value::as_str)
                .is_none()
        {
            drop(exchange);
            self.protocol_failure("Agent Tools error envelope is invalid")?;
            unreachable!();
        }
        let code = error_object["code"].as_str().unwrap_or_default();
        let message = error_object["message"].as_str().unwrap_or_default();
        Err(map_agent_error(code, message))
    }

    fn accept_hello(&self, result: &Value) -> SdkResult<Vec<String>> {
        let object = result.as_object().ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "Agent Tools hello is incompatible",
            ))
        })?;
        let expected = [
            "agent_tools_interface_version",
            "allow_exec",
            "allow_write",
            "capabilities",
            "engine_version",
            "schema_version",
            "transport_version",
        ];
        if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools hello is incompatible",
            )));
        }
        if object
            .get("agent_tools_interface_version")
            .and_then(Value::as_str)
            != Some(AGENT_TOOLS_INTERFACE_VERSION)
            || json_integer(
                object.get("schema_version").unwrap_or(&Value::Null),
                "hello.schema_version",
            )? != AGENT_TOOLS_SCHEMA_VERSION
            || json_integer(
                object.get("transport_version").unwrap_or(&Value::Null),
                "hello.transport_version",
            )? != AGENT_TOOLS_TRANSPORT_VERSION
            || object.get("engine_version").and_then(Value::as_str)
                != Some(SUPPORTED_AGENT_TOOLS_ENGINE_VERSION)
            || object.get("allow_write").and_then(Value::as_bool) != Some(self.permissions.write)
            || object.get("allow_exec").and_then(Value::as_bool) != Some(self.permissions.execute)
        {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools hello is incompatible",
            )));
        }
        let values = object
            .get("capabilities")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                boxed(EngineProtocolError::new(
                    "Agent Tools capabilities are invalid",
                ))
            })?;
        let mut capabilities = Vec::with_capacity(values.len());
        for value in values {
            capabilities.push(
                value
                    .as_str()
                    .ok_or_else(|| {
                        boxed(EngineProtocolError::new(
                            "Agent Tools capabilities are invalid",
                        ))
                    })?
                    .to_owned(),
            );
        }
        let sorted = {
            let mut copy = capabilities.clone();
            copy.sort();
            copy
        };
        if capabilities != sorted || capabilities.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools capabilities are not canonical",
            )));
        }
        let mut expected_capabilities: Vec<String> =
            READ_TOOLS.iter().map(|value| (*value).to_owned()).collect();
        if self.permissions.write {
            expected_capabilities.extend(WRITE_TOOLS.iter().map(|value| (*value).to_owned()));
        }
        if self.permissions.execute {
            expected_capabilities.extend(EXEC_TOOLS.iter().map(|value| (*value).to_owned()));
        }
        expected_capabilities.sort();
        if capabilities != expected_capabilities {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools capabilities do not match policy",
            )));
        }
        Ok(capabilities)
    }

    fn remove_policy(&self) {
        if let Ok(mut path) = self.state.policy_path.lock() {
            if let Some(path) = path.take() {
                remove_policy_path(&path);
            }
        }
    }

    fn terminate(&self) {
        if self.state.closed.swap(true, Ordering::AcqRel) {
            self.remove_policy();
            return;
        }
        if let Ok(mut stdin) = self.state.stdin.lock() {
            stdin.take();
        }
        if let Ok(mut process) = self.state.process.lock() {
            if let Some(mut child) = process.take() {
                kill_process_tree(&mut child);
            }
        }
        self.remove_policy();
    }

    fn protocol_failure(&self, message: &str) -> SdkResult<()> {
        self.terminate();
        Err(boxed(EngineProtocolError::new(message)))
    }

    fn crash_message(&self) -> String {
        let detail = self
            .state
            .stderr
            .lock()
            .map(|value| String::from_utf8_lossy(&value).trim().to_owned())
            .unwrap_or_default();
        if detail.is_empty() {
            "Agent Tools Engine exited".to_owned()
        } else {
            format!(
                "Agent Tools Engine exited: {}",
                detail.chars().take(4096).collect::<String>()
            )
        }
    }
}

impl Drop for AgentContext {
    fn drop(&mut self) {
        self.terminate();
    }
}

#[derive(Debug)]
pub struct AsyncAgentContext {
    context: AgentContext,
}

impl AsyncAgentContext {
    pub async fn open(project_root: impl AsRef<Path>) -> SdkResult<Self> {
        Ok(Self {
            context: AgentContext::open(project_root)?,
        })
    }

    pub fn from_context(context: AgentContext) -> Self {
        Self { context }
    }

    pub fn context(&self) -> &AgentContext {
        &self.context
    }

    pub fn capabilities(&self) -> &[String] {
        self.context.capabilities()
    }

    pub fn metrics(&self) -> AgentMetrics {
        self.context.metrics()
    }

    pub async fn call(&self, tool: &str, arguments: Value) -> SdkResult<ToolResult> {
        self.context.call(tool, arguments)
    }

    pub async fn read(
        &self,
        path: impl AsRef<str>,
        mode: ReadMode,
        fresh: bool,
    ) -> SdkResult<ToolResult> {
        self.context.read(path, mode, fresh)
    }

    pub async fn run<I, S>(&self, argv: I) -> SdkResult<ToolResult>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        self.context.run(argv)
    }

    pub async fn cancel(&self) -> SdkResult<()> {
        self.context.cancel()
    }

    pub async fn close(&self) -> SdkResult<()> {
        self.context.close()
    }

    pub async fn reconnect(&self) -> SdkResult<Self> {
        Ok(Self {
            context: self.context.reconnect()?,
        })
    }
}

fn hello_request() -> Value {
    let mut request = Map::new();
    request.insert("op".to_owned(), Value::String("hello".to_owned()));
    request.insert(
        "schema_version".to_owned(),
        Value::from(AGENT_TOOLS_SCHEMA_VERSION),
    );
    request.insert(
        "transport_version".to_owned(),
        Value::from(AGENT_TOOLS_TRANSPORT_VERSION),
    );
    request.insert(
        "agent_tools_interface_version".to_owned(),
        Value::String(AGENT_TOOLS_INTERFACE_VERSION.to_owned()),
    );
    request.insert("sdk_version".to_owned(), Value::String("1.1.0".to_owned()));
    Value::Object(request)
}

fn close_request() -> Value {
    let mut request = Map::new();
    request.insert("op".to_owned(), Value::String("close".to_owned()));
    Value::Object(request)
}

fn parse_tool_result(tool: &str, value: &Value) -> SdkResult<ToolResult> {
    let object = value.as_object().ok_or_else(|| {
        boxed(EngineProtocolError::new(
            "Agent Tools result is not an object",
        ))
    })?;
    let expected = [
        "text",
        "content_blocks",
        "original_tokens",
        "output_tokens",
        "saved_tokens",
        "mode",
        "changed",
        "shell",
    ];
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err(boxed(EngineProtocolError::new(
            "Agent Tools result fields are invalid",
        )));
    }
    let text = object.get("text").and_then(Value::as_str).ok_or_else(|| {
        boxed(EngineProtocolError::new(
            "Agent Tools text or mode is invalid",
        ))
    })?;
    if text.len() > MAX_TEXT_BYTES {
        return Err(boxed(EngineProtocolError::new(
            "Agent Tools text or mode is invalid",
        )));
    }
    let mode = match object.get("mode") {
        Some(Value::Null) => None,
        Some(Value::String(value)) => Some(value.clone()),
        _ => {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools text or mode is invalid",
            )))
        }
    };
    let original_tokens = json_integer(
        object.get("original_tokens").unwrap_or(&Value::Null),
        "result.original_tokens",
    )?;
    let output_tokens = json_integer(
        object.get("output_tokens").unwrap_or(&Value::Null),
        "result.output_tokens",
    )?;
    let saved_tokens = json_integer(
        object.get("saved_tokens").unwrap_or(&Value::Null),
        "result.saved_tokens",
    )?;
    if output_tokens
        .checked_add(saved_tokens)
        .filter(|value| *value == original_tokens)
        .is_none()
    {
        return Err(boxed(EngineProtocolError::new(
            "Agent Tools token metrics are inconsistent",
        )));
    }
    let changed = object
        .get("changed")
        .and_then(Value::as_bool)
        .ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "Agent Tools status metadata is invalid",
            ))
        })?;
    let shell = match object.get("shell") {
        Some(Value::Null) => None,
        Some(Value::Object(value)) => Some(Value::Object(value.clone())),
        _ => {
            return Err(boxed(EngineProtocolError::new(
                "Agent Tools status metadata is invalid",
            )))
        }
    };
    let content_blocks = object
        .get("content_blocks")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            boxed(EngineProtocolError::new(
                "Agent Tools content blocks are invalid",
            ))
        })?;
    if content_blocks.iter().any(|value| !value.is_object()) {
        return Err(boxed(EngineProtocolError::new(
            "Agent Tools content blocks are invalid",
        )));
    }
    Ok(ToolResult {
        tool: tool.to_owned(),
        text: text.to_owned(),
        content_blocks: content_blocks.clone(),
        original_tokens,
        output_tokens,
        saved_tokens,
        mode,
        changed,
        shell,
    })
}

fn map_agent_error(code: &str, message: &str) -> Box<dyn std::error::Error + Send + Sync> {
    match code {
        "permission_denied" => boxed(crate::errors::AgentPermissionError::new(message)),
        "unsupported_capability" => boxed(UnsupportedCapabilityError::new(message)),
        "invalid_request" | "invalid_state" | "unsupported_interface" => {
            boxed(EngineProtocolError::new(message))
        }
        _ => boxed(EngineExecutionError::new(message)),
    }
}

fn read_agent_stdout<R: Read>(mut reader: R, sender: mpsc::Sender<ReaderMessage>) {
    let mut pending = Vec::new();
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        let size = match reader.read(&mut chunk) {
            Ok(0) => {
                let _ = sender.send(ReaderMessage::End);
                return;
            }
            Ok(size) => size,
            Err(_) => {
                let _ = sender.send(ReaderMessage::End);
                return;
            }
        };
        pending.extend_from_slice(&chunk[..size]);
        if pending.len() > MAX_RESPONSE_BYTES + 1 {
            let _ = sender.send(ReaderMessage::Overflow);
            return;
        }
        while let Some(position) = pending.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = pending.drain(..position).collect();
            pending.drain(..1);
            if line.len() > MAX_RESPONSE_BYTES {
                let _ = sender.send(ReaderMessage::Overflow);
                return;
            }
            if sender.send(ReaderMessage::Line(line)).is_err() {
                return;
            }
        }
    }
}

fn read_agent_stderr<R: Read>(mut reader: R, target: Arc<Mutex<Vec<u8>>>) {
    let mut chunk = [0_u8; 8192];
    loop {
        match reader.read(&mut chunk) {
            Ok(0) | Err(_) => return,
            Ok(size) => {
                if let Ok(mut target) = target.lock() {
                    let available = MAX_STDERR_BYTES.saturating_sub(target.len());
                    target.extend_from_slice(&chunk[..size.min(available)]);
                }
            }
        }
    }
}

fn write_policy(
    permissions: &AgentPermissions,
    execution_policy: &ExecutionPolicy,
) -> SdkResult<PathBuf> {
    static POLICY_COUNTER: AtomicU64 = AtomicU64::new(0);
    for _ in 0..100 {
        let counter = POLICY_COUNTER.fetch_add(1, Ordering::Relaxed);
        let directory =
            std::env::temp_dir().join(format!("leanctx-agent-{}-{counter}", std::process::id()));
        match fs::create_dir(&directory) {
            Ok(()) => {
                set_private_directory(&directory);
                let path = directory.join("policy.json");
                let mut options = OpenOptions::new();
                options.write(true).create_new(true);
                set_private_file(&mut options);
                let mut file = options.open(&path).map_err(|_| {
                    boxed(EngineUnavailable::new(
                        "Agent Tools policy could not be created",
                    ))
                })?;
                let mut object = Map::new();
                object.insert("allow_exec".to_owned(), Value::Bool(permissions.execute));
                object.insert("allow_write".to_owned(), Value::Bool(permissions.write));
                object.insert(
                    "allowed_env".to_owned(),
                    Value::Array(
                        execution_policy
                            .allowed_env
                            .iter()
                            .cloned()
                            .map(Value::String)
                            .collect(),
                    ),
                );
                object.insert(
                    "allowed_executables".to_owned(),
                    Value::Array(
                        execution_policy
                            .allowed_executables
                            .iter()
                            .cloned()
                            .map(Value::String)
                            .collect(),
                    ),
                );
                object.insert(
                    "max_timeout_ms".to_owned(),
                    Value::from(execution_policy.max_timeout.as_millis() as u64),
                );
                object.insert(
                    "schema_version".to_owned(),
                    Value::from(AGENT_TOOLS_SCHEMA_VERSION),
                );
                let payload = canonical_bytes(&Value::Object(object)).map_err(boxed)?;
                if let Err(error) = file.write_all(&payload).and_then(|_| file.sync_all()) {
                    remove_policy_path(&path);
                    return Err(boxed(EngineUnavailable::new(format!(
                        "Agent Tools policy could not be written: {error}"
                    ))));
                }
                return Ok(path);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => {
                return Err(boxed(EngineUnavailable::new(
                    "Agent Tools policy directory could not be created",
                )))
            }
        }
    }
    Err(boxed(EngineUnavailable::new(
        "Agent Tools policy directory could not be created",
    )))
}

fn remove_policy_path(path: &Path) {
    let _ = fs::remove_file(path);
    if let Some(directory) = path.parent() {
        let _ = fs::remove_dir(directory);
    }
}

fn resolve_binary(requested: Option<&Path>) -> SdkResult<PathBuf> {
    let requested = requested.unwrap_or_else(|| Path::new("lean-ctx"));
    let text = requested.to_string_lossy();
    let has_separator = text.bytes().any(|byte| byte == b'/' || byte == b'\\');
    let candidate = if requested.is_absolute() || has_separator {
        fs::canonicalize(requested).map_err(|_| {
            boxed(EngineUnavailable::new(
                "configured Engine binary is unavailable",
            ))
        })?
    } else {
        let mut result = None;
        for entry in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()) {
            if entry.as_os_str().is_empty() {
                continue;
            }
            let candidate = entry.join(requested);
            if candidate.is_file() {
                result = fs::canonicalize(candidate).ok();
                if result.is_some() {
                    break;
                }
            }
        }
        result.ok_or_else(|| {
            boxed(EngineUnavailable::new(
                "configured Engine binary is unavailable",
            ))
        })?
    };
    if !candidate.is_file() || !is_executable(&candidate) {
        return Err(boxed(EngineUnavailable::new(
            "configured Engine binary is unavailable",
        )));
    }
    Ok(candidate)
}

fn valid_executable_name(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'+' | b'-'))
}

fn valid_env_name(value: &str) -> bool {
    let forbidden = [
        "COMSPEC",
        "DYLD_INSERT_LIBRARIES",
        "HOME",
        "LD_PRELOAD",
        "PATH",
        "PATHEXT",
        "PYTHONPATH",
        "RUSTC_WRAPPER",
        "SHELL",
    ];
    !value.is_empty()
        && (value.as_bytes()[0].is_ascii_alphabetic() || value.as_bytes()[0] == b'_')
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        && !forbidden.contains(&value.to_ascii_uppercase().as_str())
}

fn validate_timeout(timeout: Duration, field_name: &str) -> SdkResult<()> {
    if timeout < Duration::from_millis(100) || timeout > Duration::from_secs(120) {
        return Err(boxed(ValidationError::new(format!(
            "{field_name} must be between 0.1 and 120 seconds"
        ))));
    }
    Ok(())
}

fn safe_cwd(root: &Path, cwd: &Path) -> SdkResult<String> {
    let text = cwd
        .to_str()
        .ok_or_else(|| boxed(ValidationError::new("cwd is not valid UTF-8")))?;
    if text.is_empty()
        || text.len() > MAX_PATH_BYTES
        || text.contains('\0')
        || text.chars().any(|character| character < '\u{20}')
    {
        return Err(boxed(ValidationError::new("cwd violates the path bound")));
    }
    let absolute = normalize_path(
        if cwd.is_absolute() {
            cwd.to_owned()
        } else {
            root.join(cwd)
        }
        .as_path(),
    );
    if !contained(&absolute, root) {
        return Err(boxed(AgentPermissionError::new("cwd escapes project root")));
    }
    let relative = absolute
        .strip_prefix(root)
        .map_err(|_| boxed(AgentPermissionError::new("cwd escapes project root")))?;
    if relative.as_os_str().is_empty() {
        Ok(".".to_owned())
    } else {
        Ok(relative.to_string_lossy().replace('\\', "/"))
    }
}

fn set_private_directory(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o700));
    }
}

fn set_private_file(options: &mut OpenOptions) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
}

fn kill_process_tree(child: &mut Child) {
    #[cfg(unix)]
    {
        if let Some(pid) = NonZeroPid::from_child(child) {
            let _ = Command::new("/bin/kill")
                .arg("-KILL")
                .arg(format!("-{pid}"))
                .status();
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

struct NonZeroPid(u32);

impl NonZeroPid {
    fn from_child(child: &Child) -> Option<Self> {
        let pid = child.id();
        (pid > 0).then_some(Self(pid))
    }
}

impl std::fmt::Display for NonZeroPid {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(formatter)
    }
}

fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::metadata(path)
            .map(|metadata| metadata.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}
