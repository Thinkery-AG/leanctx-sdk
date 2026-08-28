# SemVer and deprecation quick reference

The normative versioning policy is [`SEMVER.md`](../SEMVER.md).

LeanCTX SDK applies SemVer to Stable public APIs:

- patch: backward-compatible bug and security fixes;
- minor: backward-compatible Stable additions;
- major: Stable breaking changes.

Meaningful Stable deprecations are documented before removal and remain until
the next major release unless security requires faster action.

Preview APIs may change in a minor release. Meaningful Preview breaks require
migration notes but do not receive the Stable deprecation guarantee.
