# Changelog

All notable public changes to this project are documented in this file. Detailed experiment results, failure attribution, and internal analysis notes are tracked in private documentation and are intentionally not published here.

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

## [v0.138.199] - 2026-05-20

### Added
- Added named run profiles for reproducible workspace evaluations.
- Added evaluation overlay utilities for adjusted result comparisons.
- Added external failure taxonomy utilities for baseline analysis.
- Added tests for run-profile metadata, overlay summaries, and external failure classification.

### Changed
- Improved public evaluation summaries with explicit run-profile and timeout metadata.
- Documented GateForge and OpenCode benchmark comparison with the same base model:

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 130/132 | 21/21 | 56/56 | 53/55 |
| OpenCode | 120/132 | 21/21 | 50/56 | 42/55 |

| Agent | tokens | wall time |
| --- | ---: | ---: |
| GateForge | ~39.7M | ~14,658s |
| OpenCode | ~66.1M | ~20,843s |

### Validation
- Public unit tests for the updated workspace runner and evaluation utilities pass under `python3 -m unittest`.
- Detailed benchmark assets, run counts, task identities, model/provider details, and failure attribution traces remain private until a dedicated benchmark or paper release.

## [v0.36.8] - 2026-05-01

### Added
- Added public infrastructure for the v0.23.x–v0.36.x evaluation line, covering harness contract freeze, repeatability protocol, benchmark substrate governance, provider-agnostic executor boundary audit, tool-use harness, behavioral oracle benchmarking, transparent candidate critique, semantic memory units, hard-family expansion, repair strategy attribution, candidate implementation consistency, and agent readiness closeout.
- Added versioned runners, validators, and tests for harness inventory audits, trajectory schemas, oracle contracts, and contract synthesis that preserve the executor boundary.
- Added repeatability and replay infrastructure: unified repeatability runners, provider noise classification, budget policy gates, and replay harnesses that separate provider instability from agent capability failure.
- Added provider-agnostic tool-use harness with multi-provider adapter support, enabling LLM-driven structural tool invocation as the default agent architecture.
- Added benchmark substrate governance and behavioral oracle integration: task schema/loader infrastructure, admission verification, and standardized repair and generation surfaces.
- Added agent readiness contracts: provider stability gates, evidence role enforcement, artifact completeness checks, blind lint validation, repair report generation, and no-wrapper-repair auditing.

### Changed
- Consolidated the internal v0.23.x–v0.36.x research chain into this public phase closeout.
- Migrated the default agent architecture from fixed-round passive feedback to autonomous tool-use, with fixed-round retained only as a historical comparison surface.
- Closed the phase with a readiness-first discipline: all future capability claims require stable execution surfaces, complete artifact chains, and blind validation gates.

### Validation
- Public validation is summarized at the phase level only.
- All newly added v0.23.x–v0.36.x public utilities pass their `python3 -m unittest` coverage.
- Detailed run counts, candidate identities, family-level outcomes, internal promotion rules, and per-version experiment metrics remain in private documentation.

## [v0.22.10] - 2026-04-25

### Added
- Added public infrastructure for the v0.20.x-v0.22.x evaluation line, covering search-density profiling, source-backed task construction, complex repair-target admission, live multi-turn screening, repeatability auditing, and phase synthesis.
- Added versioned runners and tests for high-quality Modelica error construction workflows that preserve the executor boundary and keep repair decisions inside the Agent/LLM loop.
- Added synthesis utilities that distinguish one-off repair successes from repeatable benchmark seeds.

### Changed
- Consolidated the internal v0.20.x, v0.21.x, and v0.22.x research chain into this public phase closeout.
- Kept the public summary focused on reusable framework and harness outcomes rather than detailed experiment design, task identities, pass-rate tables, or failure-attribution traces.
- Closed the phase with a framework-first decision: continue hardening the Agent framework, harness, oracle contracts, trajectory schema, and benchmark substrate before considering any large-scale training workflow.

### Validation
- Public validation is summarized at the phase level only.
- Newly added v0.22.x utilities pass their public `python3 -m unittest` coverage.
- Detailed run counts, candidate identities, family-level outcomes, and internal promotion rules remain in private documentation.

## [v0.19.66] - 2026-04-24

