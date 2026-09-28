// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

class AgentToolsTest {
    @Test
    void policyIsImmutableSortedAndFailClosedByDefault() {
        AgentPermissions permissions = new AgentPermissions();
        assertTrue(!permissions.write());
        assertTrue(!permissions.execute());
        ExecutionPolicy policy = new ExecutionPolicy(2.5,
                List.of("zeta", "java", "java"), List.of("ZZ", "LANG", "ZZ"));
        assertEquals(List.of("java", "zeta"), policy.allowedExecutables());
        assertEquals(List.of("LANG", "ZZ"), policy.allowedEnv());
        assertThrows(ValidationError.class, () -> new ExecutionPolicy(30,
                List.of("/bin/sh"), List.of()));
        assertThrows(ValidationError.class, () -> new ExecutionPolicy(30,
                List.of(), List.of("PATH")));
    }

    @Test
    void persistentClientNegotiatesCapabilitiesCallsToolsAndCleansPolicy() throws Exception {
        Path root = Files.createTempDirectory("leanctx-agent-test-");
        Path binary = fakeAgent(root, false, false);
        ExecutionPolicy policy = new ExecutionPolicy(2.0, List.of("printf"), List.of("LANG"));
        AgentContext context = new AgentContext(root.toString(), "inspect",
                new AgentPermissions(false, true), policy, binary.toString(), 2.0);
        try {
            assertTrue(!Files.readString(root.resolve("received-policy.json"))
                    .contains("selected_gitlab"));
            assertEquals(List.of("ctx_compose", "ctx_glob", "ctx_read", "ctx_search",
                    "ctx_shell", "ctx_symbol", "ctx_tree"), context.capabilities());
            ToolResult read = context.read("README.md", ReadMode.AUTO, false);
            assertEquals("ctx_read:ok", read.text());
            ToolResult run = context.run(List.of("printf", "ok"), ".", Map.of("LANG", "C"), 0.5);
            assertEquals("ctx_shell:ok", run.text());
            assertEquals(2, context.metrics().toolCalls());
            assertEquals(20, context.metrics().originalTokens());
            assertEquals(12, context.metrics().savedTokens());
            assertThrows(AgentPermissionError.class,
                    () -> context.run(List.of("sh"), ".", Map.of(), 0.5));
            Path outside = Files.createTempDirectory("leanctx-agent-outside-");
            try {
                Files.createSymbolicLink(root.resolve("escape-link"), outside);
                assertThrows(AgentPermissionError.class,
                        () -> context.run(List.of("printf"), "escape-link", Map.of(), 0.5));
            } finally {
                deleteTree(outside);
            }
        } finally {
            context.close();
        }
        assertNoAgentState(root);
        deleteTree(root);
    }

