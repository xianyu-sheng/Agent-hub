"""Intent Router — LLM 驱动的多 Agent 任务分解与路由。

接收用户自然语言输入，分析意图并分解为跨 Agent 的任务 DAG。
使用 LLM 理解任务 → 匹配可用 Agent 的能力 → 输出带依赖关系的执行计划。

输出格式兼容 omniagent 的 PlanDAG，可直接复用 Kahn 拓扑排序。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from agent_hub.manifest import AgentManifest

logger = logging.getLogger(__name__)

# ── 路由结果数据结构 ──────────────────────────────────────────────


@dataclass
class RoutedTask:
    """路由后的单个任务。

    兼容 omniagent PlanStep — 可以包装为 PlanStep(id, task, depends_on)。
    """

    id: int
    agent: str  # 目标 Agent 名
    task: str  # 任务名（对应 AgentManifest.capabilities.tasks[].name）
    description: str = ""  # 自然语言描述
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutedTask:
        return cls(
            id=int(data.get("id", 0)),
            agent=str(data.get("agent", "")),
            task=str(data.get("task", "")),
            description=str(data.get("description", data.get("desc", ""))),
            params=data.get("params", data.get("input", {})),
            depends_on=[int(d) for d in (data.get("depends_on", []))],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "agent": self.agent,
            "task": self.task,
            "description": self.description,
        }
        if self.params:
            result["params"] = self.params
        if self.depends_on:
            result["depends_on"] = self.depends_on
        return result


@dataclass
class RoutePlan:
    """意图路由的完整结果 — 跨 Agent 的任务 DAG。"""

    tasks: list[RoutedTask]
    analysis: str = ""  # 意图分析摘要
    is_parallel: bool = False  # 是否有可并行的任务

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def agents_involved(self) -> list[str]:
        return sorted(set(t.agent for t in self.tasks))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "analysis": self.analysis,
            "is_parallel": self.is_parallel,
            "agents_involved": self.agents_involved,
        }


# ── Router ──────────────────────────────────────────────────────────


class IntentRouter:
    """LLM 驱动的意图路由器。

    将用户自然语言请求分解为跨 Agent 的任务 DAG。
    使用 LLM 理解任务语义 + 匹配 Agent 清单中的能力描述。

    使用方式：
        router = IntentRouter(model_priority=["claude-sonnet-4-6"])
        route_plan = await router.route(
            "分析 omniagent 代码质量，然后更新简历",
            agents=manifests,
        )
    """

    # Router 的 system prompt — 指导 LLM 输出任务 DAG
    ROUTER_SYSTEM_PROMPT = """你是一个多 Agent 任务调度器。你的职责是分析用户请求，
将其分解为可以由可用 Agent 执行的子任务，并确定任务之间的依赖关系。

## 可用 Agent 及其能力

{agent_descriptions}

## 输出格式

你必须输出一个 JSON 对象，格式如下：

```json
{{
  "analysis": "简要分析用户意图（1-2句话，中文）",
  "tasks": [
    {{
      "id": 1,
      "agent": "agent_name",
      "task": "task_name",
      "description": "该步骤做什么",
      "params": {{"goal": "...", ...}},
      "depends_on": []
    }},
    {{
      "id": 2,
      "agent": "agent_name",
      "task": "task_name",
      "description": "该步骤做什么",
      "params": {{...}},
      "depends_on": [1]
    }}
  ]
}}
```

## 规则