### Added
- Added public infrastructure for Modelica generation audits, failure attribution, repair-budget analysis, prompt/profile comparison, and benchmark synthesis.
- Added CI-safe fallback behavior so public tests do not depend on private assets or local experiment artifacts.
- Added public runners and tests for the latest internal evaluation workflows.

### Changed
- Closed the v0.19.x public phase with sanitized release notes.
- Kept detailed experiment metrics, failure taxonomies, private task pools, and internal conclusions in private documentation.
- Preserved the executor boundary: new evaluation utilities do not add deterministic repair, routing, or hidden hint logic to the core repair loop.

### Validation
- Local Core A shard passed with the repository CI shard runner.
- v0.19.x public tests for the newly added audit and synthesis utilities pass under `python3 -m unittest`.
- Public release note intentionally reports capabilities at a high level only; detailed experimental results remain internal.

## [v0.18.2] - 2026-04-14

### Added
- Added public infrastructure for the v0.4.0-v0.18.2 experimental phase, including governance, evaluation, benchmark, execution, and workflow-to-product assessment utilities.
- Added tests and public code paths for the phase-level infrastructure that remains useful outside the private experiment record.

### Changed
- Consolidated the internal v0.4.0-v0.18.2 experiment chain into this public phase closeout.
- Removed detailed per-version experiment metrics, intermediate conclusions, private decision labels, and artifact deep links from the public changelog.
- Kept detailed phase evidence and interpretation in private internal documentation.

### Validation
- Public validation is summarized at the phase level only.
- Detailed run counts, pass rates, failure buckets, and artifact-level conclusions are intentionally not published.

## [v0.3.34] - 2026-04-05

### Added
- Added early public infrastructure for Modelica agent evaluation, OpenModelica integration, benchmark construction, repair-loop execution, and external-agent comparison scaffolding.
- Added tests and runnable utilities for the early experimental line where they remain part of the public codebase.

### Changed
- Consolidated the internal v0.3.x experiment chain into this public phase closeout.
- Removed task-level metrics, lane-by-lane conclusions, artifact deep links, private benchmark routing, and detailed attribution traces from the public changelog.
- Kept the public record focused on capability areas rather than experimental play-by-play.

### Validation
- Public validation is summarized at the phase level only.
- Detailed task counts, pass rates, branch/lane outcomes, and intermediate research conclusions are intentionally not published.

## [v0.2.6] - 2026-03-28

### Added
- Added the public rule-engine, experience, replay, planner-context, cross-domain validation, and difficulty-layer infrastructure for the early Modelica repair evaluation line.
- Added benchmark fixture preparation, source-viability filtering, diagnostic subtype metadata, difficulty-layer sidecars, and reusable evaluation utilities.

### Changed
- Consolidated the internal v0.2.x experiment chain into this public phase closeout.
- Kept the public record focused on reusable engineering surfaces rather than benchmark-by-benchmark results.
- Removed detailed comparison deltas, case counts, track-level outcomes, artifact deep links, and attribution traces from the public changelog.

### Validation
- Public validation is summarized at the phase level only.
- Detailed benchmark metrics, pass rates, per-track conclusions, and internal experiment artifacts are intentionally not published.

## [v0.1.9] - 2026-03-25

### Added
- Added the first public Agent Modelica foundation: OpenModelica validation, deterministic scaffolding, source-blind and multistep evaluation surfaces, guided-search modules, planner/replan modules, workspace isolation, and baseline/generalization benchmark utilities.
- Added modularization work that split large executor responsibilities into separately testable components.

### Changed
- Consolidated the internal v0.1.1-v0.1.9 experiment and hardening chain into this public phase entry.
- Kept the public record focused on engineering maturity, module extraction, and validation surfaces rather than detailed experimental outcomes.
- Removed concrete pass counts, line-count deltas, benchmark thresholds, task-level outcomes, and internal attribution details from the public changelog.

### Validation
- Public validation is summarized at the phase level only.
- Detailed smoke results, preflight metrics, benchmark rates, and intermediate experiment conclusions are intentionally not published.

## [v0.1.0] - 2026-02-20

### Added
- Initial MVP for agent-in-the-loop Modelica workflows.
- Added the first governance loop for proposal-driven execution, evidence collection, regression gating, policy decisions, and human-readable reporting.

### Validation
- MVP validation and iteration release.