    @Test
    void gitLabSourceIsValidatedSerializedAndNegotiatesProviderCapability() throws Exception {
        Path glab = Path.of(System.getProperty("java.io.tmpdir"), "glab");
        Path configDir = Path.of(System.getProperty("java.io.tmpdir"), "glab-config");
        GitLabSource source = new GitLabSource("gitlab.example", 17, "group/project", glab, configDir);
        assertThrows(ValidationError.class,
                () -> new GitLabSource("gitlab.example\n", 17, "group/project", glab));
        assertThrows(ValidationError.class,
                () -> new GitLabSource("gitlab.example", 0, "group/project", glab));
        assertThrows(ValidationError.class,
                () -> new GitLabSource("gitlab.example", 17, "group/project", Path.of("glab")));

        Path root = Files.createTempDirectory("leanctx-agent-gitlab-");
        Path missingProvider = fakeAgent(root, false, false, false, true, false);
        AgentPermissions executable = new AgentPermissions(false, true);
        ExecutionPolicy executionPolicy = new ExecutionPolicy(2.0, List.of("printf"), List.of());
        assertThrows(EngineProtocolError.class, () -> new AgentContext(root.toString(), "inspect",
                executable, executionPolicy, missingProvider.toString(), 2.0, source));

        Path binary = fakeAgent(root, false, false, false, true, true);
        AgentContext context = new AgentContext(root.toString(), "inspect",
                executable, executionPolicy, binary.toString(), 2.0, source);
        try {
            assertTrue(context.capabilities().contains("ctx_provider"));
            assertEquals(source, context.gitlabSource());
            assertEquals("ctx_read:ok", context.call("ctx_provider", Map.of(
                    "action", "query", "provider", "gitlab", "resource", "issues",
                    "mode", "snapshot", "project", 17, "limit", 1)).text());
            String policy = Files.readString(root.resolve("received-policy.json"));
            assertTrue(policy.contains("\"selected_gitlab\":{")
                    && policy.contains("\"host\":\"gitlab.example\"")
                    && policy.contains("\"project\":17")
                    && policy.contains("\"namespace\":\"group/project\"")
                    && policy.contains("\"glab\":\"" + glab + "\"")
                    && policy.contains("\"config_dir\":\"" + configDir + "\""));
        } finally {
            context.close();
            deleteTree(root);
        }
        Path asyncRoot = Files.createTempDirectory("leanctx-agent-gitlab-async-");
        try {
            Path asyncBinary = fakeAgent(asyncRoot, false, false, false, true, true);
            try (AsyncAgentContext async = new AsyncAgentContext(asyncRoot.toString(), "inspect",
                    executable, executionPolicy, asyncBinary.toString(), 2.0, source).open().join()) {
                assertTrue(async.capabilities().contains("ctx_provider"));
                assertEquals(source, async.gitlabSource());
                try (AsyncAgentContext reconnected = async.reconnect().join()) {
                    assertTrue(reconnected.capabilities().contains("ctx_provider"));
                }
            }
        } finally {
            deleteTree(asyncRoot);
        }
    }

    @Test
    void incompatibleHelloIsTerminalAndRemovesTemporaryPolicy() throws Exception {
        Path root = Files.createTempDirectory("leanctx-agent-bad-hello-");
        Path binary = fakeAgent(root, true, false);
        assertThrows(EngineProtocolError.class, () -> new AgentContext(root.toString(), "",
                new AgentPermissions(), new ExecutionPolicy(), binary.toString(), 1.0));
        assertNoAgentState(root);
        deleteTree(root);
    }

    @Test
    void timeoutTerminatesPersistentSession() throws Exception {
        Path root = Files.createTempDirectory("leanctx-agent-timeout-");
        Path binary = fakeAgent(root, false, true);
        AgentContext context = null;
        try {
            // This deadline covers hello too: allow process startup, then time out
            // the fixture's five-second call. Keep constructor failures phase-specific.
            context = assertDoesNotThrow(() -> new AgentContext(root.toString(), "",
                    new AgentPermissions(), new ExecutionPolicy(), binary.toString(), 2.0));
            AgentContext connected = context;
            assertThrows(EngineTimeout.class, () -> connected.call("ctx_read", Map.of()));
            assertThrows(EngineCrashed.class, () -> connected.call("ctx_read", Map.of()));
        } finally {
            if (context != null) {
                context.close();
            }
            assertNoAgentState(root);
            deleteTree(root);
        }
    }

