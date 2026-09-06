# LeanCTX JVM SDK

Java 21 and Kotlin-compatible SDK 1.1 for the five stable LeanCTX Product
primitives, Engine Interface v1, and the additive Agent Tools interface.

```java
try (AgentContext tools = AgentContext.open(projectRoot)) {
    ToolResult result = tools.read("README.md", ReadMode.AUTO, false);
    System.out.println(result.text());
}
```

Production code has no runtime dependencies. Engine processes use structured
arguments, bounded streams, project-root containment, strict JSON validation,
secure temporary policy files, and fail-closed process termination.

Engine 3.10.1 is published. This package remains a source preview pending its
own registry promotion gate.
