# 🚀 Agent Hub — 解耦的多 Agent 调度系统

<p align="center">
  <strong>从「做了很多项目」到「设计了一个 Agent 系统」</strong>
</p>

Agent Hub 是一个**解耦的多 Agent 中央调度系统**，通过 `agent.yaml` 自描述清单发现专业 Agent，利用 LLM 意图路由将用户自然语言任务分解为跨 Agent 的 DAG，并行调度执行，并在终端中以多面板可视化仪表盘实时展示数据流转。

**核心叙事**：不是再做一个 Agent，而是设计一套让多个专业 Agent **协同工作**的系统——Agent Hub 是粘合剂，专业 Agent 是积木。

---

## 🧠 设计理念

### 问题

你有三个专业项目：

| 项目 | 能力 | 独立运行良好，但… |
|------|------|-------------------|
| **omniagent** | 通用 AI 编程 Agent，25 种工具 | 只能手动串行调用 |
| **resume-sync** | 简历自动同步，LLM 三阶段流水线 | 不知道什么时候该更新 |
| **SmartBench** | 代码诊断引擎，多模型辩论 | 不知道诊断哪个项目 |

它们各自独立工作良好，但**彼此孤立**。跨项目的任务（如"分析 omniagent 代码质量，然后更新简历"）需要人工协调。

### 方案

Agent Hub 提供三层解耦架构：

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 1: Agent Hub (orchestrator)                            │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌───────────────┐  │
│  │ Intent   │ │ DAG      │ │ Multi-Agent│ │ Visual        │  │
│  │ Router   │ │ Scheduler│ │ Aggregator │ │ Dashboard     │  │
│  │ (LLM)    │ │ (Kahn)   │ │ (LLM)      │ │ (Rich TUI)    │  │
│  └──────────┘ └──────────┘ └───────────┘ └───────────────┘  │
└─────────────────────┬────────────────────────────────────────┘
                      │ Agent Manifest (agent.yaml)
                      │ — 只读，永不修改
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│  omniagent  │ │ resume-sync │ │  SmartBench │
│  4 tasks    │ │  5 tasks    │ │  3 tasks    │
│  CLI bridge │ │  CLI bridge │ │  CLI bridge │
└─────────────┘ └─────────────┘ └─────────────┘
 Layer 2: Agent Manifest          Layer 3: Professional Agents
 (agent.yaml, 每个项目+1文件)        (零代码改动)
```

### 设计原则

- **零侵入**：每个专业项目只新增一个 `agent.yaml`，不改任何代码
- **只读契约**：Agent Hub 只读取 `agent.yaml`，永不写入其他项目的文件
- **CLI Bridge**：通过子进程调用，无需引入任何 SDK
- **渐进升级**：预留 MCP/HTTP 协议，可平滑迁移
- **解耦边界清晰**：配置手动编辑，`agent-hub agent reload` 热加载

---

## 📸 可视化仪表盘

```
┌──────────────────────────────────────────────────────────┐
│  📋 任务 DAG                    │  📡 数据流转日志        │
│  ID  状态  Agent       任务     │  12:00:01 Hub → omni   │
│  1   🔄   🔍 omni      分析    │  12:00:15 omni → Hub    │
│  2   ⬡   📄 resume    生成    │  12:00:16 Hub → resume  │
│  3   ⬡   📄 resume    构建    │  12:00:45 resume → Hub   │
├────────────────────────────────┼──────────────────────────┤
│  🤖 Agent 实时输出              │  📊 最终输出             │
│  🟢 omniagent                  │  整合的多 Agent 报告     │
│    list_files: 142 files       │                         │
│    analyze: 发现 3 个模块      │                         │
│  ⚫ resume-sync                │                         │
│    (等待上游任务完成...)        │                         │
└──────────────────────────────────────────────────────────┘
```

- **TaskGraph**：DAG 节点实时状态（⬡ pending → 🔄 running → ✅ done / ❌ failed）
- **AgentPanels**：每个 Agent 独立输出面板，彩色边框区分，实时滚动
- **DataFlowLog**：Hub ↔ Agent 数据流转时间线
- **ResultPanel**：LLM 整合的最终输出

---

## 📦 安装

### 前置要求

- Python 3.10+
- `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 环境变量（用于 LLM 路由和汇总）

