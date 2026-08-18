# Agent Hub

> **Multi-Agent orchestration system** — LLM intent routing, Kahn-wave DAG scheduling, and 7 collaboration strategies over zero-intrusion `agent.yaml` manifests.

<p align="center">
  <a href="README_CN.md">📖 中文文档 → README_CN.md</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License MIT">
  <img src="https://img.shields.io/badge/status-beta-yellow?style=flat-square" alt="Status Beta">
  <img src="https://img.shields.io/badge/tests-465%20collected-brightgreen?style=flat-square" alt="Tests">
</p>

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Design Principles](#design-principles)
- [Features](#features)
- [Collaboration Strategies](#collaboration-strategies)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [Agent Manifest Protocol](#agent-manifest-protocol)
- [Model Configuration](#model-configuration)
- [Testing](#testing)
- [Project Map](#project-map)
- [FAQ](#faq)
- [License](#license)

---

## Overview

Agent Hub is a **decoupled central scheduler** for multi-agent orchestration. It discovers professional agents through `agent.yaml` manifest files, routes natural-language requests into cross-agent DAGs with an LLM router, and executes them using 7 collaboration strategies — with a Rich TUI dashboard showing the task graph, per-agent panels, and data-flow timeline in real time.

The system follows a **zero-intrusion** philosophy: each existing project adds a single `agent.yaml` at its root to become discoverable and callable. No restructuring, no framework imports, no deep coupling.

**Core idea**: not building yet another agent, but building the system that lets specialized agents work together — Agent Hub is the glue, professional agents are the building blocks.

```
┌─────────────────────────────────────────────────────────┐
│                  Agent Hub (Orchestrator)                │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ LLM Router  │─▶│ DAG Scheduler│─▶│ Strategy Exec. │  │
│  │ (intent →   │  │ (Kahn waves) │  │ (7 strategies) │  │
│  │  route plan)│  └──────────────┘  └────────────────┘  │
│  └─────────────┘         │                    │          │
│    ┌────────────┐  ┌─────┴─────┐       ┌──────┴──────┐   │
│    │ Session    │  │ Cron      │       │ Watchdog    │   │
│    │ Store      │  │ Scheduler │       │ (auto-heal) │   │
│    └────────────┘  └───────────┘       └─────────────┘   │
└───────────────────────┬─────────────────────────────────┘
                        │  agent.yaml (read-only)
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  OmniAgent   │ │ Resume-Sync  │ │  SmartBench  │
│  (CLI)       │ │  (CLI)       │ │  (CLI)       │
│  Coding      │ │  Resume      │ │  Code        │
│  Assistant   │ │  Automation  │ │  Diagnosis   │
└──────────────┘ └──────────────┘ └──────────────┘
```

---

## Architecture

### Three-Layer Decoupled Architecture

```
┌──────────────────────────────────────────────────────────┐
│                AGENT HUB (Orchestrator)                   │
│  Central scheduler — intent routing, DAG building,       │
│  strategy execution, session store, watchdog, cron        │
├──────────────────────────────────────────────────────────┤
│                AGENT MANIFEST (agent.yaml)                │
│  Read-only descriptor — each project adds ONE file        │
│  name, description, capabilities, interface templates     │
├──────────────────────────────────────────────────────────┤
│             PROFESSIONAL AGENTS (Executors)               │
│  OmniAgent │ resume-sync │ SmartBench │ ...              │
│  (cli / internal protocols; mcp / http planned)           │
└──────────────────────────────────────────────────────────┘
```

| Layer | Description |
|---|---|
| **Agent Hub** | Central orchestrator — intent recognition, routing, DAG scheduling, strategy execution, session store, watchdog, cron scheduling. No domain-specific logic. |
| **Agent Manifest** | A single `agent.yaml` file per project, acting as a read-only descriptor: name, description, capabilities, tasks, and interface call templates. |
| **Professional Agents** | Domain-specific agents (OmniAgent for coding, resume-sync for resume automation, SmartBench for code diagnosis). Today they communicate via **CLI subprocess** (`cli`) or **in-process dispatch** (`internal`). `mcp` and `http` protocols are declared in the manifest schema but not yet implemented — see the validator warning in `agent_hub/manifest.py`. |

---

## Design Principles

| Principle | Description |
|---|---|
| **Zero-Intrusion** | Each project only adds one `agent.yaml`. No code changes, no framework imports, no deep coupling. |
| **Natural-Language-First** | Users state goals in natural language. The LLM router decomposes them into an executable task DAG. |
| **On-Demand Invocation** | Agents are started only when needed. No persistent connections or background polling. |
| **Graceful Degradation** | When routing confidence is low (< 0.7) the CLI shows a warning; when the LLM call fails entirely, the router falls back to deterministic rule-based matching (`_rule_based_route` in `agent_hub/router.py`). |

---

## Features

### Smart Routing (`agent_hub/router.py`)

| Capability | Description |
|---|---|
| **Input Optimization** | Rewrites and enriches user queries before intent recognition (`_optimize_input`). |
| **Strategy Inference** | The LLM route plan selects one of the 7 collaboration strategies per task. |
| **Routing Memory** | Caches routing decisions; identical or repeated inputs skip LLM inference (`_check_routing_memory`). |
| **Confidence Gating** | Plans with `confidence < 0.7` are flagged (`CONFIDENCE_WARN_THRESHOLD`) and surfaced to the user. |
| **Fuzzy Matching** | Falls back to fuzzy task-to-capability matching when exact intent matching fails (`_fuzzy_match_task`). |
| **Rule Fallback** | If the LLM call fails, deterministic rule-based routing takes over (`fallback_rule_based=True` by default). |

### Session Store (`agent_hub/session_store.py`)

| Feature | Detail |
|---|---|
| Cross-turn context | Conversation state persisted across exchanges. |
| History injection | Recent conversation rounds are injected into the routing prompt for context-aware decisions. |
| Auto-cleanup | Keeps the most recent 50 session files (`MAX_SESSION_FILES = 50`). |

### Watchdog (`agent_hub/bridge.py`)

- Monitors all running agent processes on a 15-second loop (`start_watchdog(interval=15)`).
- Auto-restarts crashed agents: at most **3 restarts within a 300-second window** (`MAX_RESTARTS = 3`, `RESTART_WINDOW = 300.0`).
- Beyond the limit, the agent is marked **failed** and the orchestrator is notified.

### Cron Scheduler (`agent_hub/cron.py`)

- Pure-Python 5-field cron engine — **zero external dependencies** (no croniter).
- Supports exact values, `*`, steps (`*/5`), lists (`0,30`), and ranges (`1-5`).
- Job definitions persist to `.agent_hub/cron_jobs.json`; execution history (last 100 entries, `MAX_HISTORY_ENTRIES = 100`) to `.agent_hub/cron_history.json`.

---

## Collaboration Strategies

Agent Hub supports 7 collaboration strategies (`agent_hub/router.py` `StrategyType`, executed in `agent_hub/scheduler.py`):

| # | Strategy | Pattern | Use Case |
|---|----------|---------|----------|
| 1 | **fan_out** | Parallel dispatch to multiple agents, then aggregate results (default DAG mode) | Independent tasks executed concurrently |
| 2 | **pipeline** | Strict serial chain — emerges naturally from `depends_on` edges in the DAG | ETL-style multi-stage processing |
| 3 | **debate** | Agent A proposes → Agent B critiques → A revises → loop until exit condition | Bug diagnosis with cross-agent verification |
| 4 | **reflection** | Single agent: execute → self-review → improve, up to `max_iterations` | Code generation with self-correction |
| 5 | **vote** | Same question to multiple agents/models → pick the best output | Selecting the strongest answer |
| 6 | **plan_execute** | Plan first → execute step by step → re-plan on failure | Complex multi-step tasks |
| 7 | **hitl** | Human-in-the-loop — pause for approval at `approval_gates` task IDs | Sensitive operations needing sign-off |

Unknown strategy names fall back to `fan_out` with a warning. Loop strategies (`debate` / `reflection`) are bounded by `max_iterations`; `hitl` declares its approval gates in the route plan.

### Strategy Decision Flow

```
User Request
     │
     ▼
┌─────────────────┐
│ LLM Router      │  (input optimization → route plan + confidence)
└────────┬────────┘
         │
    ┌────┴────────┐
    │ Route plan  │── tasks[], strategy, max_iterations, approval_gates
    └────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────────────────────────────┐
│ DAG Builder     │────▶│ Kahn topological waves (parallel)    │
└────────┬────────┘     └──────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Strategy Executor: fan_out / pipeline / debate / reflection │
│                    / vote / plan_execute / hitl             │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.10 or higher
- An API key for at least one LLM provider (DeepSeek by default; any OpenAI-compatible endpoint works)

### Installation

```bash
git clone https://github.com/xianyu-sheng/Agent-hub.git
cd Agent-hub

python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

pip install -e .
```

Runtime dependencies are minimal: `pyyaml`, `rich`, `click` (declared in `pyproject.toml`; there is no separate `requirements.txt`).

### Configuration

Register at least one model — the fastest path is the interactive command center:

```bash
agent-hub            # bare launch enters the interactive command center (REPL)
```

```
agent-hub> models add        # interactive: name + API key, provider auto-detected
```

Or via environment variable:

```bash
export DEEPSEEK_API_KEY=sk-...
```

Model definitions live in `models.yaml` (`${VAR}` syntax references environment variables — keys never sit in plaintext).

### Basic Usage

```bash
# Interactive command center (bare `agent-hub` with no subcommand)
agent-hub

# One-shot task execution (streaming output by default)
agent-hub run "Analyze OmniAgent code quality and update my resume"

# With the multi-panel TUI dashboard
agent-hub run -d "Review the code in ./src for security issues"

# Agent lifecycle
agent-hub start                # start all registered agents + health panel
agent-hub status               # system overview
agent-hub stop                 # stop all agents

# Agent management
agent-hub agent list
agent-hub agent info omniagent
agent-hub agent register /path/to/project    # reads its agent.yaml
agent-hub agent validate

# Cron scheduling
agent-hub schedule list
agent-hub schedule add --name nightly --cron "0 2 * * *" --agent smartbench --task diagnose_code
agent-hub schedule history
```

Inside the interactive command center you can also type natural language directly — no `run` prefix needed:

```
agent-hub> 分析 agent-hub 代码质量
```

---

## Command Reference

### Top-Level Commands

| Command | Description |
|---|---|
| `agent-hub` | Launch the interactive command center (REPL) |
| `agent-hub start [--agents a,b]` | Start registered agents and enter the health-monitoring panel |
| `agent-hub stop` | Stop all running agents |
| `agent-hub status` | Show system status and running tasks |
| `agent-hub run [-d] [-t SECONDS] <task>` | Execute a multi-agent task (`-d` dashboard, `-t` per-agent timeout, default 300s) |

### `agent-hub agent …`

| Command | Description |
|---|---|
| `agent list` | List all registered agents |
| `agent info [name]` | Show agent details |
| `agent register [path]` | Interactively register a project (reads its `agent.yaml` into `agents.d/`) |
| `agent reload` | Reload manifests from `agents.d/` after edits |
| `agent models [name]` | Show models declared by an agent |
| `agent validate` | Validate all registered manifests |

### `agent-hub models …`

| Command | Description |
|---|---|
| `models list` | List configured LLM models |
| `models info [name]` | Model details |
| `models add [name] [-k KEY]` | Add a model (interactive or quick mode; provider auto-detected) |
| `models remove` | Interactively remove a model |
| `models update` | Interactively update a model |
| `models priority [order]` | View or set model fallback priority |

### `agent-hub schedule …`

| Command | Description |
|---|---|
| `schedule list` | List cron jobs |
| `schedule add` | Add a cron job (5-field syntax) |
| `schedule remove [name]` | Remove a job |
| `schedule run [name]` | Trigger a job immediately |
| `schedule history [--limit N]` | Show execution history |
| `schedule stop` | Stop the cron loop |

---

## Agent Manifest Protocol

The manifest is the contract between Agent Hub and any professional agent. Each agent project adds a single `agent.yaml` at its root, then `agent-hub agent register /path/to/project` copies it into `agents.d/<name>.yaml` with a `project_path` pointer.

### Schema (excerpt)

```yaml
name: smartbench
display_name: "SmartBench"
description: |
  Evidence-constrained code diagnosis engine.

protocol: cli            # cli (stable) | internal (in-process) | mcp / http (planned)

capabilities:
  tasks:
    - name: diagnose_code
      description: "Diagnose code quality and resource-lifecycle risks"
      # interface templates substitute variables like {query} per invocation
```

`internal` agents are dispatched in-process (no subprocess — used by Agent Hub itself to avoid recursion). Registry files under `agents.d/` add a `project_path` pointer to the project's on-disk location.

### Protocol Types

| Protocol | Status | Description |
|---|---|---|
| `cli` | Implemented | Invoke via CLI subprocess through `CLIBridge` (`agent_hub/bridge.py`). Requires `interface.command`. |
| `internal` | Implemented | In-process dispatch, no subprocess. Used by Agent Hub's self-manifest. |
| `mcp` | Planned | Declared in the schema; `CLIBridge` currently warns that MCP is not implemented. |
| `http` | Planned | Declared in the schema; `HTTPBridge` is future work. |

### Agent Discovery

Agent Hub loads manifests from the registry directory `agents.d/` (resolved by `_resolve_registry_dir()` in `agent_hub/cli.py`). Register new agents with `agent-hub agent register <project-path>`; edit files under `agents.d/` and run `agent-hub agent reload` to pick up changes.

---

## Model Configuration

Models are declared in `models.yaml`:

```yaml
models:
  - name: deepseek-v4-pro
    provider: deepseek
    api_base: https://api.deepseek.com
    api_key: "${DEEPSEEK_API_KEY}"    # env var reference — never plaintext
    models:
      - deepseek-v4-pro
    default: true
model_priority:
  - deepseek-v4-pro
```

`models priority` defines the fallback order when a model is unavailable or rate-limited. Any OpenAI-compatible endpoint works by setting `api_base` accordingly.

---

## Readiness Check

Startup performs layered readiness checks:

```
Layer 1: Welcome banner — version + discovered agent count
Layer 2: Pre-execution check — manifests valid, model configured
Layer 3: LLM failure guidance — actionable hints on API errors
         (e.g. rate limit → retry or switch provider via models priority)
```

---

## Testing

The suite holds **465 tests** (`pytest --collect-only`) across five files:

| File | Focus |
|---|---|
| `tests/test_bridge.py` | CLI bridge, process lifecycle, watchdog restart limits |
| `tests/test_cron.py` | Cron parsing, scheduling, history persistence |
| `tests/test_manifest.py` | Manifest schema validation and parsing |
| `tests/test_router.py` | Intent routing, confidence scoring, rule fallback |
| `tests/test_session_pid.py` | Session store and PID file management |

```bash
pip install -e ".[dev]"
pytest
```

---

## Project Map

```
Agent-hub/
├── agent_hub/                 # Core package
│   ├── cli.py                 # CLI entry: start/stop/status/run, agent/models/schedule groups, REPL
│   ├── router.py              # LLM intent router: optimization, strategy inference, confidence, rule fallback
│   ├── scheduler.py           # DAG scheduler: Kahn waves + 7 strategy executors
│   ├── bridge.py              # CLIBridge: subprocess lifecycle + watchdog auto-heal
│   ├── manifest.py            # agent.yaml parsing & validation
│   ├── llm.py                 # OpenAI-compatible LLM client (reasoning-model aware)
│   ├── model_config.py        # models.yaml management
│   ├── session_store.py       # Session persistence (keep last 50)
│   ├── pid_store.py           # PID file management
│   ├── cron.py                # Pure-Python cron engine (no external deps)
│   └── dashboard.py           # Rich multi-panel TUI dashboard
├── agents.d/                  # Registered agent manifests (agent-hub / omniagent / resume-sync / smartbench)
├── agent.yaml                 # Agent Hub's own self-manifest
├── models.yaml                # Model registry + priority
├── tests/                     # 465 tests
├── pyproject.toml             # Packaging: `agent-hub` console script
└── README.md
```

---

## FAQ

**Q: How is Agent Hub different from LangChain / AutoGen / CrewAI?**

Agent Hub is built around **zero-intrusion discovery**. Frameworks ask you to import SDKs or subclass base classes; Agent Hub only reads an `agent.yaml` from each existing project. It is an orchestrator, not a framework — your agents stay fully independent.

**Q: Can I add my own agent without modifying Agent Hub?**

Yes. Write an `agent.yaml` in your project root, then run `agent-hub agent register /path/to/your/project`. Zero code changes on either side.

**Q: What LLM providers are supported?**

Any OpenAI-compatible endpoint. DeepSeek is the default preset; set `api_base` in `models.yaml` to point at OpenAI, Anthropic-compatible proxies, local Ollama, etc.

**Q: How does the scheduler handle agent crashes?**

The watchdog scans every 15 seconds. A crashed agent with `desired_state == "running"` is restarted automatically, up to 3 times within 300 seconds. Beyond that it is marked failed and surfaced in the status panel.

**Q: Is Agent Hub production-ready?**

It is in **beta**: 465 tests cover the bridge, cron, manifest, router, and session layers, and it orchestrates three real projects (OmniAgent, resume-sync, SmartBench) daily. The `mcp` and `http` protocols are schema-declared but not yet implemented — treat manifests declaring them as forward-compatible.

---

## License

[MIT](LICENSE) © Xianyu Sheng