    @TestFactory
    Stream<DynamicTest> cancellingConvenienceFutureTerminatesTheActualSession() {
        Map<String, Function<AsyncAgentContext, CompletableFuture<ToolResult>>> operations = Map.of(
                "search", tools -> tools.search("text", ".", 3, "*.md"),
                "glob", tools -> tools.glob("*.md", ".", 3),
                "tree", tools -> tools.tree(".", 2, false),
                "compose", tools -> tools.compose("inspect", "."),
                "symbol", tools -> tools.symbol("Context"),
                "patch", tools -> tools.patch(Map.of("path", "README.md", "op", "create", "new_text", "x")));
        return operations.entrySet().stream().sorted(Map.Entry.comparingByKey()).map(operation ->
                DynamicTest.dynamicTest(operation.getKey(), () -> {
                    Path root = Files.createTempDirectory("leanctx-agent-cancel-");
                    Path binary = fakeAgent(root, false, true, true);
                    try (AsyncAgentContext tools = new AsyncAgentContext(root.toString(), "inspect",
                            new AgentPermissions(true, false), new ExecutionPolicy(),
                            binary.toString(), 10.0).open().join()) {
                        CompletableFuture<ToolResult> result = operation.getValue().apply(tools);
                        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
                        while (!Files.exists(root.resolve("call-seen")) && System.nanoTime() < deadline) {
                            Thread.sleep(10);
                        }
                        assertTrue(Files.exists(root.resolve("call-seen")), "call reached the process");
                        ProcessHandle engine = ProcessHandle.of(Long.parseLong(
                                Files.readString(root.resolve("engine.pid")))).orElseThrow();
                        ProcessHandle child = ProcessHandle.of(Long.parseLong(
                                Files.readString(root.resolve("call-child.pid")))).orElseThrow();
                        assertTrue(engine.isAlive());
                        assertTrue(child.isAlive());
                        assertTrue(result.cancel(true));
                        assertTrue(result.isCancelled());
                        assertTrue(!engine.isAlive(), "cancellation reaps the Engine process");
                        assertTrue(!child.isAlive(), "cancellation reaps the child process");
                        assertNoAgentState(root);
                        CompletionException failure = assertThrows(CompletionException.class,
                                () -> tools.read("README.md", ReadMode.AUTO, false).join());
                        assertTrue(failure.getCause() instanceof EngineCrashed);
                    } finally {
                        deleteTree(root);
                    }
                }));
    }

    @Test
    void convenienceValidationAndWriteDenialRemainFailedFuturesBeforeDispatch() throws Exception {
        Path root = Files.createTempDirectory("leanctx-agent-validation-");
        Path binary = fakeAgent(root, false, true, false);
        try (AsyncAgentContext tools = new AsyncAgentContext(root.toString(), "inspect",
                new AgentPermissions(), new ExecutionPolicy(), binary.toString(), 2.0).open().join()) {
            for (CompletableFuture<ToolResult> invalid : List.of(
                    tools.search(null, ".", 1, null), tools.glob("*.md", ".", -1),
                    tools.tree(".", -1, false), tools.compose(null, "."), tools.symbol(null))) {
                CompletionException failure = assertThrows(CompletionException.class, invalid::join);
                assertTrue(failure.getCause() instanceof ValidationError);
            }
            CompletionException failure = assertThrows(CompletionException.class,
                    () -> tools.patch(Map.of()).join());
            assertTrue(failure.getCause() instanceof AgentPermissionError);
            assertEquals(0, tools.metrics().toolCalls());
            assertTrue(!Files.exists(root.resolve("call-seen")), "invalid calls never dispatch");
        } finally {
            deleteTree(root);
        }
    }

    private static Path fakeAgent(Path root, boolean badHello, boolean delayCall) throws Exception {
        return fakeAgent(root, badHello, delayCall, false, false, false);
    }

    private static Path fakeAgent(Path root, boolean badHello, boolean delayCall,
                                  boolean allowWrite) throws Exception {
        return fakeAgent(root, badHello, delayCall, allowWrite, true, false);
    }

    private static Path fakeAgent(Path root, boolean badHello, boolean delayCall,
                                  boolean allowWrite, boolean observe) throws Exception {
        return fakeAgent(root, badHello, delayCall, allowWrite, observe, false);
    }