### 安装 Agent Hub

```bash
# 克隆仓库
git clone https://github.com/xianyu-sheng/Agent-hub.git
cd Agent-hub

# 安装（开发模式）
pip install -e .

# 验证安装
agent-hub --version
```

### 注册专业 Agent

Agent Hub 通过 `agents.d/` 目录发现 Agent。每个注册文件是一个 YAML，内含 `project_path` 指针：

```bash
# 注册一个 Agent（自动生成 agent.yaml 模板）
agent-hub agent register D:/OmniAgent_CLI
agent-hub agent register D:/工作/resume-sync
agent-hub agent register D:/SmartBench

# 查看注册状态
agent-hub agent list

# 验证所有 agent.yaml 合法性
agent-hub agent validate
```

---

## 🪄 使用方式

### 一键启动

```bash
# 启动 Agent Hub + 自动拉起所有注册的专业 Agent
agent-hub start

# 只启动指定 Agent
agent-hub start --agents omniagent,smartbench

# 查看运行状态
agent-hub status

# 停止所有 Agent
agent-hub stop
```

### 多 Agent 任务调度

```bash
# 带仪表盘的完整调度
agent-hub run "分析 omniagent 的代码架构质量，然后更新我的简历"

# 无仪表盘模式（纯命令行）
agent-hub run "同时诊断 omniagent 和 SmartBench 的代码质量" --no-dashboard

# 交互模式
agent-hub run
任务> 分析 omniagent 代码并更新简历
任务> quit
```

### Agent 配置管理（只读）

```bash
agent-hub agent list                 # 列出所有已注册 Agent
agent-hub agent info omniagent       # 查看 omniagent 完整配置
agent-hub agent validate             # 验证所有 agent.yaml 合法性
agent-hub agent reload               # 重载配置（手动编辑后）
agent-hub agent register <path>      # 注册新 Agent
```

---

## 📋 Agent Manifest 协议

### agent.yaml 规范

每个专业 Agent 项目根目录下放置一个 `agent.yaml`，这是 Agent Hub 与该 Agent 之间的**唯一契约**：

```yaml
# Agent Manifest — 自描述清单
# 此文件供 Agent-hub 读取，永不修改。配置变更请手动编辑。

name: my-agent                    # 唯一标识名（必填）
display_name: "我的 Agent"        # 人类可读名称
description: |                    # 详细描述，供 LLM 路由使用
  描述此 Agent 的功能和特点。
protocol: cli                     # 通信协议：cli | mcp | http
version: "1.0"

capabilities:
  tasks:                          # 可执行的任务列表（至少 1 个）
    - name: do_something          # 任务名（在 Agent 内唯一）
      description: 执行某个操作    # 自然语言描述（LLM 路由依赖此字段）
      input:                      # 期望的输入参数
        goal: "string — 任务目标"
      output:                     # 期望的输出
        result: "string — 执行结果"
      tools:                      # 该任务可用的工具（可选）
        - tool_a
        - tool_b

  tools: [global_tool]            # 全局工具（可选）
  models: [model_x]               # 支持的模型（可选）
  modes: [react]                  # 支持的模式（可选）
  constraints:                    # 约束条件（可选）
    max_concurrency: 3
    timeout: 600

interface:
  command: my-agent --task "{task}" --json-output "{goal}"  # CLI 命令模板（必填）
  working_dir: /path/to/project   # 工作目录（可选）
  env:                            # 额外环境变量（可选）
    PYTHONPATH: src
```

### 命令模板占位符

| 占位符 | 说明 | 示例 |
|--------|------|------|
| `{task}` | 任务名 | `analyze_code` |
| `{goal}` | 任务目标（来自 params.goal） | `分析项目结构` |
| `{mode}` | 运行模式 | `react` |
| `{project}` | 项目路径 | `D:/OmniAgent_CLI` |
| `{params_json}` | 完整 params 的 JSON | `{"goal":"...","mode":"react"}` |

### 协议预留：`update_own_config`

