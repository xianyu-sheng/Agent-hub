# 🚀 Agent Hub — 解耦的多 Agent 调度系统

<p align="center">
  <strong>直接输入自然语言，自动路由到专业 Agent 协同工作</strong>
</p>

Agent Hub 是一个**解耦的多 Agent 中央调度系统**。通过 `agent.yaml` 自描述清单发现专业 Agent，利用 LLM 意图路由将用户自然语言任务分解为跨 Agent 的 DAG，并行调度执行，并在终端中以 Rich TUI 仪表盘实时展示。

**核心理念**：不是再做一个 Agent，而是设计一套让多个专业 Agent **协同工作**的系统——Agent Hub 是粘合剂，专业 Agent 是积木。

---

## 🎯 快速体验

```bash
pip install -e .
agent-hub                            # 进入交互式命令中心
# 直接输入自然语言即可：
agent-hub> 分析 agent-hub 代码质量并更新简历
```

**无需 `run` 前缀**，直接说人话。

---

## 🧠 设计理念

### 三层解耦架构

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 1: Agent Hub (orchestrator)                            │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌───────────────┐  │
│  │ Prompt   │ │ DAG      │ │ Multi-Agent│ │ Visual        │  │
│  │ Optimizer│ │ Scheduler│ │ Aggregator │ │ Dashboard     │  │
│  │ (LLM)    │ │ (Kahn)   │ │ (LLM)      │ │ (Rich TUI)    │  │
│  └──────────┘ └──────────┘ └───────────┘ └───────────────┘  │
└─────────────────────┬────────────────────────────────────────┘
                      │ Agent Manifest (agent.yaml)
                      │ — 只读，永不修改
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│  omniagent  │ │ resume-sync │ │  SmartBench │
│  4 tasks    │ │  6 tasks    │ │  3 tasks    │
│  CLI bridge │ │  CLI bridge │ │  CLI bridge │
└─────────────┘ └─────────────┘ └─────────────┘
```

### 设计原则

- **零侵入**：每个专业项目只新增一个 `agent.yaml`，不改任何代码
- **交互式引导**：所有命令支持无参交互模式，逐步引导用户操作
- **自然语言优先**：直接输入任务描述，无需记忆命令格式
- **按需调用**：Agent 在 `run` 时通过子进程拉起，无需常驻后台
- **智能兜底**：模型未配置/Key 未设置时给出精确的修复命令

---

## 📸 界面展示

### 交互式命令中心 (REPL)

```
──────────────── 🔄 Agent Hub — 多 Agent 中央调度系统 ─────────────────
v0.1.0 · 直接输入自然语言或命令 · help 查看帮助 · quit 退出

                      📋 系统概览
┌────────────────┬─────────────────────────────────────┐
│ 已注册 Agent   │ 🔄 agent-hub ✅ (internal, 4 tasks) │
│                │ 🔍 omniagent ✅ (cli, 4 tasks)      │
│                │ 📄 resume-sync ✅ (cli, 6 tasks)    │
│                │ 🧪 smartbench ✅ (cli, 3 tasks)     │
│ 已配置模型     │ ⭐ deepseek-v4-pro (deepseek)       │
│ 系统状态       │ 🟢 运行中                           │
└────────────────┴─────────────────────────────────────┘
💡 已就绪: 直接输入任务描述开始工作

