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

*Benchmark snapshot as of June 26, 2026.*

All agents use the same foundation model family and are evaluated under the same benchmark and wall-clock conditions.

GateForge is currently evaluated on three Modelica workflow families:

| Workflow family | Agent task | Validation |
| --- | --- | --- |
| Model repair | Fix an existing broken Modelica model | Model checking and simulation |
| Model generation | Create a complete executable Modelica model from a task brief | Model checking, simulation, and physical behavior analysis |
| Model tuning | Tune model parameters to match target physical behavior | Model checking, simulation, and hidden behavior verification |

GateForge outperforms SOTA coding agents across repair, generation, and tuning workflows, with the clearest separation on harder Modelica tasks.

### Model Repair

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 130/132 | 21/21 | 56/56 | 53/55 |
| Claude Code | 123/132 | 21/21 | 55/56 | 47/55 |
| OpenCode | 120/132 | 21/21 | 50/56 | 49/55 |

GateForge beats both baselines: executing faster with fewer tokens than OpenCode, and finishing quicker with a higher success rate than Claude Code.

| Agent | reported tokens* | wall time |
| --- | ---: | ---: |
| GateForge | ~39.7M | ~14,658s |
| Claude Code | ~15.9M | ~35,191s |
| OpenCode | ~66.1M | ~20,843s |

### Model Generation

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 21/22 | 2/2 | 10/10 | 9/10 |
| Claude Code | 19/22 | 2/2 | 10/10 | 7/10 |
| OpenCode | 18/22 | 2/2 | 10/10 | 6/10 |

GateForge leads the generation benchmark while using fewer reported tokens and less wall time than both baselines.

| Agent | reported tokens* | wall time |
| --- | ---: | ---: |
| GateForge | ~1.31M | ~1,343s |
| Claude Code | ~1.57M | ~4,474s |
| OpenCode | ~9.81M | ~3,693s |

### Model Tuning

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 35/43 | 4/4 | 24/24 | 7/15 |
| Claude Code | 33/43 | 4/4 | 24/24 | 5/15 |
| OpenCode | 33/43 | 4/4 | 23/24 | 6/15 |

GateForge leads the tuning benchmark overall, with the main separation on harder physical-system tuning tasks.

| Agent | reported tokens* | wall time |
| --- | ---: | ---: |
| GateForge | ~19.1M | ~7,348s |
| Claude Code | ~3.91M | ~13,135s |
| OpenCode | ~40.1M | ~12,457s |

\* Reported tokens are runner-reported estimates. GateForge records provider usage directly, while other runners may include or omit local context management, compression, retries, and tool-output handling costs.

## Legal Notice

Without prior written permission, no content on this site may be used for AI model training, fine-tuning, evaluation, or dataset construction.

- `LEGAL_NOTICE.md`
- `CONTENT_AUTHORIZATION_POLICY.md`
- `robots.txt`
