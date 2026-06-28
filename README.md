# Agent Hub

> **Multi-Agent Orchestration System** — LLM intent routing, DAG scheduling, and 7 collaboration strategies.

<p align="center">
  <a href="README_CN.md">📖 中文文档 → README_CN.md</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License MIT">
  <img src="https://img.shields.io/badge/status-beta-yellow?style=flat-square" alt="Status Beta">
  <img src="https://img.shields.io/badge/tests-30%20passed-brightgreen?style=flat-square" alt="Tests 30 Passed">
  <img src="https://img.shields.io/badge/coverage-80%25-yellowgreen?style=flat-square" alt="Coverage 80%">
</p>

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Design Principles](#design-principles)
- [Features](#features)
  - [Smart Routing](#smart-routing)
  - [Session Memory](#session-memory)
  - [Watchdog](#watchdog)
  - [Cron Scheduler](#cron-scheduler)
- [Collaboration Strategies](#collaboration-strategies)
- [Quick Start](#quick-start)
- [REPL Usage](#repl-usage)
- [Command Reference](#command-reference)
- [Agent Manifest Protocol](#agent-manifest-protocol)
- [Readiness Check](#readiness-check)
- [Testing](#testing)
- [Project Map](#project-map)
- [FAQ](#faq)
- [License](#license)

---

## Overview

Agent Hub is a **decoupled central scheduler** for multi-agent orchestration. It discovers professional agents through lightweight `agent.yaml` manifest files, routes natural language requests into cross-agent DAGs, and executes them using 7 different collaboration strategies.

The system follows a **zero-intrusion** philosophy — each existing project only needs to add a single `agent.yaml` file to become discoverable and callable by Agent Hub. No restructuring, no deep coupling.

```
┌─────────────────────────────────────────────────────────┐
│                   Agent Hub (Orchestrator)               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Smart Router │─▶│ DAG Scheduler│─▶│ Strategy Exec. │  │
│  └─────────────┘  └──────────────┘  └────────────────┘  │
│         │                │                    │          │
│    ┌────┴────┐     ┌────┴────┐         ┌─────┴─────┐   │
│    │ Session │     │ Cron    │         │ Watchdog  │    │
│    │ Memory  │     │ Sched.  │         │ Monitor   │    │
│    └─────────┘     └─────────┘         └───────────┘   │
└───────────────────────┬─────────────────────────────────┘
                        │  agent.yaml (read-only)
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  OmniAgent   │ │ Resume-Sync  │ │ SmartBench    │
│  (CLI/MCP)   │ │  (CLI)       │ │  (CLI/MCP)   │
│  Coding      │ │  Resume      │ │ Code Analysis │
│  Assistant   │ │  Automation  │ │ & Debate     │
└──────────────┘ └──────────────┘ └──────────────┘
```

---

## Architecture

### Three-Layer Decoupled Architecture

```
┌──────────────────────────────────────────────────────────┐
│                AGENT HUB (Orchestrator)                   │
│  Central scheduler — intent routing, DAG building,       │
│  strategy execution, session memory, watchdog, cron       │
├──────────────────────────────────────────────────────────┤
│                AGENT MANIFEST (agent.yaml)                │
│  Read-only descriptor — each project adds ONE file        │
│  name, description, capabilities, interface templates     │
├──────────────────────────────────────────────────────────┤
│             PROFESSIONAL AGENTS (Executors)               │
│  omniagent │ resume-sync │ SmartBench │ ...              │
│  (cli / internal / mcp / http protocols)                  │
└──────────────────────────────────────────────────────────┘
```

| Layer | Description |
|---|---|
| **Agent Hub** | Central orchestrator — handles intent recognition, routing, DAG scheduling, strategy execution, session memory, watchdog, and cron scheduling. No domain-specific logic. |
| **Agent Manifest** | A single `agent.yaml` file per project, acting as a read-only descriptor. Contains the agent's name, description, capabilities, tasks, and interface call templates. |
| **Professional Agents** | The actual domain-specific agents (e.g., omniagent for coding, resume-sync for resume automation, SmartBench for code analysis). Communicate via CLI, internal Python calls, MCP, or HTTP. |

---

## Design Principles

| Principle | Description |
|---|---|
| **Zero-Intrusion** | Each existing project only needs to add one `agent.yaml` file. No code changes, no framework imports, no deep coupling. |
| **Interactive Guidance** | When a user's intent is ambiguous, the system proactively asks clarifying questions rather than guessing or failing silently. |
| **Natural-Language-First** | Users express their goals in natural language. The system understands, disambiguates, and translates into executable DAGs. |
| **On-Demand Invocation** | Agents are only loaded and called when needed. No persistent connections or background polling. |
| **Intelligent Fallback** | When routing confidence is low or an agent fails, the system degrades gracefully — fuzzy matching, rule-based fallbacks, and clear user-facing warnings. |

---

## Features

### Smart Routing

| Capability | Description |
|---|---|
| **Input Optimization** | Automatically rewrites and enriches user queries for better intent recognition. |
| **Collaboration Strategy Inference** | Analyzes the task to determine which of the 7 collaboration strategies is most appropriate. |
| **Routing Memory** | Caches routing decisions for repeated requests — skips LLM inference on identical inputs. |
| **Confidence Gating** | If routing confidence is below 70%, the system shows a user warning and asks for confirmation. |
| **Fuzzy Matching** | When exact intent matching fails, performs fuzzy matching against known agent capabilities. |
| **Rule Fallback** | If LLM-based routing is unavailable, falls back to deterministic rule-based matching. |

### Session Memory

| Feature | Detail |
|---|---|
| Cross-turn context | Maintains conversation state across multiple exchanges. |
| Last 3 rounds injected | Recent conversation history is injected into the routing prompt for context-aware decisions. |
| Auto-cleanup | Automatically trims the conversation history to keep the last 50 rounds to manage context window. |

### Watchdog

- Monitors all running agent processes.
- Auto-restarts crashed processes (max 3 restarts within 5 minutes).
- If the restart limit is exceeded, marks the agent as **failed** and notifies the orchestrator.

### Cron Scheduler

- Pure Python cron engine — **zero external dependencies**.
- Standard 5-field cron syntax (`minute hour day month weekday`).
- Execution history is persisted to disk for audit and recovery.

---

## Collaboration Strategies

Agent Hub supports 7 collaboration strategies for orchestrating multi-agent workflows:

| # | Strategy | Pattern | Use Case |
|---|----------|---------|----------|
| 1 | **fan_out** | Parallel dispatch to multiple agents, then aggregate results | Code review by multiple analyzers simultaneously |
| 2 | **debate** | diagnose → fix → loop (iterative critique between agents) | Bug diagnosis with cross-agent verification |
| 3 | **reflection** | execute → self-review → improve | Code generation with self-correction |
| 4 | **vote** | Multi-model concurrent execution → compare results | Selecting the best output from multiple LLMs |
| 5 | **plan_execute** | Plan first → execute step by step | Complex multi-step tasks requiring decomposition |
| 6 | **hitl** | Human-in-the-loop — pause for approval at critical steps | Sensitive operations (deployments, data deletion) |
| 7 | **pipeline** | Sequential execution through a chain of agents | ETL pipelines, multi-stage processing |

### Strategy Decision Flow

```
User Request
     │
     ▼
┌─────────────────┐
│ Intent Analysis │
└────────┬────────┘
         │
    ┌────┴────┐
    │  Single │      Multi
    │  Agent? │───────┼──────────
    └────┬────┘       │
         │            ▼
    ┌────┴────┐  ┌──────────┐
    │ Execute │  │ Strategy │
    │ Directly│  │ Selection│
    └─────────┘  └────┬─────┘
                      │
         ┌────────────┼────────────┬───────────┬──────────┐
         ▼            ▼            ▼           ▼          ▼
     ┌──────┐   ┌────────┐  ┌──────────┐ ┌──────┐  ┌────────┐
     │Fanout│   │ Debate │  │Reflection│ │ Vote │  │Pipeline│
     └──────┘   └────────┘  └──────────┘ └──────┘  └────────┘
                                           ┌──────┐
                                           │HITL  │
                                           └──────┘
                                      ┌─────────────┐
                                      │Plan_Execute │
                                      └─────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.10 or higher
- (Optional) API keys for LLM providers you plan to use

### Installation

```bash
# Clone the repository
git clone https://github.com/xianyu-sheng/Agent-hub.git
cd Agent-hub

# (Recommended) Create a virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .
```

### Configuration

Create a `.env` file in the project root:

```bash
# At least one LLM provider is required
OPENAI_API_KEY=sk-...
# or
DEEPSEEK_API_KEY=sk-...
# or
ANTHROPIC_API_KEY=sk-ant-...

# Optional: Agent search paths (comma-separated)
AGENT_PATH=./agents,~/my-agents
```

### Basic Usage

```bash
# Launch the REPL
agent-hub repl

# One-shot task execution
agent-hub run "Review the code in ./src for security issues"

# List discovered agents
agent-hub list

# Show agent details
agent-hub info omniagent
```

### Example: Multi-Agent Workflow

```
User: "Fix the bug in main.py and update my resume to mention it"

Agent Hub:
  1. [Router] Parses intent → two tasks: code fix + resume update
  2. [DAG Builder] Creates parallel DAG:
     ┌─────────────┐
     │─ OmniAgent  │── Code fix for main.py
     │─ Resume-Sync│── Update resume
     └─────────────┘
  3. [Strategy] Assigns "reflection" to OmniAgent, "pipeline" to Resume-Sync
  4. [Executor] Runs both in parallel, streams results to user
```

---

## REPL Usage

Agent Hub includes a rich REPL interface built with [Rich](https://github.com/Textualize/rich).

```
$ agent-hub repl
╔════════════════════════════════════════════════════════╗
║                   Agent Hub REPL                       ║
║         Multi-Agent Orchestration System               ║
╚════════════════════════════════════════════════════════╝

Detected agents:
  [1] omniagent   - AI Coding Assistant (CLI / MCP)
  [2] resume-sync - Resume Auto-Sync (CLI)
  [3] SmartBench  - Code Diagnostic Platform (CLI / MCP)

agent-hub> Review security of current project
╭─ Router ───────────────────────────────────────────────╮
│ Intent: code_review                                    │
│ Agent: SmartBench                                      │
│ Strategy: debate                                       │
│ Confidence: 87%                                        │
╰────────────────────────────────────────────────────────╯
╭─ Execution ────────────────────────────────────────────╮
│ [SmartBench] Diagnosing... ━━━━━━━━━━━━ 100% 0:00:05   │
│ [SmartBench] Found 3 potential issues                  │
│ [SmartBench] Generating fix recommendations...         │
╰────────────────────────────────────────────────────────╯
```

### REPL Commands

| Command | Description |
|---|---|
| `help` | Show available commands |
| `agents` / `list` | List all discovered agents |
| `info <name>` | Show agent details |
| `status` | Show system status and running tasks |
| `history` | Show conversation history |
| `clear` | Clear conversation history |
| `exit` / `quit` | Exit the REPL |

---

## Command Reference

### CLI Commands

| Command | Description |
|---|---|
| `agent-hub repl` | Launch the interactive TUI REPL |
| `agent-hub run <task>` | One-shot task execution |
| `agent-hub list` | List discovered agents |
| `agent-hub info <name>` | Show agent details |
| `agent-hub status` | Show system status |
| `agent-hub cron list` | Show scheduled cron jobs |
| `agent-hub cron add <schedule>` | Add a cron job |
| `agent-hub cron remove <id>` | Remove a cron job |

### CLI Options

| Option | Description |
|---|---|
| `--config <path>` | Path to config file |
| `--agent-path <path>` | Additional path to search for agent.yaml files |
| `--llm <provider>` | LLM provider to use (openai, deepseek, etc.) |
| `--model <name>` | Model name override |
| `--verbose` / `-v` | Verbose output |
| `--quiet` / `-q` | Quiet mode (minimal output) |

---

## Agent Manifest Protocol

The Agent Manifest protocol is the contract between Agent Hub and any professional agent. Each agent project adds a single `agent.yaml` file at its root.

### Schema

```yaml
name: omniagent                            # Unique agent identifier
display_name: "OmniAgent"                  # Human-readable name
description: "AI-powered coding assistant  # What this agent does
  with multi-model support, MCP tools,
  and ReAct workflows."

protocol: cli                              # Communication protocol
                                           # Options: cli | internal | mcp | http

capabilities:
  - task: code_generation                   # Task identifier
    description: "Generate code from        # Task description
      natural language descriptions"
    interface:                              # How to invoke this task
      command: "omniagent run"
      args: "{query}"
      # Template variables:
      #   {query}      - Original user input
      #   {session_id} - Current session ID
      #   {context}    - Conversation context
    examples:                               # Example queries for this task
      - "Create a Python REST API with FastAPI"
      - "Write a binary search in Rust"

  - task: code_review
    description: "Review code for bugs,
      security issues, and best practices"
    interface:
      command: "omniagent review"
      args: "{query}"
    examples:
      - "Review auth.py for security issues"
      - "Check my merge request for bugs"
```

### Protocol Types

| Protocol | Description |
|---|---|
| `cli` | Invoke via command line subprocess. Requires `command` and `args` in the interface definition. |
| `internal` | Direct Python import and function call. Agent Hub loads the agent as a Python module. |
| `mcp` | Model Context Protocol — communicate via stdio or SSE transport. |
| `http` | Invoke via HTTP request to a running service. Requires `url` in the interface definition. |

### Agent Discovery

Agent Hub discovers agents by searching for `agent.yaml` files in:

1. Paths specified in the `AGENT_PATH` environment variable (comma-separated).
2. Default search paths: `./agents/`, `~/.agent-hub/agents/`.
3. Paths passed via the `--agent-path` CLI option.

---

## Readiness Check

Agent Hub performs a three-layer readiness check at startup:

```
Layer 1: Welcome Banner
  ┌────────────────────────────────────────────┐
  │  Agent Hub v0.x.x                          │
  │  Multi-Agent Orchestration System          │
  │  Agents discovered: 3                      │
  └────────────────────────────────────────────┘

Layer 2: Pre-Execution Check
  ✓ Agent manifests valid
  ✓ LLM provider configured
  ✓ Network connectivity OK

Layer 3: LLM Call Failure Guidance
  ✗ API call failed: rate limited
  → Guidance: "You've hit the rate limit.
    Retry in 30 seconds or switch to a
    different provider with --llm deepseek"
```

---

## Testing

Agent Hub includes 30 tests covering:

- **Agent Manifest Protocol**: Schema validation, field parsing, optional field defaults.
- **Real Project agent.yaml Validation**: Validates manifests from omniagent, resume-sync, and SmartBench.
- **Routing Logic**: Intent parsing, confidence scoring, fallback behavior.
- **DAG Construction**: Multi-agent dependency resolution, cycle detection.

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=agent_hub --cov-report=term-missing

# Run specific test file
pytest tests/test_manifest.py
```

---

## Project Map

```
agent-hub/
├── agent_hub/                 # Core package
│   ├── __init__.py
│   ├── router.py              # Smart routing engine
│   ├── dag/                   # DAG scheduler
│   │   ├── builder.py
│   │   └── executor.py
│   ├── strategies/            # 7 collaboration strategies
│   │   ├── fan_out.py
│   │   ├── debate.py
│   │   ├── reflection.py
│   │   ├── vote.py
│   │   ├── plan_execute.py
│   │   ├── hitl.py
│   │   └── pipeline.py
│   ├── manifest/              # Agent Manifest protocol
│   │   ├── parser.py
│   │   └── validator.py
│   ├── memory/                # Session memory
│   │   └── session.py
│   ├── cron/                  # Cron scheduler
│   │   └── scheduler.py
│   ├── watchdog/              # Process watchdog
│   │   └── monitor.py
│   └── cli/                   # CLI & REPL
│       ├── main.py
│       └── repl.py
├── tests/                     # Test suite (30 tests)
│   ├── test_manifest.py
│   ├── test_routing.py
│   └── test_strategies.py
├── agents/                    # Sample agent manifests
│   ├── omniagent/
│   │   └── agent.yaml
│   ├── resume-sync/
│   │   └── agent.yaml
│   └── SmartBench/
│       └── agent.yaml
├── requirements.txt
├── setup.py
└── README.md
```

---

## FAQ

**Q: How is Agent Hub different from other agent frameworks (LangChain, AutoGen, CrewAI)?**

Agent Hub is designed around **zero-intrusion** and **decoupled discovery**. Unlike frameworks that require you to import SDKs or subclass base classes, Agent Hub simply reads `agent.yaml` files from existing projects. It's an orchestrator, not a framework — your agents remain completely independent.

**Q: Can I add my own agent without modifying Agent Hub?**

Yes. Write an `agent.yaml` file in your project root, place it somewhere Agent Hub can discover it (via `AGENT_PATH` or `--agent-path`), and Agent Hub will automatically detect it. Zero code changes to Agent Hub or your project.

**Q: What LLM providers are supported?**

Agent Hub is provider-agnostic. It can work with OpenAI, DeepSeek, Anthropic Claude, Google Gemini, Qwen, Ollama (local), and any OpenAI-compatible API endpoint.

**Q: How does the DAG scheduler handle failures?**

The watchdog monitors all running processes. If a process crashes, it attempts up to 3 restarts within 5 minutes. If exceeded, the agent is marked as failed. The DAG executor receives the failure status and can trigger fallback strategies or notify the user.

**Q: Can I use Agent Hub for production workloads?**

Agent Hub is currently in **beta**. It has been tested with 30 tests covering core protocols and real agent manifests, but production use may require additional hardening for your specific environment.

---

## License

[MIT](LICENSE) © Xianyu Sheng

---

<p align="center">
  <sub>Built with ❤️ for the open-source AI agent community</sub>
</p>
