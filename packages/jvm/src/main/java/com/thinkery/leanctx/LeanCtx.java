package com.thinkery.leanctx;

/** Stable package-level version and wire constants. */
public final class LeanCtx {
    public static final String __version__ = "1.3.0";
    public static final String VERSION = __version__;
    public static final String ENGINE_INTERFACE_VERSION = "1.0.0";
    public static final int SCHEMA_VERSION = 1;
    public static final int TRANSPORT_VERSION = 1;
    public static final String AGENT_TOOLS_INTERFACE_VERSION = "1.0.0";
    public static final int AGENT_TOOLS_SCHEMA_VERSION = 1;
    public static final int AGENT_TOOLS_TRANSPORT_VERSION = 1;
    // Paired with LeanCTX 3.12.2 (the matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5, 3.12.0 and 3.12.1).
    public static final String SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = "3.12.2";


    /** Preview: parse a Context Store evidence document. */
    public static ContextStorePreview.ContextPolicyEvidence parsePolicyEvidence(Object raw) {
        return ContextStorePreview.parsePolicyEvidence(raw);
    }

    /** Preview: parse a task lineage document. */
    public static ContextStorePreview.TaskLineage parseTaskLineage(Object raw) {
        return ContextStorePreview.parseTaskLineage(raw);
    }

    /** Preview: read the scope's content-free policy evidence. */
    public static ContextStorePreview.ContextPolicyEvidence readPolicyEvidence(
            SubprocessInstanceClient instance, String projectRoot, ContextStorePreview.Scope scope) {
        return ContextStorePreview.readPolicyEvidence(instance, projectRoot, scope);
    }

    /** Preview: read one task's lineage within the supplied scope. */
    public static ContextStorePreview.TaskLineage readTaskLineage(
            SubprocessInstanceClient instance, String projectRoot, String taskId,
            ContextStorePreview.Scope scope) {
        return ContextStorePreview.readTaskLineage(instance, projectRoot, taskId, scope);
    }

    private LeanCtx() {
    }
}
