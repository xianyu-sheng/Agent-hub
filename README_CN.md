# 🚀 Agent Hub — 解耦的多 Agent 调度系统

<p align="center">
  <strong>直接输入自然语言，自动路由到专业 Agent 协同工作</strong>
  <br>
  <em>7 种协作策略 · 有记忆的调度系统 · 定时自主运行</em>
</p>

Agent Hub 是一个**解耦的多 Agent 中央调度系统**。通过 `agent.yaml` 自描述清单发现专业 Agent，利用 LLM 意图路由将用户自然语言任务分解为跨 Agent 的 DAG，根据**协作策略**（辩论/反思/投票/人机协同...）执行调度，并在终端中以 Rich TUI 仪表盘实时展示。

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

### 定时调度

```bash
agent-hub schedule list                              # 列出所有定时任务
agent-hub schedule add <name> \                      # 添加定时任务
  --cron "0 9 * * 1-5" --agent <agent> --task <task>
agent-hub schedule remove [name]                     # 删除定时任务
agent-hub schedule run [name]                        # 手动触发一次
agent-hub schedule history                           # 查看执行历史 (最近 20 条)
```

### 任务执行

```bash
agent-hub run "分析代码并更新简历"  # 带仪表盘的多 Agent 调度（自动选择协作策略）
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
├── __init__.py          # 版本 0.2.0
├── manifest.py          # AgentManifest 数据模型 + YAML 加载 + Agent 发现
├── bridge.py            # CLI Bridge 子进程调用 + Agent 生命周期 + Watchdog 自愈
├── router.py            # LLM 意图路由 + 输入优化 + 协作策略推断 + 路由记忆
├── scheduler.py         # 中央调度器：策略分派 → DAG 执行 → 循环控制 → 汇总
├── dashboard.py         # Rich TUI 仪表盘 (AgentDashboard + HealthDashboard)
├── llm.py               # 异步 OpenAI-compatible chat completion 客户端
├── model_config.py      # 模型配置管理 (models.yaml CRUD + 就绪检查)
├── session_store.py     # 跨轮次会话记忆 (上下文注入 + 指代消解)
├── cron.py              # 定时调度引擎 (5 字段 cron + 执行历史)
├── pid_store.py         # 跨 CLI 调用的进程状态持久化
└── cli.py               # Click 命令行入口 + 交互式 REPL
```

### 调度流水线

```
用户输入 "将 omniagent 代码质量提升到 A 级"
    │
    ▼
┌───────────────────┐
│ 0. 会话上下文注入  │  ← SessionStore.get_recent_context(3) — 最近 3 轮历史
├───────────────────┤
│ 1. 就绪检查        │  ← check_system_ready() — 模型+Key 是否可用
├───────────────────┤
│ 2. Agent 发现      │  ← 扫描 agents.d/*.yaml → 加载 agent.yaml
├───────────────────┤
│ 3. 路由记忆检查    │  ← routing_memory.json — 相同请求跳过 LLM
├───────────────────┤
│ 4. 意图路由 (LLM)  │  ← 分解为跨 Agent DAG + 推断协作策略 + 置信度
├───────────────────┤
│ 5. 策略分派        │  ← 根据 strategy 选择执行模式
│   ├─ fan_out       │     并行分派 → 汇总
│   ├─ debate        │     A诊断 → B修复 → 循环直到通过阈值
│   ├─ reflection    │     执行 → 自审 → 改进（循环）
│   ├─ vote          │     多模型并发 → 比较差异 → 选最佳
│   ├─ plan_execute  │     先规划 → 按步执行 → 失败重规划
│   └─ hitl          │     逐任务执行 → 审批关卡暂停等人类确认
├───────────────────┤
│ 6. DAG 波次/循环   │  ← Kahn 拓扑排序 (fan_out) 或 迭代循环 (debate/reflection)
├───────────────────┤
│ 7. LLM 汇总        │  ← 整合多 Agent 输出 → 连贯结论
├───────────────────┤
│ 8. 会话保存        │  ← 保存到 .agent_hub/sessions/ + 高置信度路由记忆
└───────────────────┘
```

### 协作策略

Agent Hub 不只是把任务分派给 Agent，它根据任务性质**自动选择协作模式**：

| 策略 | 触发场景 | 工作方式 |
|------|---------|---------|
| **fan_out** `并行分派` | 独立任务、信息查询 | 并行执行 → LLM 汇总（默认） |
| **debate** `辩论-修复` | "提升/优化/修复" | A 诊断 → B 修复 → A 再诊断 → 循环直到通过阈值 |
| **reflection** `自反思` | "写/创作/生成" | 执行 → 自审查 → 改进 → 循环 |
| **vote** `多视角投票` | "评估/对比/审查" | 同一问题 → 多模型 → 比较差异 → 选最佳 |
| **plan_execute** `规划-执行` | "实现/开发/构建" | 先规划分步 → 按步执行 → 失败自动重规划 |
| **hitl** `人机协同` | "部署/推送/发布" | 关键步骤暂停等人类 [Y]批准 [n]拒绝 [r]重试 |
| **pipeline** `串行管线` | 有严格先后依赖 | A→B→C 串行执行 |

