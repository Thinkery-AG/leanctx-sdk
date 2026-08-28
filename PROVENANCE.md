# Provenance

| Source group | Classification | Basis |
| --- | --- | --- |
| `src/leanctx_sdk/**` | `CLEAN_REIMPLEMENT` | accepted public behavior and versioned wire specifications |
| `tests/**` | `CLEAN_REIMPLEMENT` | independently authored behavioral assertions |
| `fixtures/engine-interface-v1/**` | `OPEN_PROTOCOL` | public Engine Interface v1 contract |
| `fixtures/sdk-v1/**` | `CLEAN_REIMPLEMENT` | Product contract and deterministic fingerprints |
| `fixtures/openai-agents-0.8.4/**` | `THIRD_PARTY` | published metadata plus independently recorded artifact digests |
| `scripts/**` | `CLEAN_REIMPLEMENT` | release, integrity, and installed-artifact requirements |
| `.github/workflows/**`, `requirements/**` | `CLEAN_REIMPLEMENT` | release policy and exact tool locks |
| examples and public documentation | `CLEAN_REIMPLEMENT` | public Product contract |

No SDK implementation is copied or line-adapted from LeanCTX Engine. The P4
clean-reimplementation boundary remains valid after P5–P7 additions. Engine is
consumed only through its public CLI wire boundary; Engine internals, P8/P9,
Cloud, and hosted services are not dependencies.

Third-party wheels are dependencies, not SDK source. Their digests, declared
licenses, content audit, and accepted findings are bound separately by release
evidence. Git-tracked release inputs are covered by the fail-closed source and
secret scans.
