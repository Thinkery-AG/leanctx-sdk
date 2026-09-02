package com.thinkery.leanctx;

/** Artifact, receipt, or source evidence failed an integrity check. */
public class ArtifactIntegrityError extends EngineExecutionError {
    public ArtifactIntegrityError() {
        this(null, null, null, null);
    }

    public ArtifactIntegrityError(String message) {
        this(message, null, null, null);
    }

    public ArtifactIntegrityError(String message, ContextFailure failure, ContextView view) {
        this(message, failure, view, null);
    }

    public ArtifactIntegrityError(String message, ContextFailure failure, ContextView view,
                                  Throwable cause) {
        super(message, failure, view, cause, "artifact_integrity_error",
                "abort and replace the artifact with a digest-verified copy",
                false, false, true, false, false);
    }
}
