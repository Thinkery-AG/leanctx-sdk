// Compile against the candidate JAR in a fresh consumer directory.
import com.thinkery.leanctx.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Collectors;
import java.util.concurrent.TimeUnit;

public final class ProAgentReference {
    private static AgentContext open(Path root, String binary) {
        return new AgentContext(root, "", new AgentPermissions(), new ExecutionPolicy(), Path.of(binary), 30.0);
    }
    private static boolean observeSnapshot(String text) throws Exception {
        Process observer = new ProcessBuilder(System.getenv("LEANCTX_REFERENCE_PYTHON"),
            System.getenv("LEANCTX_REFERENCE_SNAPSHOT_OBSERVER"))
            .redirectOutput(ProcessBuilder.Redirect.DISCARD).redirectError(ProcessBuilder.Redirect.DISCARD).start();
        try {
            try (var input = observer.getOutputStream()) { input.write(text.getBytes(StandardCharsets.UTF_8)); }
            return observer.waitFor(15, TimeUnit.SECONDS) && observer.exitValue() == 0;
        } finally { if (observer.isAlive()) observer.destroyForcibly(); }
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 4) throw new IllegalArgumentException("engine, previous engine, fixture root, output required");
        Path root = Path.of(args[2]);
        Path policy = root.resolve(".lean-ctx/policy.toml");
        String rules = Files.readString(policy);
        Map<String, Boolean> checks = new TreeMap<>();
        Map<String, String> responses = new TreeMap<>();
        boolean oldRejected = false;
        try (AgentContext old = open(root, args[1])) {
            old.capabilities();
        } catch (EngineProtocolError error) {
            oldRejected = error.getMessage().contains("hello is incompatible");
        }
        checks.put("old_engine_rejected", oldRejected);
        GitLabSource source = System.getenv("LEANCTX_REFERENCE_GITLAB_SOURCE") == null ? null : new GitLabSource(
            System.getenv("LEANCTX_REFERENCE_GITLAB_HOST"), Long.parseLong(System.getenv("LEANCTX_REFERENCE_GITLAB_PROJECT")),
            System.getenv("LEANCTX_REFERENCE_GITLAB_NAMESPACE"), Path.of(System.getenv("LEANCTX_REFERENCE_GITLAB_GLAB")));
        try (AgentContext context = new AgentContext(root, "", new AgentPermissions(), new ExecutionPolicy(), Path.of(args[0]), 30.0, source)) {
            String read = context.read("login.py", ReadMode.FULL, false).text();
            String composed = context.compose("investigate authentication retry", ".").text();
            if ("1".equals(System.getenv("LEANCTX_REFERENCE_PRO"))) {
                checks.put("pro_context_selection", composed.contains("Pro context selection:")
                    && !composed.contains("Pro context selection unavailable"));
            }
            checks.put("useful_masked_read", read.contains("REFRESH_SESSION_FIRST") && read.contains("REDACTED") && !read.contains("CUS-1234"));
            checks.put("useful_protected_compose", composed.contains("REFRESH_SESSION_FIRST") && composed.contains("login.py") && List.of("CUS-1234", "PRIVATE_CANARY", "private.py").stream().noneMatch(composed::contains));
            responses.put("read", read); responses.put("compose", composed);
            String separator = rules.endsWith("\n") ? "" : "\n";
            String deniedContext = "[context]\ndeny_tools=[\"ctx_read\"]\n";
            Files.writeString(policy, rules.contains("[context]") ? rules.replace("[context]", deniedContext) : rules + separator + deniedContext);
            String denied;
            boolean blocked;
            try {
                denied = context.read("login.py", ReadMode.FULL, false).text();
                blocked = denied.contains("POLICY BLOCKED");
            } catch (AgentPermissionError | EngineExecutionError error) {
                denied = error.getMessage();
                blocked = denied.toLowerCase(Locale.ROOT).contains("policy");
            }
            checks.put("changed_rule_blocks_read", blocked && !denied.contains("REFRESH_SESSION_FIRST") && !denied.contains("CUS-1234"));
            responses.put("denied", denied);
            Files.writeString(policy, rules);
            String restored = context.read("login.py", ReadMode.FULL, false).text();
            checks.put("same_session_rule_repair", restored.contains("REFRESH_SESSION_FIRST") && !restored.contains("CUS-1234"));
            responses.put("restored", restored);
            if (source != null) {
                Map<String, Object> query = new HashMap<>(Map.of("action", "query", "provider", "gitlab", "resource", "merge_requests",
                    "project", Long.toString(source.project()), "mode", "snapshot", "limit", 1));
                checks.put("live_selected_gitlab", observeSnapshot(context.call("ctx_provider", query).text()));
                for (String[] refusal : List.of(new String[]{"foreign_project_refused", "project", "other/project"},
                    new String[]{"unsupported_source_action_refused", "action", "refresh"})) {
                    var deniedQuery = new HashMap<>(query); deniedQuery.put(refusal[1], refusal[2]);
                    try { context.call("ctx_provider", deniedQuery); checks.put(refusal[0], false); }
                    catch (AgentPermissionError error) { checks.put(refusal[0], true); }
                }
                try {
                    Files.delete(policy);
                    try { context.read("login.py", ReadMode.FULL, false); checks.put("source_policy_removal_closes_session", false); }
                    catch (AgentPermissionError error) { checks.put("source_policy_removal_closes_session", true); }
                } finally { Files.writeString(policy, rules); }
                responses.clear();
            }
        }
        boolean passed = checks.size() >= 5 && checks.values().stream().allMatch(Boolean::booleanValue);
        String checksJson = checks.entrySet().stream().map(e -> "\"" + e.getKey() + "\":" + e.getValue()).collect(Collectors.joining(","));
        String responsesJson = responses.entrySet().stream().map(e -> "\"" + e.getKey() + "\":\"" + Base64.getEncoder().encodeToString(e.getValue().getBytes(StandardCharsets.UTF_8)) + "\"").collect(Collectors.joining(","));
        String location = Base64.getEncoder().encodeToString(AgentContext.class.getProtectionDomain().getCodeSource().getLocation().toString().getBytes(StandardCharsets.UTF_8));
        Files.writeString(Path.of(args[3]), "{\"passed\":" + passed + ",\"checks\":{" + checksJson + "},\"responses_base64\":{" + responsesJson + "},\"sdk_location_base64\":\"" + location + "\"}\n");
        if (!passed) throw new IllegalStateException("installed reference failed");
    }
}