agent-hub > 分析 smartbench 代码质量并更新简历    ← 直接说人话
```

### 启动仪表盘 (Start TUI)

```
┌──────────────────────────────────────────────────────────┐
│           Agent Hub — 多 Agent 中央调度系统                │
├────────────────────┬─────────────────────────────────────┤
│  🤖 Agent 状态      │  📊 系统信息                        │
│  ● 🔍 omniagent    │  📦 模型: ⭐ deepseek-v4-pro        │
│    ✅ 就绪          │  统计: 4 Agent · 3 外部 · 1 模型    │
│  ● 📄 resume-sync  │                                     │
│    ✅ 就绪          │  💡 直接输入任务描述即可工作         │
│  ● 🧪 smartbench   │                                     │
│    ✅ 就绪          │                                     │
├────────────────────┴─────────────────────────────────────┤
│  ✅ 全部 3 个 Agent 就绪！系统已启动                      │
└──────────────────────────────────────────────────────────┘
```

---

## 📦 安装

```bash
git clone https://github.com/xianyu-sheng/Agent-hub.git
cd Agent-hub
pip install -e .
agent-hub --version
```

### 配置 LLM 模型

Agent Hub 提供**交互式引导**，只需模型名 + API Key：

```bash
# 交互模式（推荐）
agent-hub models add
# → 1/2 模型名称: deepseek-v4-pro
# → 自动识别: deepseek → https://api.deepseek.com
# → 2/2 API Key: DEEPSEEK_API_KEY

# 命令行模式
agent-hub models add gpt-4o -k OPENAI_API_KEY
```

支持自动识别：deepseek / claude / gpt / qwen / glm / doubao / moonshot / kimi / gemini / ollama

### 注册专业 Agent

```bash
# 交互模式
agent-hub agent register
# → 项目路径: D:/SmartBench

# CLI 模式
agent-hub agent register D:/OmniAgent_CLI
```

---

## 🪄 完整命令参考

### 交互式命令中心

```bash
agent-hub                    # 进入 REPL，直接输入自然语言或命令
```

REPL 内支持所有子命令，且**自然语言自动路由**：

```
agent-hub > 你好，帮我分析项目代码并更新简历
→ 自动识别为任务 → 路由到 smartbench + resume-sync → 执行
```

### 模型配置

```bash
agent-hub models add              # 交互式引导添加（只需模型名 + Key）
agent-hub models add <name> -k <KEY>  # 快速添加（自动识别供应商/API）
agent-hub models list             # 列出已配置模型
agent-hub models remove           # 交互式选择删除
agent-hub models update           # 交互式引导更新
agent-hub models info [name]      # 模型详情
agent-hub models priority         # 查看/设置优先级
```

### Agent 管理

```bash
agent-hub agent register          # 交互式引导注册
agent-hub agent list              # 列出所有已注册 Agent
agent-hub agent info [name]       # Agent 详情（可选指定）
agent-hub agent validate          # 验证所有 agent.yaml
agent-hub agent models [name]     # 查看 Agent 声明的模型
agent-hub agent reload            # 重载配置
```

### 系统控制

```bash
agent-hub start                   # 验证所有 Agent 就绪状态 (TUI 仪表盘)
agent-hub stop                    # 停止通过 start 启动的进程
agent-hub status                  # 查看运行状态
```

### 任务执行

```bash
agent-hub run "分析代码并更新简历"  # 带仪表盘的多 Agent 调度
agent-hub run --no-dashboard "..." # 纯命令行模式
agent-hub run                      # 交互式任务模式
```

---

## 🛡️ 兜底机制

未配置模型或 API Key 时，系统不会报晦涩的技术错误，而是**给出精确的修复命令**：

```
⚠ 已配置 1 个模型，但 API Key 均未设置: deepseek-v4-pro
  设置环境变量: set DEEPSEEK_API_KEY=sk-xxxx
  或更新模型: models update deepseek-v4-pro -k sk-xxxx
```

三层检查：
1. **欢迎横幅** — 进入即显示就绪状态
2. **任务执行前** — 前置拦截，未就绪直接返回指引
3. **LLM 调用失败** — 列出尝试的模型 + 排查建议

---

## 📋 Agent Manifest 协议

每个专业 Agent 项目根目录下放置 `agent.yaml`：

```yaml
name: my-agent
display_name: "我的 Agent"
description: |
  描述此 Agent 的功能和特点（供 LLM 路由使用）。
