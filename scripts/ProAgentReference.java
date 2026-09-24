// Compile against the candidate JAR in a fresh consumer directory.
import com.thinkery.leanctx.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Collectors;

public final class ProAgentReference {
    private static AgentContext open(Path root, String binary) {
        return new AgentContext(root, "", new AgentPermissions(), new ExecutionPolicy(), Path.of(binary), 30.0);
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
        try (AgentContext context = open(root, args[0])) {
            String read = context.read("login.py", ReadMode.FULL, false).text();
            String composed = context.compose("investigate authentication retry", ".").text();
            checks.put("useful_masked_read", read.contains("REFRESH_SESSION_FIRST") && read.contains("REDACTED") && !read.contains("CUS-1234"));
            checks.put("useful_protected_compose", composed.contains("REFRESH_SESSION_FIRST") && composed.contains("login.py") && List.of("CUS-1234", "PRIVATE_CANARY", "private.py").stream().noneMatch(composed::contains));
            responses.put("read", read); responses.put("compose", composed);
            Files.writeString(policy, rules + "[context]\ndeny_tools=[\"ctx_read\"]\n");
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
        }
        boolean passed = checks.size() == 5 && checks.values().stream().allMatch(Boolean::booleanValue);
        String checksJson = checks.entrySet().stream().map(e -> "\"" + e.getKey() + "\":" + e.getValue()).collect(Collectors.joining(","));
        String responsesJson = responses.entrySet().stream().map(e -> "\"" + e.getKey() + "\":\"" + Base64.getEncoder().encodeToString(e.getValue().getBytes(StandardCharsets.UTF_8)) + "\"").collect(Collectors.joining(","));
        String location = Base64.getEncoder().encodeToString(AgentContext.class.getProtectionDomain().getCodeSource().getLocation().toString().getBytes(StandardCharsets.UTF_8));
        Files.writeString(Path.of(args[3]), "{\"passed\":" + passed + ",\"checks\":{" + checksJson + "},\"responses_base64\":{" + responsesJson + "},\"sdk_location_base64\":\"" + location + "\"}\n");
        if (!passed) throw new IllegalStateException("installed reference failed");
    }
}
