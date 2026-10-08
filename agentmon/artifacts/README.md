# AgentMon Local Artifacts

This directory stores generated AgentMon delivery artifacts so the repository
root can stay focused on source code and docs.

## Layout

- `packages/`: source tarballs and matching `.sha256` files.
- `patches/`: incremental patch files and matching `.sha256` files.
- `snapshots/`: early baseline snapshots kept for reference.

When creating a new package or patch, place it under the matching subdirectory
instead of the project root or Desktop.

## Latest Package

```text
packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz
packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.sha256
packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.BUILD_MANIFEST.json
packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.summary.json
packages/agentmon-server-test-ready-20260806-project-audit-hardening-test-summary.txt
```

This package contains the optimizer experiment harness research line with M1/M2
evidence and advice replay, M3 advice-proxy evaluation, experiment
request/contract generation, mechanism registry profiles, and staged
experiment gates, P2 discovery version-contract fixes, server-profile test
gates, release-gate entrypoint, build/test provenance manifests, package
archive safety checks, quick/full source gate separation, gate provenance JSON,
sidecar SHA256 files, workspace/source isolation gates, runtime-source integrity
evidence, explicit batch validity states, dependency-correct optimizer DAG
ordering, configuration-bound replay reuse, artifact hash validation, strict
failure exit propagation, profile-driven tool parser candidates, and benchmark
task discovery without a fixed task id. Runtime gate JSON files remain
server-side validation results. Generated caches and the historical
`artifacts/` directory are excluded from the archive.