protocol: cli
version: "1.0"

capabilities:
  tasks:
    - name: do_something          # 任务名（需匹配实际 CLI 命令）
      description: 执行某个操作
      input:
        goal: "string — 任务目标"
      output:
        result: "string — 执行结果"

interface:
  command: my-agent {task} --json-output "{goal}"  # CLI 命令模板
```

### 命令模板占位符

| 占位符 | 来源 | 示例 |
|--------|------|------|
| `{task}` | 任务名 | `analyze_code` |
| `{goal}` | params.goal | `分析项目结构` |
| `{mode}` | params.mode | `react` |
| `{project}` | params.project_path | `D:/project` |
| `{params_json}` | 完整 params JSON | `{"goal":"..."}` |

### 协议类型

| 协议 | 说明 |
|------|------|
| `cli` | CLI 子进程调用（默认） |
| `internal` | 进程内调度（agent-hub 自身，防递归） |
| `mcp` | MCP 协议（预留） |
| `http` | HTTP 接口（预留） |

### 调度关系声明 (`scheduled_agents`)

当一个 Agent 是**调度/编排系统**（通过 CLI Bridge 调度其他 Agent）时，应在 `agent.yaml` 中声明 `scheduled_agents`，描述它调度的子 Agent 及其角色：

```yaml
# agent-hub 的 agent.yaml 示例
name: agent-hub
display_name: "Agent Hub Scheduler"
protocol: internal

scheduled_agents:
  - name: omniagent
    repo: D:/OmniAgent_CLI
    role: "通用 AI 编程 Agent — 代码分析、生成、重构、命令执行"
    interface: cli
  - name: smartbench
    repo: D:/SmartBench
    role: "代码质量诊断引擎 — 静态分析、性能基准、项目指纹"
    interface: cli
  - name: resume-sync
    repo: D:/工作/resume-sync
    role: "简历自动同步器 — Git 变更检测 → LLM 生成 → LaTeX 编译"
    interface: cli
```

**字段说明**：

| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | ✅ | 子 Agent 名称（需与对应 agent.yaml 中的 `name` 一致） |
| `repo` | ✅ | 子 Agent 项目根目录的绝对路径 |
| `role` | ✅ | 子 Agent 在调度系统中的角色（1-2 句话，供 resume-sync 等下游工具生成简历时使用） |
| `interface` | ❌ | 调度接口类型（`cli` / `mcp` / `http`，默认 `cli`） |

**用途**：

- **resume-sync 集成**：生成简历要点时自动读取 `scheduled_agents`，注入子项目关系上下文，使 LLM 产出体现系统架构层级的描述（例如"设计了一套多 Agent 协同调度系统"而非"做了 3 个独立项目"）
- **健康检查**（规划中）：`agent-hub start` 仪表盘可按调度关系展示拓扑
- **文档自生成**：下游工具可据此生成架构图、依赖关系图

> 💡 **设计原则**：`scheduled_agents` 描述的是"调度关系"，不是"依赖关系"。如果 Agent A 只是调用了 Agent B 的 API（而非通过 Agent Manifest 协议调度），不应列在此处。

---

## 🏗️ 架构

```
agent_hub/
├── __init__.py          # 版本 0.1.0
├── manifest.py          # AgentManifest 数据模型 + YAML 加载 + Agent 发现
├── bridge.py            # CLI Bridge 子进程调用 + Agent 生命周期管理
├── router.py            # LLM 意图路由 + 输入优化器
├── scheduler.py         # 中央调度器：发现 → 路由 → DAG 执行 → 汇总
├── dashboard.py         # Rich TUI 仪表盘 (AgentDashboard + StartDashboard)
├── llm.py               # 异步 OpenAI-compatible chat completion 客户端
├── model_config.py      # 模型配置管理 (models.yaml CRUD + 就绪检查)
├── pid_store.py         # 跨 CLI 调用的进程状态持久化
└── cli.py               # Click 命令行入口 + 交互式 REPL
```

### 调度流水线

```
用户输入 "分析代码质量并更新简历"
    │
    ▼
