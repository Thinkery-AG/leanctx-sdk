# LeanCTX Go SDK

The Go module `github.com/Thinkery-AG/leanctx-sdk/packages/go` implements LeanCTX SDK
1.1.0. It provides the five stable Product primitives, a strict Engine
Interface v1 subprocess adapter, and the persistent Agent Tools 1.1 client.

The module is released from this monorepo with the Go-standard
`packages/go/vX.Y.Z` tag. Subprocess operation requires the published LeanCTX
Engine 3.10.1.

```go
import leanctx "github.com/Thinkery-AG/leanctx-sdk/packages/go"
```

The package is source-available under the accompanying `LICENSE`. Runtime
dependencies are limited to the Go standard library.