1. **agent** 必须是上面「可用 Agent」列表中列出的 name
2. **task** 必须是该 Agent 的「可执行的任务」列表中列出的任务名
3. **params** 必须匹配该任务的 input schema 中的字段
4. **depends_on** 标注该步骤依赖的前序步骤 ID 列表（无依赖则 []）
5. 可以独立执行的步骤标记为 depends_on: [] — 它们将被并行执行
6. 如果一个 Agent 无法完成用户请求，在 analysis 中说明原因，返回空 tasks
7. 只输出 JSON，不要输出其他文本"""

    def __init__(
        self,
        model_priority: list[str],
        *,
        fallback_rule_based: bool = True,
    ) -> None:
        self.model_priority = model_priority
        self.fallback_rule_based = fallback_rule_based

    async def route(
        self,
        user_input: str,
        agents: dict[str, AgentManifest],
        *,
        extra_context: str = "",
    ) -> RoutePlan:
        """分析用户意图并生成跨 Agent 任务 DAG。

        Args:
            user_input: 用户自然语言输入
            agents: 可用 Agent 字典 {name: AgentManifest}
            extra_context: 额外上下文（如对话历史）

        Returns:
            RoutePlan 包含分解后的任务列表
        """
        if not agents:
            return RoutePlan(
                tasks=[],
                analysis="未发现任何 Agent。请先注册: agent register <项目路径>",
            )

        # 构建 Agent 描述 → system prompt
        agent_descriptions = self._build_agent_descriptions(agents)

        # 优化用户输入：添加任务映射指引
        optimized_input = self._optimize_input(user_input, agents)

        # 调用 LLM 进行意图路由
        messages = [
            {"role": "system", "content": self.ROUTER_SYSTEM_PROMPT.format(
                agent_descriptions=agent_descriptions,
            )},
            {"role": "user", "content": optimized_input},
        ]

        if extra_context:
            messages.insert(1, {"role": "system", "content": extra_context})

        try:
            llm_output = await self._call_llm(messages)
            plan = self._parse_llm_output(llm_output, agents)

            # 验证并补充
            plan = self._validate_and_enrich(plan, agents)

            logger.info(
                "路由结果: %d 个任务, agents=[%s], parallel=%s",
                plan.task_count,
                ", ".join(plan.agents_involved),
                plan.is_parallel,
            )
            return plan

        except Exception as e:
            logger.warning("LLM 路由失败: %s，使用规则回退", e)
            if self.fallback_rule_based:
                return self._rule_based_route(user_input, agents)
            return RoutePlan(
                tasks=[],
                analysis=(
                    "LLM 路由失败。请检查模型配置:\n"
                    "  models add deepseek-v4-pro -k DEEPSEEK_API_KEY\n"
                    f"  错误详情: {e}"
                ),
            )

    # ── LLM 交互 ─────────────────────────────────────────────────

    async def _call_llm(self, messages: list[dict]) -> str:
        """调用 LLM，按 model_priority 尝试。"""
        from agent_hub.llm import chat_completion_from_config

        last_error = None
        tried_models: list[str] = []
        for model_id in self.model_priority:
            try:
                result = await chat_completion_from_config(
                    model_id=model_id,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.3,
                )
                if result and result.strip():
                    return result
                else:
                    tried_models.append(model_id)
            except Exception as e:
                last_error = e
                tried_models.append(f"{model_id}({e})")
                continue

        if not tried_models:
            tried_models = list(self.model_priority)
        raise RuntimeError(
            f"所有模型 ({', '.join(tried_models)}) 调用失败。"
            f"请检查: 1) models add 配置模型 2) API Key 已设置 3) 网络连接"
        )

    @staticmethod
    def _parse_llm_output(
        output: str,
        agents: dict[str, AgentManifest],
    ) -> RoutePlan:
        """解析 LLM 输出的 JSON → RoutePlan。"""
        # 尝试提取 JSON 块
        output = output.strip()

        # 移除 markdown 代码块标记
        if output.startswith("```"):
            lines = output.split("\n")
            # 移除首行 ```json 和末行 ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            output = "\n".join(lines)

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            # 尝试查找 JSON 块
            import re
            match = re.search(r'\{[\s\S]*"tasks"[\s\S]*\}', output)
            if match:
                data = json.loads(match.group(0))
            else:
                raise ValueError(f"无法解析 LLM 输出为 JSON: {output[:200]}...")

        analysis = str(data.get("analysis", ""))
        raw_tasks = data.get("tasks", [])

        tasks = [RoutedTask.from_dict(t) for t in raw_tasks if isinstance(t, dict)]

        # 检测并行性
        has_parallel = any(
            len(t.depends_on) == 0 for t in tasks
        ) and len(tasks) > 1

        return RoutePlan(
            tasks=tasks,
            analysis=analysis,
            is_parallel=has_parallel,
        )

    # ── 验证与补充 ──────────────────────────────────────────────

    def _validate_and_enrich(
        self,
        plan: RoutePlan,
        agents: dict[str, AgentManifest],
    ) -> RoutePlan:
        """验证路由结果并补充缺失信息。"""
        valid_tasks: list[RoutedTask] = []
        warnings: list[str] = []

        # 收集有效的步骤 ID（用于 depends_on 校验）
        valid_ids: set[int] = set()

        for task in plan.tasks:
            # 检查 Agent 存在
            if task.agent not in agents:
                warnings.append(f"未知 Agent '{task.agent}' — 跳过步骤 {task.id}")
                continue

            agent = agents[task.agent]

            # 检查任务存在
            if not agent.has_task(task.task):
                # 尝试模糊匹配
                matched = self._fuzzy_match_task(task.task, agent)
                if matched:
                    task.task = matched.name
                    warnings.append(
                        f"步骤 {task.id}: '{task.task}' → 匹配到 Agent '{task.agent}' 的 '{matched.name}'"
                    )
                else:
                    warnings.append(
                        f"Agent '{task.agent}' 不支持任务 '{task.task}' — 跳过步骤 {task.id}"
                    )
                    continue

            # 补充 params：如果缺少 goal 但有 description，用 description 填充
            task_info = agent.get_task(task.task)
            if task_info and not task.params.get("goal") and task.description:
                task.params["goal"] = task.description

            valid_tasks.append(task)
            valid_ids.add(task.id)

        # 清理无效的 depends_on 引用
        for task in valid_tasks:
            task.depends_on = [d for d in task.depends_on if d in valid_ids]

        if warnings:
            logger.warning("路由验证警告: %s", "; ".join(warnings))

        # 重新计算并行性
        has_parallel = len(valid_tasks) > 1 and any(
            len(t.depends_on) == 0 for t in valid_tasks
        )

        return RoutePlan(
            tasks=valid_tasks,
            analysis=plan.analysis,
            is_parallel=has_parallel,
        )

    @staticmethod
    def _fuzzy_match_task(
        target: str,
        agent: AgentManifest,
    ) -> AgentTask | None:
        """模糊匹配任务名（当 LLM 输出的任务名不完全匹配时）。

        Returns:
            匹配到的 AgentTask，或 None（未匹配）
        """
        from agent_hub.manifest import AgentTask

        target_lower = target.lower().replace(" ", "_").replace("-", "_")

        for task in agent.capabilities.tasks:
            task_lower = task.name.lower().replace(" ", "_").replace("-", "_")
            if target_lower == task_lower:
                return task
            # 子串匹配
            if target_lower in task_lower or task_lower in target_lower:
                return task

        return None

    # ── 规则回退 ─────────────────────────────────────────────────

    def _rule_based_route(
        self,
        user_input: str,
        agents: dict[str, AgentManifest],
    ) -> RoutePlan:
        """当 LLM 路由失败时，使用简单的关键词规则回退。

        基于关键词匹配选择 Agent，生成串行任务列表。
        """
        user_lower = user_input.lower()
        selected: list[RoutedTask] = []
        task_id = 0

        for agent_name, agent in agents.items():
            # 检查 agent 的 display_name 和 description 是否与用户输入相关
            name_lower = agent.display_name.lower() + " " + agent.description.lower()

            # 简单关键词匹配
            keywords = set()
            for task in agent.capabilities.tasks:
                for word in task.description.lower().split():
                    if len(word) > 3:
                        keywords.add(word)
                for word in task.name.lower().replace("_", " ").split():
                    if len(word) > 3:
                        keywords.add(word)

            relevance = sum(1 for kw in keywords if kw in user_lower)

            if relevance > 0 or agent_name.lower() in user_lower:
                # 选择该 Agent 的第一个任务
                if agent.capabilities.tasks:
                    task_id += 1
                    primary_task = agent.capabilities.tasks[0]
                    selected.append(RoutedTask(
                        id=task_id,
                        agent=agent_name,
                        task=primary_task.name,
                        description=primary_task.description,
                        params={"goal": user_input},
                        depends_on=[],
                    ))

        if not selected:
            # 完全无匹配 — 使用第一个 Agent
            first = next(iter(agents.values()))
            if first.capabilities.tasks:
                selected.append(RoutedTask(
                    id=1,
                    agent=first.name,
                    task=first.capabilities.tasks[0].name,
                    description=first.capabilities.tasks[0].description,
                    params={"goal": user_input},
                    depends_on=[],
                ))

        return RoutePlan(
            tasks=selected,
            analysis=f"规则回退路由：根据关键词匹配选择了 {len(selected)} 个 Agent",
            is_parallel=len(selected) > 1 and all(not t.depends_on for t in selected),
        )

    # ── 辅助 ─────────────────────────────────────────────────────

    def _optimize_input(
        self, user_input: str, agents: dict[str, AgentManifest]
    ) -> str:
        """优化用户输入 — 添加任务映射指引，帮助 LLM 精确匹配任务。

        类似 omniagent 的 prompt optimizer，在用户原始输入前追加结构化指引：
        1. 强调使用精确的任务名（不可臆造）
        2. 给出多步骤任务的拆分示例
        3. 优先匹配最相关的 Agent
        """
        # 构建任务快速索引
        task_index_lines: list[str] = []
        for name, agent in sorted(agents.items()):
            for t in agent.capabilities.tasks:
                task_index_lines.append(
                    f"  [{name}] {t.name} — {t.description}"
                )

        task_index = "\n".join(task_index_lines)

        return (
            f"{user_input}\n\n"
            f"--- 任务匹配指引 ---\n"
            f"请将上述用户需求分解为具体任务。务必使用以下精确任务名（不可臆造名称）:\n"
            f"{task_index}\n\n"
            f"规则:\n"
            f"- 如果用户说「更新简历」「同步简历」，优先路由到 resume-sync 的 run 任务\n"
            f"- 如果用户说「分析代码」「诊断项目」，优先路由到 smartbench 的 diagnose_code 或 fingerprint_project\n"
            f"- 如果用户说「写代码」「改bug」，优先路由到 omniagent 的对应任务\n"
            f"- 涉及多个 Agent 时，分析依赖关系（如先诊断再修改）\n"
            f"- params.goal 填入用户的具体需求描述（中文，完整保留原始语义）\n"
            f"- 任务名必须和上述索引中的完全一致，不要自创名称"
        )

    @staticmethod
    def _build_agent_descriptions(agents: dict[str, AgentManifest]) -> str:
        """构建供 LLM 路由使用的 Agent 能力描述文本。"""
        parts: list[str] = []
        for name, agent in agents.items():
            tasks_desc = "\n".join(
                f"    - {t.name}: {t.description}"
                + (f"\n      输入: {json.dumps(t.input, ensure_ascii=False)}" if t.input else "")
                for t in agent.capabilities.tasks
            )
            parts.append(
                f"### {agent.display_name} (name: {name})\n"
                f"描述: {agent.description}\n"
                f"可执行的任务:\n{tasks_desc}"
            )
        return "\n\n".join(parts)
