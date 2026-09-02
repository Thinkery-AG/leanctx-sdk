# LeanCTX Go SDK

The Go module `github.com/Thinkery-AG/leanctx-sdk-go` implements LeanCTX SDK
1.1.0. It provides the five stable Product primitives, a strict Engine
Interface v1 subprocess adapter, and the persistent Agent Tools 1.1 client.

The 1.1.0 release is currently unpublished/private and requires a compatible
LeanCTX Engine for subprocess operation. Agent Tools negotiation requires
Engine Tools Interface support `3.10.1`.

```go
import leanctx "github.com/Thinkery-AG/leanctx-sdk-go"
```

The package is source-available under the accompanying `LICENSE`. Runtime
dependencies are limited to the Go standard library.
