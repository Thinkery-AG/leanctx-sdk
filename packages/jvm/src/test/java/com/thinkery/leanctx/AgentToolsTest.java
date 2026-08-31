package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;

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
            context = new AgentContext(root.toString(), "", new AgentPermissions(),
                    new ExecutionPolicy(), binary.toString(), 0.2);
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

    private static Path fakeAgent(Path root, boolean badHello, boolean delayCall) throws Exception {
        Path script = root.resolve("fake-agent-" + UUID.randomUUID());
        boolean allowExec = !delayCall;
        String readCapabilities = "[\"ctx_compose\",\"ctx_glob\",\"ctx_read\",\"ctx_search\",\"ctx_symbol\",\"ctx_tree\"]";
        String capabilities = badHello
                ? "[\"ctx_read\"]"
                : allowExec
                        ? "[\"ctx_compose\",\"ctx_glob\",\"ctx_read\",\"ctx_search\",\"ctx_shell\",\"ctx_symbol\",\"ctx_tree\"]"
                        : readCapabilities;
        String delay = delayCall ? "sleep 5\n" : "";
        String source = "#!/bin/sh\n"
                + "policy=''\n"
                + "while [ \"$#\" -gt 0 ]; do\n"
                + "  if [ \"$1\" = \"--policy-file\" ]; then policy=\"$2\"; shift; fi\n"
                + "  shift\n"
                + "done\n"
                + "[ -f \"$policy\" ] || exit 17\n"
                + "id=0\n"
                + "while IFS= read -r line; do\n"
                + "  id=$((id + 1))\n"
                + "  case \"$line\" in\n"
                + "    *hello*) printf '%s\\n' '{\"id\":\"'\"$id\"'\",\"ok\":true,\"result\":{\"agent_tools_interface_version\":\"1.0.0\",\"allow_exec\":" + allowExec + ",\"allow_write\":false,\"capabilities\":" + capabilities + ",\"engine_version\":\"3.10.1\",\"schema_version\":1,\"transport_version\":1}}' ;;\n"
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