    private static Path fakeAgent(Path root, boolean badHello, boolean delayCall,
                                  boolean allowWrite, boolean observe,
                                  boolean selectedGitlab) throws Exception {
        Path script = root.resolve("fake-agent-" + UUID.randomUUID());
        boolean allowExec = !delayCall;
        List<String> tools = new ArrayList<>(List.of("ctx_compose", "ctx_glob", "ctx_read",
                "ctx_search", "ctx_symbol", "ctx_tree"));
        if (allowExec) {
            tools.add("ctx_shell");
        }
        if (allowWrite) {
            tools.addAll(List.of("ctx_edit", "ctx_fill", "ctx_patch"));
        }
        if (selectedGitlab) {
            tools.add("ctx_provider");
        }
        Collections.sort(tools);
        String capabilities = badHello ? "[\"ctx_read\"]"
                : tools.stream().map(tool -> "\"" + tool + "\"").collect(Collectors.joining(",", "[", "]"));
        String delay = delayCall ? (observe
                ? "sleep 5 &\nprintf '%s' \"$!\" > call-child.pid\nprintf 'seen' > call-seen\nwait \"$!\"\n"
                : "sleep 5\n") : "";
        String source = "#!/bin/sh\n"
                + (observe ? "printf '%s' \"$$\" > engine.pid\n" : "")
                + "policy=''\n"
                + "while [ \"$#\" -gt 0 ]; do\n"
                + "  if [ \"$1\" = \"--policy-file\" ]; then policy=\"$2\"; shift; fi\n"
                + "  shift\n"
                + "done\n"
                + "[ -f \"$policy\" ] || exit 17\n"
                + "IFS= read -r policy_json < \"$policy\"\n"
                + "printf '%s\\n' \"$policy_json\" > received-policy.json\n"
                + "id=0\n"
                + "while IFS= read -r line; do\n"
                + "  id=$((id + 1))\n"
                + "  case \"$line\" in\n"
                + "    *hello*) printf '%s\\n' '{\"id\":\"'\"$id\"'\",\"ok\":true,\"result\":{\"agent_tools_interface_version\":\"1.0.0\",\"allow_exec\":" + allowExec + ",\"allow_write\":" + allowWrite + ",\"capabilities\":" + capabilities + ",\"engine_version\":\"4.0.0\",\"schema_version\":1,\"transport_version\":1}}' ;;\n"
                + "    *\\\"tool\\\":\\\"ctx_shell\\\"*) printf '%s\\n' '{\"id\":\"'\"$id\"'\",\"ok\":true,\"result\":{\"text\":\"ctx_shell:ok\",\"content_blocks\":[],\"original_tokens\":10,\"output_tokens\":4,\"saved_tokens\":6,\"mode\":null,\"changed\":false,\"shell\":{\"exit_code\":0}}}' ;;\n"
                + "    *call*) " + delay + "printf '%s\\n' '{\"id\":\"'\"$id\"'\",\"ok\":true,\"result\":{\"text\":\"ctx_read:ok\",\"content_blocks\":[],\"original_tokens\":10,\"output_tokens\":4,\"saved_tokens\":6,\"mode\":null,\"changed\":false,\"shell\":null}}' ;;\n"
                + "    *close*) printf '%s\\n' '{\"id\":\"'\"$id\"'\",\"ok\":true,\"result\":{}}'; exit 0 ;;\n"
                + "    *) exit 19 ;;\n"
                + "  esac\n"
                + "done\n";
        Files.writeString(script, source, StandardCharsets.UTF_8);
        int syntaxStatus = new ProcessBuilder("/bin/sh", "-n", script.toString()).start().waitFor();
        if (syntaxStatus != 0) {
            throw new IllegalStateException(source);
        }
        assertTrue(script.toFile().setExecutable(true));
        return script;
    }

    private static void assertNoAgentState(Path root) throws IOException {
        try (Stream<Path> children = Files.list(root)) {
            assertTrue(children.noneMatch(item -> item.getFileName().toString().startsWith(".leanctx-agent-")));
        }
    }

    private static void deleteTree(Path root) throws IOException {
        try (Stream<Path> children = Files.walk(root)) {
            children.sorted((left, right) -> right.getNameCount() - left.getNameCount())
                    .forEach(item -> {
                        try {
                            Files.deleteIfExists(item);
                        } catch (IOException exception) {
                            throw new RuntimeException(exception);
                        }
                    });
        }
    }
}