如果某个 Agent 将来需要「通过消息更新自身配置」的能力，只需在其 `capabilities.tasks` 中加入：

```yaml
- name: update_own_config
  description: 接收配置更新指令，修改自身的 agent.yaml
  input:
    field: "string — 要修改的字段路径"
    value: "any — 新值"
  output:
    status: "string"
    previous_value: "any"
```

Agent Hub 通过标准 CLI Bridge 调用此任务即可，无需特殊逻辑。当前版本不强制要求实现。

---

## 🏗️ 架构详解

### 模块职责

```
agent_hub/
├── __init__.py          # 版本 0.1.0
├── manifest.py          # AgentManifest 数据模型 + YAML 加载 + Agent 发现
├── bridge.py            # CLI Bridge 子进程调用 + Agent 生命周期管理
├── router.py            # LLM 意图路由：自然语言 → 跨 Agent 任务 DAG
├── scheduler.py         # 中央调度器：发现 → 路由 → DAG 执行 → 汇总
├── dashboard.py         # Rich TUI 多面板可视化仪表盘
├── llm.py               # 轻量 OpenAI-compatible chat completion 客户端
└── cli.py               # Click 命令行入口
```

### 调度流水线

```
用户输入 "分析 omniagent 代码质量，然后更新简历"
    │
    ▼
┌──────────────────┐
│ 1. Agent 发现     │  ← 扫描 agents.d/*.yaml → 加载各项目的 agent.yaml
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 2. 意图路由 (LLM) │  ← 分析意图 → {
│                  │       tasks: [
│                  │         {id:1, agent:"omniagent", task:"analyze_code", depends_on:[]},
│                  │         {id:2, agent:"resume-sync", task:"generate_bullets", depends_on:[1]},
│                  │         {id:3, agent:"resume-sync", task:"apply_and_build", depends_on:[2]}
│                  │       ]
│                  │     }
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 3. DAG 波次计算   │  ← Kahn 拓扑排序 → Wave 1: [id=1], Wave 2: [id=2], Wave 3: [id=3]
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 4. 并行执行       │  ← 波内 asyncio.gather, 波间串行, stdout → Dashboard 实时更新
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 5. LLM 汇总      │  ← 整合所有 Agent 输出 → 连贯结论
└──────────────────┘
```

### 路由规则

- LLM 路由成功 → 按 Agent 清单中的 agent + task 精确匹配
- 任务名模糊匹配 → 自动修正（如 LLM 输出 `analyze` → 匹配到 `analyze_code`）
- LLM 路由失败 → 关键词规则回退，保证至少有一个 Agent 被调用
- 无效依赖自动清理 → 移除不存在的依赖引用

### 并行策略

- **Kahn 算法**计算 DAG 波次：入度为 0 的节点组成一波
- **波内并行**：同一波的任务通过 `asyncio.gather` 同时执行
- **波间串行**：依赖满足后才进入下一波
- **异常隔离**：单个任务失败不影响同波其他任务

---

## 🔬 测试

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 29 个测试覆盖：
# - AgentTask 构造与序列化 (5 tests)
# - AgentCapabilities 构造 (3 tests)
# - AgentInterface 构造 (2 tests)
# - AgentManifest YAML 加载与验证 (12 tests)
# - Agent 发现与注册 (4 tests)
# - 实际项目 agent.yaml 加载 (3 tests)
```

---

## 🗺️ 路线图

- [x] Agent Manifest 协议 (agent.yaml)
- [x] CLI Bridge + Agent 生命周期管理
- [x] LLM 意图路由 → 跨 Agent DAG
- [x] Rich TUI 多面板仪表盘
- [x] DAG 并行调度 (Kahn 波次)
- [x] 一键启停 (agent-hub start/stop)
- [x] 只读配置管理 (agent register/list/info/validate/reload)
- [ ] MCP 协议支持（渐进升级路径）
- [ ] Agent 健康检查自动重启
- [ ] 任务执行历史持久化
- [ ] 多用户并发调度

---

## 📄 许可

MIT License

---

## 👤 作者

**xianyu-sheng**

> 从「做了很多项目」到「设计了一个 Agent 系统」—— Agent Hub 是多 Agent 协同调度的思考和实现。
