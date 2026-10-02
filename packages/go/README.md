# LeanCTX Go SDK

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

The Go module `github.com/Thinkery-AG/leanctx-sdk/packages/go` connects your
host-owned model and workflow to a local LeanCTX Engine. Your application keeps
its model, agent loop, and UI. The module provides the five Stable lifecycle
primitives plus the separate Stable Agent Tools API.

The module uses the Go-standard `packages/go/vX.Y.Z` tag. The published SDK
1.1.0 release requires Engine 3.10.1; current `main` sources target Engine
3.10.5. Check the release-specific compatibility record before choosing an
Engine binary.

```go
import leanctx "github.com/Thinkery-AG/leanctx-sdk/packages/go"
```

The SDK source license permits non-production use; production use, OEM
embedding, and commercial redistribution require a separate written agreement
signed by Thinkery AG. Runtime dependencies are limited to the Go standard
library.
