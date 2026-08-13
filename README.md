# GateForge

<p align="center">
  <a href="https://github.com/wangzizhe/GateForge/actions/workflows/ci.yml" style="text-decoration:none;"><img src="https://github.com/wangzizhe/GateForge/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>&nbsp;
  <a href="https://github.com/wangzizhe/GateForge/releases" style="text-decoration:none;"><img src="https://img.shields.io/github/release/wangzizhe/GateForge.svg?include_prereleases" alt="Release" /></a>&nbsp;
  <a href="LICENSE" style="text-decoration:none;"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>&nbsp;
  <a href="https://www.python.org/" style="text-decoration:none;"><img src="https://img.shields.io/badge/python-%3E%3D3.10-3776AB.svg" alt="Python >= 3.10" /></a>
</p>
<p align="center" style="margin: 0.75rem auto 1rem; max-width: 920px; padding: 0.75rem 1rem; border: 1px solid #d0d7de; border-radius: 8px; background: #f6f8fa;">
  <strong>AI Agents for Physical Systems Modeling</strong>
</p>

## Agentic Modelica Workflow Benchmark

*Benchmark snapshot as of August 13, 2026.*

GateForge and Claude Code use the same foundation model family.

GateForge is currently evaluated on three Modelica workflow families:

| Workflow family | Agent task | Validation |
| --- | --- | --- |
| Model repair | Fix an existing broken Modelica model | Model checking and simulation |
| Model generation | Create a complete executable Modelica model from a task brief | Model checking, simulation, and physical behavior analysis |
| Model tuning | Calibrate parameters of an existing Modelica model | Model checking, simulation, and held-out behavioral validation |

GateForge records a higher observed success rate than Claude Code across all three workflow families, with the clearest separation on harder Modelica tasks.

### Model Repair

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 130/132 | 21/21 | 56/56 | 53/55 |
| Claude Code | 123/132 | 21/21 | 55/56 | 47/55 |

GateForge solves seven more cases and records substantially less total sequential wall time.

| Agent | wall time |
| --- | ---: |
| GateForge | ~14,650s (4.07h) |
| Claude Code | ~35,191s (9.78h) |

### Model Generation

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 35/50 | 2/2 | 10/10 | 23/38 |
| Claude Code | 27/50 | 2/2 | 10/10 | 15/38 |

GateForge solves eight more cases and records lower total wall time.

| Agent | wall time |
| --- | ---: |
| GateForge | ~11,176s (3.10h) |
| Claude Code | ~13,865s (3.85h) |

### Model Tuning

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 37/50 | 4/4 | 24/24 | 9/22 |
| Claude Code | 34/50 | 4/4 | 23/24 | 7/22 |

GateForge solves three more cases and records lower total wall time.

| Agent | wall time |
| --- | ---: |
| GateForge | ~10,711s (2.98h) |
| Claude Code | ~20,886s (5.80h) |

Wall time is the total recorded sequential case runtime and excludes infrastructure-only attempts.

## Legal Notice

Without prior written permission, no content on this site may be used for AI model training, fine-tuning, evaluation, or dataset construction.

- `LEGAL_NOTICE.md`
- `CONTENT_AUTHORIZATION_POLICY.md`
- `robots.txt`
