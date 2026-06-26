"""Central Scheduler — 多 Agent 任务调度器。

整合所有模块的中央调度引擎：
1. discover_agents()     — 发现可用 Agent
2. route(user_input)     — LLM 意图路由 → 任务 DAG
3. execute_dag(tasks)    — DAG 波次并行执行
4. aggregate(results)    — LLM 整合结果
5. render(result)        — 渲染到仪表盘

DAG 执行策略：
- 使用 Kahn 拓扑排序计算波次
- 波内任务并行执行（asyncio.gather + CLIBridge）
- 波间串行（依赖满足后才进入下一波）
- 每波结束后更新 Dashboard
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# ── Windows UTF-8 编码修复 ──────────────────────────────────────────
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console

from agent_hub.bridge import AgentProcessRegistry, CLIBridge
from agent_hub.dashboard import AgentDashboard, TaskNode
from agent_hub.manifest import AgentManifest, discover_from_registry
from agent_hub.router import IntentRouter, RoutePlan, RoutedTask

logger = logging.getLogger(__name__)

# ── 执行结果 ────────────────────────────────────────────────────────


@dataclass
class TaskExecutionResult:
    """单个任务的执行结果。"""

    task: RoutedTask
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0

    @property
    def summary(self) -> str:
        status = "✅" if self.success else "❌"
        return f"{status} [{self.task.agent}] {self.task.task} ({self.duration_ms:.0f}ms)"


@dataclass
class SchedulerResult:
    """调度器完整执行结果。"""

    user_input: str
    route_plan: RoutePlan
    task_results: list[TaskExecutionResult] = field(default_factory=list)
    aggregate: str = ""
    total_duration_ms: float = 0.0

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.task_results if r.success)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.task_results if not r.success)

    @property
    def is_success(self) -> bool:
        return self.fail_count == 0

    def summary_str(self) -> str:
        lines = [
            f"📊 调度完成: {self.success_count}/{len(self.task_results)} 成功",
            f"⏱ 总耗时: {self.total_duration_ms:.0f}ms",
        ]
        for r in self.task_results:
            lines.append(f"  {r.summary}")
        if self.aggregate:
            lines.append(f"\n📝 汇总:\n{self.aggregate[:500]}")
        return "\n".join(lines)


# ── 简易 DAG 波次计算（内联，不依赖 omniagent）────────────────────


def _compute_waves(tasks: list[RoutedTask]) -> list[list[RoutedTask]]:
    """使用 Kahn 算法计算任务 DAG 的并行波次。

    Args:
        tasks: 带 depends_on 的任务列表

    Returns:
        [[wave_0_tasks], [wave_1_tasks], ...]
    """
    from collections import deque

    task_map: dict[int, RoutedTask] = {t.id: t for t in tasks}
    in_degree: dict[int, int] = {}
    reverse_deps: dict[int, list[int]] = {t.id: [] for t in tasks}

    for t in tasks:
        valid_deps = [d for d in t.depends_on if d in task_map]
        in_degree[t.id] = len(valid_deps)
        for dep_id in valid_deps:
            reverse_deps[dep_id].append(t.id)

    # 起始波：所有入度为 0 的节点
    queue = deque(tid for tid, deg in in_degree.items() if deg == 0)

    if not queue:
        # 所有任务都有依赖 — 退化处理：按 ID 排序串行
        sorted_tasks = sorted(tasks, key=lambda t: t.id)
        return [[t] for t in sorted_tasks]

    waves: list[list[RoutedTask]] = []
    processed = 0

    while queue:
        wave_size = len(queue)
        current_wave: list[RoutedTask] = []

        for _ in range(wave_size):
            tid = queue.popleft()
            current_wave.append(task_map[tid])
            processed += 1

            for dependent_id in reverse_deps[tid]:
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

        waves.append(current_wave)

    # 未处理的任务（有环等）→ 追加到最后一波
    if processed < len(tasks):
        remaining = [t for t in tasks if t.id not in {tt.id for w in waves for tt in w}]
        waves.append(remaining)

    return waves


# ── 调度器 ──────────────────────────────────────────────────────────


class AgentScheduler:
    """多 Agent 中央调度器。

    完整流水线：
    1. 发现 Agent（从 agents.d/）
    2. 意图路由（LLM → 任务 DAG）
    3. DAG 波次并行执行
    4. LLM 汇总结果
    5. 渲染到仪表盘

    使用方式：
        scheduler = AgentScheduler(model_priority=["deepseek-v4-pro"])
        result = await scheduler.execute("分析 omniagent 代码并更新简历")
        print(result.aggregate)
    """

    def __init__(
        self,
        model_priority: list[str],
        *,
        registry_dir: str | None = None,
        console: Console | None = None,
        max_concurrent: int = 5,
        default_timeout: int = 300,
    ) -> None:
        self.model_priority = model_priority
        self.registry_dir = registry_dir
        self.console = console or Console(force_terminal=True)
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout

        # 子模块（延迟初始化）
        self._router: IntentRouter | None = None
        self._bridge: CLIBridge | None = None
        self._registry: AgentProcessRegistry | None = None
        self._dashboard: AgentDashboard | None = None
        self._agents: dict[str, AgentManifest] = {}

    @property
    def router(self) -> IntentRouter:
        if self._router is None:
            self._router = IntentRouter(self.model_priority)
        return self._router

    @property
    def bridge(self) -> CLIBridge:
        if self._bridge is None:
            self._registry = AgentProcessRegistry()
            self._bridge = CLIBridge(
                default_timeout=self.default_timeout,
                registry=self._registry,
            )
        return self._bridge

    @property
    def dashboard(self) -> AgentDashboard:
        if self._dashboard is None:
            self._dashboard = AgentDashboard(
                console=self.console,
                title="Agent Hub",
            )
        return self._dashboard

    # ── 主入口 ─────────────────────────────────────────────────────

    async def execute(
        self,
        user_input: str,
        *,
        agents: dict[str, AgentManifest] | None = None,
        show_dashboard: bool = True,
    ) -> SchedulerResult:
        """执行完整的多 Agent 调度流水线。

        Args:
            user_input: 用户自然语言输入
            agents: 预加载的 Agent 字典（不传则自动从 agents.d/ 发现）
            show_dashboard: 是否显示 Rich Live 仪表盘

        Returns:
            SchedulerResult 包含完整执行结果
        """
        start_time = time.monotonic()

        # Step 1: 发现 Agent
        if agents is None:
            agents = self._load_agents()
        self._agents = agents

        if not agents:
            return SchedulerResult(
                user_input=user_input,
                route_plan=RoutePlan(tasks=[], analysis="没有可用的 Agent"),
                total_duration_ms=(time.monotonic() - start_time) * 1000,
            )

        self.console.print(f"[dim]发现 {len(agents)} 个 Agent: {', '.join(agents.keys())}[/dim]")

        # Step 2: 意图路由
        self.console.print("[dim]🔍 分析意图...[/dim]")
        route_plan = await self.router.route(user_input, agents)

        if not route_plan.tasks:
            self.console.print(f"[yellow]⚠ {route_plan.analysis}[/yellow]")
            return SchedulerResult(
                user_input=user_input,
                route_plan=route_plan,
                total_duration_ms=(time.monotonic() - start_time) * 1000,
            )

        self.console.print(
            f"[dim]  路由结果: {route_plan.task_count} 个任务 "
            f"({', '.join(route_plan.agents_involved)})[/dim]"
        )

        # Step 3-5: DAG 执行 + Dashboard + 汇总
        if show_dashboard:
            result = await self._execute_with_dashboard(user_input, route_plan, agents)
        else:
            result = await self._execute_headless(user_input, route_plan, agents)

        result.total_duration_ms = (time.monotonic() - start_time) * 1000
        return result

    def _load_agents(self) -> dict[str, AgentManifest]:
        """从 agents.d/ 加载 Agent 清单。"""
        try:
            return discover_from_registry(self.registry_dir)
        except Exception as e:
            logger.error("加载 Agent 失败: %s", e)
            return {}

    # ── 仪表盘模式执行 ──────────────────────────────────────────

    async def _execute_with_dashboard(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
    ) -> SchedulerResult:
        """带 Rich Live 仪表盘的 DAG 执行。"""
        dash = self.dashboard

        # 初始化仪表盘组件
        # 任务图
        task_nodes = [
            TaskNode(
                id=t.id,
                agent=t.agent,
                task=t.task,
                description=t.description,
                depends_on=t.depends_on,
            )
            for t in route_plan.tasks
        ]
        dash.task_graph.set_nodes(task_nodes)

        # Agent 面板
        dash.ensure_panels(list(agents.keys()))

        # 数据流日志
        for task in route_plan.tasks:
            dash.dataflow.log_request(task.agent, task.task, task.params)

        # 执行结果收集
        task_results: list[TaskExecutionResult] = []

        # 计算波次
        waves = _compute_waves(route_plan.tasks)
        self.console.print(
            f"[dim]🔄 DAG 执行: {len(route_plan.tasks)} 步, {len(waves)} 波[/dim]"
        )

        with dash.run():
            for wave_idx, wave in enumerate(waves):
                wave_label = f"Wave {wave_idx + 1}/{len(waves)}"

                # 标记波内任务为 running
                for task in wave:
                    dash.task_graph.update_status(task.id, "running")

                dash.refresh()

                # 并行执行波内任务
                tasks_coros = [
                    self.bridge.execute(
                        manifest=agents[task.agent],
                        task_name=task.task,
                        params=task.params,
                        on_stdout=lambda line, ag=task.agent: dash.get_panel(ag).append(line),
                    )
                    for task in wave
                ]
                results = await asyncio.gather(*tasks_coros, return_exceptions=True)

                # 处理波次结果
                for task, result in zip(wave, results):
                    if isinstance(result, Exception):
                        exec_result = TaskExecutionResult(
                            task=task,
                            success=False,
                            error=str(result),
                        )
                    else:
                        exec_result = TaskExecutionResult(
                            task=task,
                            success=result.success,
                            output=result.output,
                            error=result.error,
                            duration_ms=result.duration_ms,
                        )

                    task_results.append(exec_result)

                    # 更新仪表盘
                    status = "done" if exec_result.success else "failed"
                    dash.task_graph.update_status(task.id, status)

                    # 数据流日志
                    result_summary = (
                        exec_result.output[:80] + "..."
                        if len(exec_result.output) > 80
                        else exec_result.output
                    )
                    dash.dataflow.log_response(task.agent, result_summary)

                    # Agent 面板状态
                    if task.agent in dash.agent_panels:
                        dash.agent_panels[task.agent].set_status(status)

                dash.refresh()

        # 汇总 — 在仪表盘内完成，避免退出 Live 后用户看到空白终端
        # 先显示"汇总中"状态，然后调用 LLM，最后更新结果面板
        dash.set_result("⏳ 正在汇总各 Agent 结果...")
        dash.refresh()
        aggregate = await self._aggregate(user_input, task_results, route_plan.analysis)
        dash.set_result(aggregate)
        dash.refresh()

        return SchedulerResult(
            user_input=user_input,
            route_plan=route_plan,
            task_results=task_results,
            aggregate=aggregate,
        )

    # ── 无仪表盘模式执行 ────────────────────────────────────────

    async def _execute_headless(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
    ) -> SchedulerResult:
        """无 UI 的纯命令行模式执行。"""
        task_results: list[TaskExecutionResult] = []

        waves = _compute_waves(route_plan.tasks)

        for wave_idx, wave in enumerate(waves):
            self.console.print(
                f"[dim]Wave {wave_idx + 1}/{len(waves)}: "
                f"{', '.join(f'[{t.agent}]{t.task}' for t in wave)}[/dim]"
            )

            tasks_coros = [
                self.bridge.execute(
                    manifest=agents[task.agent],
                    task_name=task.task,
                    params=task.params,
                )
                for task in wave
            ]
            results = await asyncio.gather(*tasks_coros, return_exceptions=True)

            for task, result in zip(wave, results):
                if isinstance(result, Exception):
                    exec_result = TaskExecutionResult(task=task, success=False, error=str(result))
                    self.console.print(f"  [red]✗[/red] [{task.agent}] {task.task}: {result}")
                else:
                    exec_result = TaskExecutionResult(
                        task=task,
                        success=result.success,
                        output=result.output,
                        error=result.error,
                        duration_ms=result.duration_ms,
                    )
                    status = "✓" if result.success else "✗"
                    color = "green" if result.success else "red"
                    self.console.print(
                        f"  [{color}]{status}[/{color}] [{task.agent}] {task.task} "
                        f"({result.duration_ms:.0f}ms)"
                    )

                task_results.append(exec_result)

        aggregate = await self._aggregate(user_input, task_results, route_plan.analysis)

        return SchedulerResult(
            user_input=user_input,
            route_plan=route_plan,
            task_results=task_results,
            aggregate=aggregate,
        )

    # ── 汇总 ────────────────────────────────────────────────────────

    async def _aggregate(
        self,
        user_input: str,
        task_results: list[TaskExecutionResult],
        analysis: str = "",
    ) -> str:
        """使用 LLM 汇总所有 Agent 的执行结果。"""
        if not task_results:
            return "未执行任何任务。"

        # 单任务直接返回
        if len(task_results) == 1 and task_results[0].success:
            return task_results[0].output

        # 构建汇总 prompt
        results_text = "\n\n".join(
            f"## [{r.task.agent}] {r.task.task}\n"
            f"状态: {'✅ 成功' if r.success else '❌ 失败'}\n"
            f"输出: {r.output[:800] if r.success else r.error[:800]}"
            for r in task_results
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个多 Agent 任务汇总专家。请根据各 Agent 的执行结果，"
                    "给出最终的完整回答。整合所有输出，形成连贯的结论。用中文回答。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"原始任务: {user_input}\n"
                    f"任务分析: {analysis}\n\n"
                    f"各 Agent 执行结果:\n{results_text}"
                ),
            },
        ]

        try:
            from agent_hub.llm import chat_completion_from_config

            for model_id in self.model_priority:
                result = await chat_completion_from_config(
                    model_id=model_id,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.5,
                )
                if result:
                    return result
        except Exception as e:
            logger.warning("LLM 汇总失败: %s", e)

        # 回退：直接拼接
        return "\n\n---\n\n".join(
            f"### [{r.task.agent}] {r.task.task}\n{r.output or r.error}"
            for r in task_results
        )

    # ── 便捷方法 ─────────────────────────────────────────────────

    async def execute_single(
        self,
        agent_name: str,
        task_name: str,
        params: dict[str, Any] | None = None,
        *,
        agents: dict[str, AgentManifest] | None = None,
    ) -> str:
        """直接调用单个 Agent 的单个任务（跳过路由）。"""
        if agents is None:
            agents = self._load_agents()

        if agent_name not in agents:
            return f"未知 Agent: {agent_name}"

        agent = agents[agent_name]
        if not agent.has_task(task_name):
            available = [t.name for t in agent.capabilities.tasks]
            return f"Agent '{agent_name}' 不支持任务 '{task_name}'。可用: {available}"

        self.console.print(
            f"[dim]🎯 直接调用: [{agent_name}] {task_name}[/dim]"
        )

        result = await self.bridge.execute(
            manifest=agent,
            task_name=task_name,
            params=params or {},
        )

        if result.success:
            self.console.print(f"[green]✅ [{agent_name}] {task_name} ({result.duration_ms:.0f}ms)[/green]")
        else:
            self.console.print(f"[red]❌ [{agent_name}] {task_name}: {result.error}[/red]")

        return result.output or result.error
