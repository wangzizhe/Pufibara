# Changelog

This file documents changes to the currently maintained public surface. Superseded exploratory releases remain available through Git history rather than being summarized here.

## Unreleased

### Changed
- Updated public project metadata, legal notices, brand assets, and Python compatibility coverage.
- Simplified the public CI entrypoint to discover the maintained provider-free test suite directly.
- Reduced the current repository to maintained provider-neutral Modelica execution and integration utilities.

### Removed
- Removed retired experimental profiles, admission-stage helpers, analysis utilities, CI-shard infrastructure, standalone launchers, and their dedicated tests.
- Removed the superseded workspace-style Agent prototype from the maintained public tree.

## [v0.213.979] - 2026-08-17

### Added
- Added conservative cleanup for regenerable OpenModelica build products while preserving source models, simulation results, logs, and other durable evidence.
- Added an experiment wrapper that performs cleanup after successful, failed, and interrupted commands and records a machine-readable cleanup summary.
- Added provider-scoped prompt caching support for Anthropic requests, including stable tool-definition caching and configuration validation.

### Changed
- Hardened cleanup against short Docker Desktop bind-unmount races with bounded retries and visible persistent failures.
- Added model-specific sampling compatibility so unsupported optional request fields can be omitted without changing other providers.

### Validation
- Added synthetic unit coverage for cleanup safety boundaries, evidence preservation, non-zero command exits, transient unmount failures, prompt-cache projection, and provider scoping.
- This public release contains reusable runtime infrastructure only. Private tasks, evaluation artifacts, Agent policies, verifier logic, and experimental protocols remain excluded.