**策略由 LLM 路由器自动推断**，用户无需指定。若 LLM 未指定策略，系统根据关键词自动回退选择（`CollaborationStrategy.default_for()`）。

### 智能路由

- **输入优化**：自动追加精确任务名索引，防止 LLM 臆造不存在的任务
- **协作策略推断**：LLM 分析任务性质 → 自动选择最佳协作模式（debate/reflection/vote...）
- **路由记忆**：相同请求命中时跳过 LLM，直接使用缓存路由（`routing_memory.json`）
- **置信度门禁**：路由置信度 < 0.7 时显示警告，防止错误路由静默执行
- **模糊匹配**：LLM 输出的任务名自动修正到最接近的实际任务
- **规则回退**：LLM 不可用时的规则路由（confidence=0.3）+ 自动策略检测
- **退出条件**：循环策略支持 `score >= 90`、`pass_rate > 0.9` 等表达式求值

---

## 🔬 测试

```bash
pip install -e ".[dev]"
pytest tests/ -v           # 30 tests (manifest 协议全覆盖 + 真实项目 agent.yaml 验证)
```

---

---

## 🔧 高级特性

### 会话记忆（Session Memory）

Agent Hub 自动记住每次调度的上下文，实现**跨轮次感知**：

```
agent-hub > 列出所有 agent
→ 路由: agent-hub.discover_capabilities → 发现 4 个 Agent

agent-hub > 再列一次    ← "再"被正确理解为指代上一轮
→ 读取 SessionStore 上下文 → 路由到相同任务
```

- 每轮调度自动保存到 `.agent_hub/sessions/`（JSON）
- 最近 3 轮上下文注入 LLM 路由 prompt，实现指代消解
- 自动清理最旧的会话文件（保留 50 轮）

### 路由反馈闭环（Routing Feedback）

路由错误可持续改进：

- **置信度门禁**：路由置信度 < 70% 时显示警告 + 路由分析
- **路由记忆**：用户确认的高置信度路由自动保存到 `routing_memory.json`
- **记忆优先**：相同请求命中时跳过 LLM 调用，直接使用缓存路由（confidence=0.9）
- **规则回退增强**：LLM 不可用时自动检测协作策略 + 检查路由记忆

### Agent 健康自愈（Watchdog）

Agent 进程异常退出后**自动重启**：

- 每 15 秒扫描注册表，发现 `desired_state == "running"` 但进程已退出 → 自动重启
- 5 分钟内最多重启 3 次（防止无限重启循环）
- 超过限制 → 标记为 `failed` + 日志告警
- `agent-hub stop` 主动停止 → 设置 `desired_state = "stopped"` → 不会自动重启

### 定时调度（Cron Scheduler）

Agent Hub 可以**自主定时执行任务**，无需人工触发：

```bash
# 每个工作日早上 9 点自动诊断代码质量
agent-hub schedule add daily-check \
  --cron "0 9 * * 1-5" \
  --agent smartbench \
  --task diagnose_code \
  --params '{"project": "omniagent"}'

# 查看任务和执行历史
agent-hub schedule list
agent-hub schedule history
```

- 纯 Python cron 引擎（零外部依赖）
- 支持标准 5 字段 cron：`*`、`*/N`、`1-5`、`0,30`
- 执行历史持久化到 `.agent_hub/cron_history.json`
- 调度器启动时自动加载并运行 cron 循环

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

### 已完成

- [x] Agent Manifest 协议 (agent.yaml)
- [x] CLI Bridge + Agent 生命周期管理 + Watchdog 自愈
- [x] LLM 意图路由 → 跨 Agent DAG + 协作策略推断
- [x] 输入优化器 (Prompt Optimizer)
- [x] Rich TUI 仪表盘 (AgentDashboard + StartDashboard)
- [x] DAG 并行调度 (Kahn 波次) + 循环策略引擎
- [x] 交互式 REPL (agent-hub 默认命令)
- [x] 自然语言自动路由（无需 run 前缀）
- [x] 模型配置管理 (models add/remove/list/update/priority)
- [x] 就绪检查 + 用户友好错误指引
- [x] 跨轮次会话记忆 (SessionStore + 上下文注入)
- [x] 路由反馈闭环 (置信度门禁 + routing_memory)
- [x] 7 种协作策略 (fan_out/debate/reflection/vote/plan_execute/hitl/pipeline)
- [x] 定时调度 (cron 引擎 + schedule 命令组)
- [x] Agent 健康自愈 (Watchdog 自动重启)
- [x] 协议完整性 (mcp/http 预留 + 防御性错误信息)

### 规划中

- [ ] MCP/HTTP 协议实现
- [ ] Agent 间直接通信（不经过 Hub 汇总）
- [ ] 多用户会话隔离
- [ ] Web Dashboard

---

## 📄 许可

MIT License

---

## 👤 作者

**xianyu-sheng**

> 从「做了很多项目」到「设计了一个 Agent 系统」—— Agent Hub 是多 Agent 协同调度的思考和实现。
