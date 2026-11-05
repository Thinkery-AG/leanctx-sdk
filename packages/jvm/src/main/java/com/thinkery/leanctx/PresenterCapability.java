package com.thinkery.leanctx;

/**
 * A bearer secret the LeanCTX instance returns once to the host that presented sources.
 *
 * <p>Whoever later shows it is treated as THE HOLDER OF THAT CAPABILITY for
 * those presentations. It is not an agent name, not an attested identity and
 * not an authorization: current source rights are checked again at every use.
 *
 * <p>Host-internal: never put it in model context, a tool argument, a tool
 * result, a log or a receipt, and never send it to a provider. {@link #toString()}
 * is redacted, the type is not {@code Serializable}, and equality is identity;
 * {@link #reveal()} is the one deliberate way to the secret.
 */
public final class PresenterCapability {
    /** First LeanCTX instance release that serves the v2 materialization contract. */
    public static final String MIN_ENGINE_VERSION = "3.11.0";
    /** Longest presenter lifetime a host may ask for: one day. */
    public static final int MAX_TTL_SECONDS = 24 * 60 * 60;
    /** Request header that carries a capability; never a body member. */
    public static final String HEADER = "x-leanctx-presenter";

    private final String secret;

    /** Wrap a capability a host kept in its own secret store. */
    public PresenterCapability(String secret) {
        if (!isWellFormed(secret)) {
            throw new ValidationError("presenter capability is malformed");
        }
        this.secret = secret;
    }

    static boolean isWellFormed(String secret) {
        return secret != null && secret.matches("lcp1_[0-9a-f]{64}");
    }

    /** Return the secret. Never log, print or forward the result. */
    public String reveal() {
        return secret;
    }

    @Override
    public String toString() {
        return "PresenterCapability(<redacted>)";
    }
}
