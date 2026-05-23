# GateForge

<p align="center">
  <a href="https://github.com/wangzizhe/GateForge/actions/workflows/ci.yml" style="text-decoration:none;"><img src="https://github.com/wangzizhe/GateForge/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>&nbsp;
  <a href="https://github.com/wangzizhe/GateForge/releases" style="text-decoration:none;"><img src="https://img.shields.io/github/release/wangzizhe/GateForge.svg?include_prereleases" alt="Release" /></a>&nbsp;
  <a href="LICENSE" style="text-decoration:none;"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>&nbsp;
  <a href="https://www.python.org/" style="text-decoration:none;"><img src="https://img.shields.io/badge/python-%3E%3D3.10-3776AB.svg" alt="Python >= 3.10" /></a>
</p>
<p align="center" style="margin: 0.75rem auto 1rem; max-width: 920px; padding: 0.75rem 1rem; border: 1px solid #d0d7de; border-radius: 8px; background: #f6f8fa; font-size: 1.25rem;">
  <strong>AI Agent for Physical Systems Modeling</strong>
</p>

## Agentic Modelica Workflow Benchmark

*Benchmark snapshot as of May 21, 2026.*

All agents use the same foundation model family and are evaluated under the same benchmark and wall-clock conditions.

GateForge outperforms SOTA coding agents, with the strongest margin on medium and hard Modelica workflows.

| Agent | Total | easy | medium | hard |
| --- | ---: | ---: | ---: | ---: |
| GateForge | 130/132 | 21/21 | 56/56 | 53/55 |
| Claude Code | 123/132 | 21/21 | 55/56 | 47/55 |
| OpenCode | 120/132 | 21/21 | 50/56 | 49/55 |

It beat both baselines: executing faster with fewer tokens than OpenCode, and finishing quicker with a higher success rate than Claude Code.

| Agent | reported tokens* | wall time |
| --- | ---: | ---: |
| GateForge | ~39.7M | ~14,658s |
| Claude Code | ~15.9M | ~35,191s |
| OpenCode | ~66.1M | ~20,843s |

*Reported tokens are runner-reported estimates; GateForge records provider usage directly, while other runners may omit local context management, compression, retries, or tool-output handling costs.

## Legal Notice

Without prior written permission, no content on this site may be used for AI model training, fine-tuning, evaluation, or dataset construction.

- `LEGAL_NOTICE.md`
- `CONTENT_AUTHORIZATION_POLICY.md`
- `robots.txt`