┌───────────────────┐
│ 0. 就绪检查        │  ← check_system_ready() — 模型+Key 是否可用
├───────────────────┤
│ 1. Agent 发现      │  ← 扫描 agents.d/*.yaml → 加载 agent.yaml
├───────────────────┤
│ 2. 输入优化 (LLM)  │  ← Prompt Optimizer 丰富任务映射指引
├───────────────────┤
│ 3. 意图路由 (LLM)  │  ← 分解为跨 Agent DAG
├───────────────────┤
│ 4. DAG 波次计算    │  ← Kahn 拓扑排序 → 并行波次
├───────────────────┤
│ 5. 并行执行        │  ← asyncio.gather + CLI Bridge 子进程
├───────────────────┤
│ 6. LLM 汇总        │  ← 整合多 Agent 输出 → 连贯结论
└───────────────────┘
```

### 智能路由

- **输入优化**：自动追加精确任务名索引，防止 LLM 臆造不存在的任务
- **模糊匹配**：LLM 输出的任务名自动修正到最接近的实际任务
- **规则回退**：LLM 不可用时基于关键词的规则路由
- **依赖自动清理**：移除无效的 depends_on 引用

---

## 🔬 测试

```bash
pip install -e ".[dev]"
pytest tests/ -v           # 30 tests
```

---

## 🩺 故障排查

### 任务执行超时（300s Timeout）

**症状**：`agent-hub run "编写代码"` 路由到 omniagent 后挂起，300 秒超时。

**根因**：omniagent 收到任务后进入交互式 REPL（`Prompt.ask()`），与 agent-hub 竞争终端 stdin，导致永久阻塞。

**修复**（两个层面）：

1. **OmniAgent ≥ feat/headless-execution**：新增 `--goal` 参数支持非交互模式（自动批准工具调用、结果输出到 stdout 后退出）。agent.yaml 命令模板已更新为：
   ```
   omniagent --mode {mode} --goal "{goal}"
   ```

2. **Agent-hub ≥ fix/stdin-devnull**：bridge.py 子进程启动时设置 `stdin=DEVNULL`，防止任何 Agent 意外竞争终端输入。即使 Agent 进入 REPL，也会因 EOF 立即退出而非挂起。

**验证**：
```bash
# 直接测试 omniagent headless 模式
omniagent --mode react --goal "写一个 Python 快速排序函数"

# 全链路测试
agent-hub run "写一个 Python 快速排序函数"
```

### Agent 命令未找到

确保 Agent 的 CLI 入口已安装到 PATH（如 `pip install -e .`），或 agent.yaml 中 `interface.command` 使用完整路径。

---

## 🗺️ 路线图

- [x] Agent Manifest 协议 (agent.yaml)
- [x] CLI Bridge + Agent 生命周期管理
- [x] LLM 意图路由 → 跨 Agent DAG
- [x] 输入优化器 (Prompt Optimizer)
- [x] Rich TUI 仪表盘 (AgentDashboard + StartDashboard)
- [x] DAG 并行调度 (Kahn 波次)
- [x] 交互式 REPL (agent-hub 默认命令)
- [x] 自然语言自动路由（无需 run 前缀）
- [x] 模型配置管理 (models add/remove/list/update/priority)
- [x] 就绪检查 + 用户友好错误指引
- [x] SmartBench 外部 Agent 调度验证
- [ ] MCP/HTTP 协议支持
- [ ] Agent 健康检查自动重启
- [ ] 任务执行历史持久化

---

## 📄 许可

MIT License

---

## 👤 作者

**xianyu-sheng**

> 从「做了很多项目」到「设计了一个 Agent 系统」—— Agent Hub 是多 Agent 协同调度的思考和实现。
