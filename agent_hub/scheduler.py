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
import json
import logging
import os
import sys
import time as time_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent_hub import ensure_utf8

ensure_utf8()

from rich.console import Console

from agent_hub.bridge import AgentProcessRegistry, CLIBridge
from agent_hub.cron import CronJob, CronRunRecord, CronScheduler
from agent_hub.dashboard import AgentDashboard, TaskNode
from agent_hub.manifest import AgentManifest, discover_from_registry
from agent_hub.router import CollaborationStrategy, IntentRouter, RoutePlan, RoutedTask
from agent_hub.session_store import SessionStore, new_session_record

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


def _build_wave_context(prev_results: list[TaskExecutionResult]) -> str:
    """从前一波次的结果构建上下文摘要，注入到后续波次的任务中。

    解决 Pipeline 模式下的上下文断裂问题：Wave 2 收到泛型 prompt 却不知道
    Wave 1 发现了什么（或什么都没发现），导致重复工作或搜索错误的目标。
    """
    if not prev_results:
        return ""
    lines = ["## 前序波次执行结果"]
    for r in prev_results:
        status = "✅ 成功" if r.success else f"❌ 失败: {r.error}"
        output_summary = ""
        if r.output:
            # 截取前 500 字符作为摘要，防止 prompt 膨胀
            output_summary = r.output[:500]
            if len(r.output) > 500:
                output_summary += "..."
        lines.append(
            f"- [{r.task.agent}] {r.task.task}: {status}\n"
            f"  输出摘要: {output_summary}"
        )
    return "\n".join(lines)



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
        # 并发节流信号量 — 防止 Wave 内同时生成过多子进程耗尽系统资源
        self._concurrency_sem = asyncio.Semaphore(max_concurrent)

        # 子模块（延迟初始化）
        self._router: IntentRouter | None = None
        self._bridge: CLIBridge | None = None
        self._registry: AgentProcessRegistry | None = None
        self._dashboard: AgentDashboard | None = None
        self._agents: dict[str, AgentManifest] = {}
        self._session_store: SessionStore | None = None
        self._cron: CronScheduler | None = None

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
        start_time = time_mod.monotonic()

        # Step 0: 前置检查 — LLM 是否就绪
        try:
            from agent_hub.model_config import check_system_ready
            llm_ready, llm_msg = check_system_ready()
            if not llm_ready:
                return SchedulerResult(
                    user_input=user_input,
                    route_plan=RoutePlan(tasks=[], analysis=llm_msg),
                    total_duration_ms=(time_mod.monotonic() - start_time) * 1000,
                )
        except Exception:
            pass  # 模型配置检查失败不应阻止后续执行（环境变量可能仍可用）

        # Step 1: 发现 Agent
        if agents is None:
            agents = self._load_agents()
        self._agents = agents

        # 自动启动 Watchdog（仅首次，内部有去重保护）
        self.bridge.start_watchdog()

        # 自动启动 Cron 调度器（仅首次，内部有去重保护）
        self._start_cron_if_needed()

        if not agents:
            return SchedulerResult(
                user_input=user_input,
                route_plan=RoutePlan(
                    tasks=[],
                    analysis="未发现任何 Agent。请先注册: agent register <项目路径>",
                ),
                total_duration_ms=(time_mod.monotonic() - start_time) * 1000,
            )

        self.console.print(f"[dim]发现 {len(agents)} 个 Agent: {', '.join(agents.keys())}[/dim]")

        # Step 2: 意图路由（注入最近 3 轮会话上下文）
        self.console.print("[dim]🔍 分析意图...[/dim]")
        session_context = ""
        try:
            if self._session_store is None:
                self._session_store = SessionStore()
            session_context = self._session_store.get_recent_context(3)
        except Exception:
            pass
        route_plan = await self.router.route(
            user_input, agents, session_context=session_context,
        )

        if not route_plan.tasks:
            self.console.print(f"[yellow]⚠ {route_plan.analysis}[/yellow]")
            return SchedulerResult(
                user_input=user_input,
                route_plan=route_plan,
                total_duration_ms=(time_mod.monotonic() - start_time) * 1000,
            )

        self.console.print(
            f"[dim]  路由结果: {route_plan.task_count} 个任务 "
            f"({', '.join(route_plan.agents_involved)})[/dim]"
        )
        if route_plan.strategy != "fan_out":
            self.console.print(
                f"[bold cyan]  协作策略: {route_plan.strategy_label}[/bold cyan]"
                + (f" (最多 {route_plan.max_iterations} 轮)" if route_plan.is_loop_strategy else "")
                + (f" [审批关卡: {route_plan.approval_gates}]" if route_plan.approval_gates else "")
            )

        # 低置信度警告
        if route_plan.is_low_confidence:
            self.console.print(
                f"[yellow]⚠️  路由置信度较低 ({route_plan.confidence:.0%})[/yellow]\n"
                f"[dim]  分析: {route_plan.analysis}[/dim]"
            )

        # Step 3-5: 根据协作策略分派执行
        result = await self._execute_strategy(
            user_input, route_plan, agents, show_dashboard,
        )

        result.total_duration_ms = (time_mod.monotonic() - start_time) * 1000

        # 高置信度路由 → 自动保存到路由记忆（加速后续相同请求）
        if not route_plan.is_low_confidence and result.is_success:
            try:
                self.router._save_routing_memory(user_input, route_plan)
            except Exception:
                pass

        # 保存会话记录（用于跨轮次上下文感知）
        try:
            if self._session_store is None:
                self._session_store = SessionStore()
            record = new_session_record(
                user_input=user_input,
                route_plan=route_plan.to_dict(),
                task_results=[
                    {
                        "agent": r.task.agent,
                        "task": r.task.task,
                        "success": r.success,
                        "output": r.output[:200],
                        "error": r.error[:200],
                        "duration_ms": r.duration_ms,
                    }
                    for r in result.task_results
                ],
                aggregate=result.aggregate,
                duration_ms=result.total_duration_ms,
            )
            self._session_store.save(record)
        except Exception:
            logger.debug("保存会话记录失败", exc_info=True)

        return result

    async def _execute_strategy(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
        show_dashboard: bool,
    ) -> SchedulerResult:
        """根据协作策略分派到对应的执行方法。

        这是策略 DSL 的执行入口。每种策略有独立的执行逻辑：
        - fan_out / pipeline: 现有 DAG 执行（pipeline 由 depends_on 自然形成串行）
        - debate / reflection: 循环执行（Phase 5b/5c 实现）
        - hitl: DAG + 审批关卡（Phase 5b 实现）
        - vote: 多 Agent 并发投票（Phase 5c 实现）
        - plan_execute: 先规划后执行（Phase 5c 实现）
        """
        strategy = route_plan.strategy

        if strategy == CollaborationStrategy.FAN_OUT:
            return await self._execute_fan_out(user_input, route_plan, agents, show_dashboard)

        elif strategy == CollaborationStrategy.PIPELINE:
            # Pipeline = 严格串行 DAG（depends_on 已定义顺序）
            return await self._execute_fan_out(user_input, route_plan, agents, show_dashboard)

        elif strategy == CollaborationStrategy.DEBATE:
            return await self._execute_debate(user_input, route_plan, agents, show_dashboard)

        elif strategy == CollaborationStrategy.REFLECTION:
            return await self._execute_reflection(user_input, route_plan, agents, show_dashboard)

        elif strategy == CollaborationStrategy.VOTE:
            return await self._execute_vote(user_input, route_plan, agents, show_dashboard)

        elif strategy == CollaborationStrategy.PLAN_EXECUTE:
            return await self._execute_plan_execute(user_input, route_plan, agents, show_dashboard)

        elif strategy == CollaborationStrategy.HUMAN_IN_LOOP:
            return await self._execute_hitl(user_input, route_plan, agents, show_dashboard)

        else:
            # 未知策略 → 回退到 fan_out
            logger.warning("未知策略 '%s'，回退到 fan_out", strategy)
            return await self._execute_fan_out(user_input, route_plan, agents, show_dashboard)

    # ── 策略实现：fan_out / pipeline（当前 DAG 逻辑）───────────────

    async def _execute_fan_out(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
        show_dashboard: bool,
    ) -> SchedulerResult:
        """Fan-out / Pipeline 策略 — 使用现有 DAG 波次并行执行。"""
        if show_dashboard:
            return await self._execute_with_dashboard(user_input, route_plan, agents)
        else:
            return await self._execute_headless(user_input, route_plan, agents)

    # ── 策略实现：debate（辩论-修复循环）─────────────────────────

    async def _execute_debate(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
        show_dashboard: bool,
    ) -> SchedulerResult:
        """Debate 策略：A 提案 → B 批评 → A 修订 → 循环直到通过阈值。

        典型场景：SmartBench 诊断 → OmniAgent 修复 → SmartBench 再诊断 → ...
        退出条件：达到 max_iterations 或 exit_condition 满足。
        """
        from agent_hub.router import RoutedTask

        all_results: list[TaskExecutionResult] = []
        start_time = time_mod.monotonic()

        self.console.print(
            f"[bold magenta]⚔ 辩论模式: 最多 {route_plan.max_iterations} 轮[/bold magenta]"
        )

        for iteration in range(1, route_plan.max_iterations + 1):
            self.console.print(
                f"\n[bold]── Round {iteration}/{route_plan.max_iterations} ──[/bold]"
            )

            # 每一轮重新执行所有 tasks（保持 depends_on 顺序）
            # 将上一轮的结果作为 context 注入
            enriched_plan = route_plan
            if all_results:
                last_outputs = "\n".join(
                    f"[{r.task.agent}] {r.task.task}: {r.output[:300]}"
                    for r in all_results[-len(route_plan.tasks):]
                )
                # 为每个 task 注入上一轮的输出作为 context
                for task in enriched_plan.tasks:
                    task.params["context"] = (
                        f"上一轮输出:\n{last_outputs}\n\n"
                        f"请基于以上反馈改进结果。"
                    )
                    task.params["iteration"] = iteration

            # 执行当前轮
            if show_dashboard:
                round_result = await self._execute_with_dashboard(
                    f"{user_input} (round {iteration})", enriched_plan, agents,
                )
            else:
                round_result = await self._execute_headless(
                    f"{user_input} (round {iteration})", enriched_plan, agents,
                )

            all_results.extend(round_result.task_results)

            # 检查退出条件
            if route_plan.exit_condition and self._check_exit_condition(
                route_plan.exit_condition, round_result.task_results,
            ):
                self.console.print(
                    f"[green]✅ 退出条件满足: {route_plan.exit_condition}[/green]"
                )
                break

            # 检查是否全部成功
            if round_result.is_success:
                self.console.print("[green]✅ 本轮全部成功，辩论结束[/green]")
                break

        total_duration = (time_mod.monotonic() - start_time) * 1000
        aggregate = await self._aggregate(user_input, all_results, route_plan.analysis)

        return SchedulerResult(
            user_input=user_input,
            route_plan=route_plan,
            task_results=all_results,
            aggregate=aggregate,
            total_duration_ms=total_duration,
        )

    # ── 策略实现：reflection（自反思循环）────────────────────────

    async def _execute_reflection(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
        show_dashboard: bool,
    ) -> SchedulerResult:
        """Reflection 策略：执行 → 自审 → 改进，同一 Agent 内循环。

        典型场景：OmniAgent 写代码 → 自审查 → 改进 → 再审查 → ...
        """
        all_results: list[TaskExecutionResult] = []
        start_time = time_mod.monotonic()

        self.console.print(
            f"[bold magenta]🪞 自反思模式: 最多 {route_plan.max_iterations} 轮[/bold magenta]"
        )

        previous_output = ""
        for iteration in range(1, route_plan.max_iterations + 1):
            self.console.print(
                f"\n[bold]── Reflection Round {iteration}/{route_plan.max_iterations} ──[/bold]"
            )

            # 注入上一轮的输出作为反思上下文
            if previous_output:
                for task in route_plan.tasks:
                    task.params["context"] = (
                        f"这是第 {iteration} 轮改进。\n"
                        f"上一轮输出:\n{previous_output[:500]}\n\n"
                        f"请审查以上输出，找出可改进之处，然后给出改进版本。"
                    )
                    task.params["iteration"] = iteration

            if show_dashboard:
                round_result = await self._execute_with_dashboard(
                    f"{user_input} (reflection round {iteration})", route_plan, agents,
                )
            else:
                round_result = await self._execute_headless(
                    f"{user_input} (reflection round {iteration})", route_plan, agents,
                )

            all_results.extend(round_result.task_results)

            # 保存输出用于下一轮反思
            if round_result.task_results:
                previous_output = round_result.task_results[-1].output

            # 检查退出条件
            if route_plan.exit_condition and self._check_exit_condition(
                route_plan.exit_condition, round_result.task_results,
            ):
                self.console.print(
                    f"[green]✅ 退出条件满足: {route_plan.exit_condition}[/green]"
                )
                break

        total_duration = (time_mod.monotonic() - start_time) * 1000
        aggregate = await self._aggregate(user_input, all_results, route_plan.analysis)

        return SchedulerResult(
            user_input=user_input,
            route_plan=route_plan,
            task_results=all_results,
            aggregate=aggregate,
            total_duration_ms=total_duration,
        )

    # ── 策略实现：vote（多视角投票）─────────────────────────────

    async def _execute_vote(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
        show_dashboard: bool,
    ) -> SchedulerResult:
        """Vote 策略：同一任务 → 多模型执行 → LLM 比较差异 → 选最佳。

        将每个 task 用 model_priority 中的前 N 个模型各执行一次，
        然后用 LLM 比较各模型的输出，整合为最优结果。
        """
        self.console.print("[bold magenta]🗳 投票模式: 多模型并发执行[/bold magenta]")

        all_results: list[TaskExecutionResult] = []
        start_time = time_mod.monotonic()

        # 每个 task 用多个模型各执行一次
        models = self.model_priority[:3]  # 最多 3 个模型
        if len(models) < 2:
            self.console.print("[dim]  仅 1 个模型可用，回退到 fan_out[/dim]")
            return await self._execute_fan_out(user_input, route_plan, agents, show_dashboard)

        self.console.print(f"[dim]  使用模型: {', '.join(models)}[/dim]")

        for task in route_plan.tasks:
            self.console.print(f"\n[bold]  📍 任务: [{task.agent}] {task.task}[/bold]")
            task_outputs: list[dict] = []

            for model_id in models:
                self.console.print(f"[dim]    🤖 {model_id}...[/dim]")
                # 临时切换模型优先级为单模型
                orig_priority = self.model_priority
                self.model_priority = [model_id]

                try:
                    result = await self._execute_single_task(task, agents, dash=None)
                finally:
                    self.model_priority = orig_priority

                task_outputs.append({
                    "model": model_id,
                    "success": result.success,
                    "output": result.output[:600],
                    "error": result.error[:200],
                    "duration_ms": result.duration_ms,
                })
                all_results.append(result)

            # LLM 比较各模型输出
            if len(task_outputs) >= 2:
                comparison_prompt = (
                    f"原始任务: {task.description or task.task}\n\n"
                    + "\n\n---\n\n".join(
                        f"模型 [{t['model']}]:\n{t['output']}"
                        for t in task_outputs if t['success']
                    )
                    + "\n\n请比较以上各模型的输出，选出最佳回答或整合为最优答案。用中文回复。"
                )
                try:
                    from agent_hub.llm import chat_completion_from_config
                    best = await chat_completion_from_config(
                        model_id=models[0],
                        messages=[{"role": "user", "content": comparison_prompt}],
                        max_tokens=1024, temperature=0.3,
                    )
                    if best:
                        self.console.print(f"    [green]✅ 最佳答案已生成[/green]")
                        # 将最佳答案注入结果列表，供 aggregate 使用
                        best_task = RoutedTask(
                            id=999, agent="llm-comparator", task="compare_models",
                            description="多模型投票比较最佳答案",
                        )
                        all_results.append(TaskExecutionResult(
                            task=best_task, success=True, output=best,
                        ))
                except Exception:
                    best = None

        total_duration = (time_mod.monotonic() - start_time) * 1000
        aggregate = await self._aggregate(user_input, all_results, route_plan.analysis)

        return SchedulerResult(
            user_input=user_input, route_plan=route_plan,
            task_results=all_results, aggregate=aggregate,
            total_duration_ms=total_duration,
        )

    # ── 策略实现：plan_execute（先规划后执行）────────────────────

    async def _execute_plan_execute(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
        show_dashboard: bool,
    ) -> SchedulerResult:
        """Plan-Execute 策略：先规划 → 按步执行 → 失败则自动重规划。

        将第一个 task 作为"规划阶段"，后续 task 作为"执行阶段"。
        执行阶段中的任何失败会触发重规划（将失败信息反馈给规划 Agent）。
        """
        self.console.print("[bold magenta]📋 规划-执行模式[/bold magenta]")

        all_results: list[TaskExecutionResult] = []
        start_time = time_mod.monotonic()
        max_replans = route_plan.max_iterations

        for replan_round in range(max_replans + 1):
            if replan_round == 0:
                self.console.print("[bold]  📐 规划阶段...[/bold]")
            else:
                self.console.print(f"[bold]  🔄 重规划 (第 {replan_round} 次)...[/bold]")

            # 执行所有 tasks（DAG 顺序）
            if show_dashboard:
                round_result = await self._execute_with_dashboard(
                    user_input, route_plan, agents,
                )
            else:
                round_result = await self._execute_headless(
                    user_input, route_plan, agents,
                )

            all_results.extend(round_result.task_results)

            # 检查是否全部成功
            if round_result.is_success:
                self.console.print("[green]✅ 所有步骤成功[/green]")
                break

            # 有失败 → 尝试重规划
            if replan_round < max_replans:
                failures = [
                    f"[{r.task.agent}] {r.task.task}: {r.error}"
                    for r in round_result.task_results if not r.success
                ]
                self.console.print(
                    f"[yellow]⚠️  {len(failures)} 个步骤失败，触发重规划[/yellow]"
                )
                # 将失败信息注入第一个 task 的 params 作为 context
                if route_plan.tasks:
                    route_plan.tasks[0].params["context"] = (
                        f"上一轮执行中 {len(failures)} 个步骤失败:\n"
                        + "\n".join(failures)
                        + "\n\n请调整计划以解决以上问题。"
                    )
            else:
                self.console.print("[red]✗ 重规划次数耗尽[/red]")

        total_duration = (time_mod.monotonic() - start_time) * 1000
        aggregate = await self._aggregate(user_input, all_results, route_plan.analysis)

        return SchedulerResult(
            user_input=user_input, route_plan=route_plan,
            task_results=all_results, aggregate=aggregate,
            total_duration_ms=total_duration,
        )

    # ── 策略实现：hitl（人机协同）───────────────────────────────

    async def _execute_hitl(
        self,
        user_input: str,
        route_plan: RoutePlan,
        agents: dict[str, AgentManifest],
        show_dashboard: bool,
    ) -> SchedulerResult:
        """Human-in-the-loop 策略：逐任务执行，审批关卡处暂停等人类确认。

        审批关卡 = route_plan.approval_gates 中列出的 task ID。
        被标记的 task 完成后暂停，展示输出，等待用户选择：
          [Y] 批准继续  [n] 拒绝中止  [r] 重试（输入反馈）
        """
        from rich.prompt import Prompt as RichPrompt

        self.console.print(
            f"[bold magenta]👤 人机协同模式[/bold magenta]"
            + (f" [审批关卡: 步骤 {route_plan.approval_gates}]" if route_plan.approval_gates else "")
        )
        self.console.print("[dim]  关键操作将在执行后等待您的确认[/dim]")

        all_results: list[TaskExecutionResult] = []
        start_time = time_mod.monotonic()
        approval_set = set(route_plan.approval_gates)

        # HitL 使用串行执行（逐任务），确保审批流清晰
        for task in route_plan.tasks:
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                self.console.print(
                    f"\n[bold]  ▶ [{task.agent}] {task.task}[/bold]"
                    + (f" [审批关卡]" if task.id in approval_set else "")
                )

                # 执行单个任务
                result = await self._execute_single_task(task, agents, dash=None)
                all_results.append(result)

                icon = "✅" if result.success else "❌"
                self.console.print(
                    f"  {icon} 完成 ({result.duration_ms:.0f}ms)"
                )
                if result.output:
                    self.console.print(f"  [dim]输出: {result.output[:300]}[/dim]")
                if result.error:
                    self.console.print(f"  [red]错误: {result.error[:200]}[/red]")

                # 非审批关卡 → 直接继续
                if task.id not in approval_set:
                    break

                # 审批关卡 → 等待人类决策
                self.console.print()
                choice = RichPrompt.ask(
                    f"  [bold yellow]⚠ 审批关卡[/bold yellow] — "
                    f"[{task.agent}] {task.task}",
                    choices=["Y", "n", "r"],
                    default="Y",
                )

                if choice == "Y":
                    self.console.print("  [green]✅ 已批准，继续[/green]")
                    break  # 批准，继续下一个 task
                elif choice == "n":
                    self.console.print("  [red]✗ 已拒绝，中止执行[/red]")
                    # 返回已收集的结果
                    total_duration = (time_mod.monotonic() - start_time) * 1000
                    aggregate = f"执行被用户中止于步骤 {task.id} ([{task.agent}] {task.task})"
                    return SchedulerResult(
                        user_input=user_input, route_plan=route_plan,
                        task_results=all_results, aggregate=aggregate,
                        total_duration_ms=total_duration,
                    )
                elif choice == "r":
                    if attempt < max_retries:
                        feedback = RichPrompt.ask("    反馈（将传给 Agent 重试）", default="请改进输出质量")
                        task.params["context"] = f"用户反馈: {feedback}\n请根据反馈重新执行。"
                        task.params["retry_attempt"] = attempt
                        self.console.print(f"  [yellow]🔄 重试 (第 {attempt}/{max_retries} 次)[/yellow]")
                    else:
                        self.console.print(f"  [red]✗ 已达最大重试次数 ({max_retries})[/red]")
                        break

        total_duration = (time_mod.monotonic() - start_time) * 1000
        aggregate = await self._aggregate(user_input, all_results, route_plan.analysis)

        return SchedulerResult(
            user_input=user_input, route_plan=route_plan,
            task_results=all_results, aggregate=aggregate,
            total_duration_ms=total_duration,
        )

    # ── 退出条件求值 ──────────────────────────────────────────

    @staticmethod
    def _check_exit_condition(
        condition: str,
        task_results: list[TaskExecutionResult],
    ) -> bool:
        """简易退出条件求值器。

        支持的格式（安全子集，不使用 eval）：
        - "success" → 所有任务成功
        - "score >= 90" / "pass_rate > 0.9" → 从输出中提取数值比较

        Returns:
            True 如果条件满足
        """
        if not condition or not condition.strip():
            return False

        condition = condition.strip().lower()

        # "success" → 全部成功
        if condition == "success":
            return all(r.success for r in task_results)

        # 尝试解析 "key op value" 格式
        import re
        match = re.match(r"(\w+)\s*(>=|<=|>|<|==|!=)\s*([\d.]+)", condition)
        if not match:
            return False

        key, op, target_str = match.group(1), match.group(2), match.group(3)
        try:
            target = float(target_str)
        except ValueError:
            return False

        # 在每个 task_result 的 output 中搜索 key: value 或 key=value 或 key value
        for r in task_results:
            if not r.success:
                continue
            output = r.output.lower()
            # 尝试多种格式: "score: 92", "score=92", "score 92"
            for pattern in [
                rf"{key}\s*[:=]\s*([\d.]+)",
                rf"{key}\s+([\d.]+)",
            ]:
                m = re.search(pattern, output)
                if m:
                    try:
                        value = float(m.group(1))
                    except ValueError:
                        continue
                    if op == ">=":
                        return value >= target
                    elif op == "<=":
                        return value <= target
                    elif op == ">":
                        return value > target
                    elif op == "<":
                        return value < target
                    elif op == "==":
                        return value == target
                    elif op == "!=":
                        return value != target

        return False

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

        prev_wave_results: list[TaskExecutionResult] = []

        with dash.run():
            for wave_idx, wave in enumerate(waves):
                wave_label = f"Wave {wave_idx + 1}/{len(waves)}"

                # 注入前序波次上下文到当前波次的任务中
                if wave_idx > 0 and prev_wave_results:
                    ctx = _build_wave_context(prev_wave_results)
                    for task in wave:
                        task.params["_pipeline_context"] = ctx
                        self.console.print(
                            f"[dim]  ↳ 注入 Wave {wave_idx} 上下文 "
                            f"({len(ctx)} 字符)[/dim]"
                        )

                # 标记波内任务为 running
                for task in wave:
                    dash.task_graph.update_status(task.id, "running")

                dash.refresh()

                # 并行执行波内任务（统一分派：内部/外部）
                tasks_coros = [
                    self._execute_single_task_throttled(task, agents, dash)
                    for task in wave
                ]
                results = await asyncio.gather(*tasks_coros, return_exceptions=True)

                # 本轮波次结果（用于下一波次上下文注入）
                wave_results: list[TaskExecutionResult] = []

                # 处理波次结果
                for task, result in zip(wave, results):
                    if isinstance(result, Exception):
                        exec_result = TaskExecutionResult(
                            task=task,
                            success=False,
                            error=str(result),
                        )
                    else:
                        exec_result = result

                    task_results.append(exec_result)

                    # 保存波次结果用于下一波次上下文注入
                    wave_results.append(exec_result)

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

            # 保存本轮波次结果，供下一波次使用
            prev_wave_results = wave_results

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
        """流式输出模式 — Agent 输出实时滚动（类似 pip install）。"""
        task_results: list[TaskExecutionResult] = []
        prev_wave_results: list[TaskExecutionResult] = []

        waves = _compute_waves(route_plan.tasks)

        # 流式回调：Agent 每行输出立即打印
        def _make_stream_callback(agent_name: str):
            color = {
                "omniagent": "cyan", "resume-sync": "magenta",
                "smartbench": "yellow", "agent-hub": "green",
            }.get(agent_name, "white")
            return lambda line: self.console.print(
                f"[dim][{agent_name}][/dim] {line}"
            )

        for wave_idx, wave in enumerate(waves):
            if len(waves) > 1:
                self.console.print(
                    f"[dim]── Wave {wave_idx + 1}/{len(waves)} ──[/dim]"
                )

            # 注入前序波次上下文到当前波次的任务中
            if wave_idx > 0 and prev_wave_results:
                ctx = _build_wave_context(prev_wave_results)
                for task in wave:
                    task.params["_pipeline_context"] = ctx

            # 构建任务协程：外部 Agent 带流式回调，内部 Agent 走原路径
            # 所有任务受 max_concurrent Semaphore 节流
            async def _throttled_internal(task):
                async with self._concurrency_sem:
                    return await self._execute_single_task(task, agents, dash=None)

            async def _throttled_streaming(task, cb):
                async with self._concurrency_sem:
                    return await self._execute_single_task_streaming(task, agents, cb)

            tasks_coros = []
            for task in wave:
                manifest = agents.get(task.agent)
                if manifest and manifest.protocol != "internal":
                    callback = _make_stream_callback(task.agent)
                    tasks_coros.append(_throttled_streaming(task, callback))
                else:
                    tasks_coros.append(_throttled_internal(task))

            results = await asyncio.gather(*tasks_coros, return_exceptions=True)

            wave_results: list[TaskExecutionResult] = []
            for task, result in zip(wave, results):
                if isinstance(result, Exception):
                    exec_result = TaskExecutionResult(task=task, success=False, error=str(result))
                    self.console.print(f"  [red]✗ [{task.agent}] {task.task}[/red]: {result}")
                else:
                    exec_result = result
                    icon = "✅" if result.success else "❌"
                    self.console.print(
                        f"  {icon} [{task.agent}] {task.task} "
                        f"([dim]{exec_result.duration_ms:.0f}ms[/dim])"
                    )

                task_results.append(exec_result)
                wave_results.append(exec_result)

            prev_wave_results = wave_results

        aggregate = await self._aggregate(user_input, task_results, route_plan.analysis)

        return SchedulerResult(
            user_input=user_input,
            route_plan=route_plan,
            task_results=task_results,
            aggregate=aggregate,
        )

    async def _execute_single_task_streaming(
        self,
        task: RoutedTask,
        agents: dict[str, AgentManifest],
        on_stdout: Any = None,
    ) -> TaskExecutionResult:
        """执行单个任务并流式输出 stdout（用于 headless 模式）。"""
        manifest = agents.get(task.agent)
        if not manifest:
            return TaskExecutionResult(task=task, success=False, error=f"Agent '{task.agent}' 未注册")

        if manifest.protocol == "internal":
            return await self._run_internal_task(task, manifest)

        result = await self.bridge.execute(
            manifest=manifest,
            task_name=task.task,
            params=task.params,
            on_stdout=on_stdout,
        )
        return TaskExecutionResult(
            task=task,
            success=result.success,
            output=result.output,
            error=result.error,
            duration_ms=result.duration_ms,
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

        # 单任务快速路径：输出足够丰富时直接返回，避免不必要的 LLM 调用
        if len(task_results) == 1 and task_results[0].success:
            output = task_results[0].output
            # 输出较短或为结构化列表（如 discover_capabilities）→ 走 LLM 增强
            if len(output) > 500:
                return output
            # 短输出（如纯列表）→ 继续走 LLM 汇总以提供更有价值的回答

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
                    "给出最终完整、有深度的回答。\n\n"
                    "要求：\n"
                    "1. 整合所有 Agent 输出，形成连贯结论（而非简单罗列）\n"
                    "2. 如果输出是结构化数据（如 Agent 列表），展开每个条目的能力说明\n"
                    "3. 如果某个 Agent 失败，说明可能原因和替代方案\n"
                    "4. 用中文回答，结构清晰（可用标题分段）\n"
                    "5. 不要只说'完成了X个任务'——要说明完成了什么、结果是什么"
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

    # ── 内部任务分派 ───────────────────────────────────────────

    async def _execute_single_task(
        self,
        task: RoutedTask,
        agents: dict[str, AgentManifest],
        dash: AgentDashboard | None = None,
    ) -> TaskExecutionResult:
        """统一的任务执行入口 — 根据 protocol 分派到内部或外部执行。

        内部协议 (protocol=internal)：进程内调度，不生成子进程（避免递归）
        外部协议 (protocol=cli/mcp/http)：通过 CLIBridge 子进程执行
        """
        manifest = agents.get(task.agent)
        if not manifest:
            return TaskExecutionResult(
                task=task,
                success=False,
                error=f"Agent '{task.agent}' 未注册",
            )

        if manifest.protocol == "internal":
            return await self._run_internal_task(task, manifest)

        # 外部 Agent — CLI Bridge 子进程执行
        result = await self.bridge.execute(
            manifest=manifest,
            task_name=task.task,
            params=task.params,
            on_stdout=(
                lambda line, ag=task.agent: dash.get_panel(ag).append(line)
                if dash else None
            ),
        )
        return TaskExecutionResult(
            task=task,
            success=result.success,
            output=result.output,
            error=result.error,
            duration_ms=result.duration_ms,
        )

    async def _execute_single_task_throttled(
        self,
        task: RoutedTask,
        agents: dict[str, AgentManifest],
        dash: AgentDashboard | None = None,
    ) -> TaskExecutionResult:
        """与 _execute_single_task 相同，但受 max_concurrent Semaphore 节流。

        防止 Wave 内同时生成过多子进程耗尽系统资源（文件描述符、内存）。
        """
        async with self._concurrency_sem:
            return await self._execute_single_task(task, agents, dash)

    async def _run_internal_task(
        self,
        task: RoutedTask,
        manifest: AgentManifest,
    ) -> TaskExecutionResult:
        """在进程内执行 internal 协议 Agent 的任务。

        不生成子进程 — 直接调用 AgentScheduler 自身的方法。
        这是防止 agent-hub 自调用时产生无限递归的关键机制。
        """
        start_time = time_mod.monotonic()

        try:
            if task.task == "route_task":
                # 调用 LLM 路由器分解意图为跨 Agent DAG
                plan = await self.router.route(
                    task.params.get("goal", ""),
                    self._agents,
                    extra_context=task.params.get("context", ""),
                )
                output = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)

            elif task.task == "manage_agents":
                action = task.params.get("action", "status")
                agent_names = task.params.get("agent_names", [])
                output = await self._handle_manage_agents(action, agent_names)

            elif task.task == "discover_capabilities":
                ags = self._agents if self._agents else self._load_agents()
                filt = (task.params.get("filter") or "").lower()
                lines = []
                for name, m in sorted(ags.items()):
                    if filt and filt not in name.lower() and filt not in m.display_name.lower():
                        continue
                    tasks_str = ", ".join(t.name for t in m.capabilities.tasks)
                    lines.append(
                        f"{name} ({m.display_name}): "
                        f"{len(m.capabilities.tasks)} tasks [{tasks_str}]"
                    )
                output = "\n".join(lines) if lines else "No agents match the filter."

            elif task.task == "aggregate_results":
                # 将 params 中的 dict 结果转换为 TaskExecutionResult 列表
                raw_results = task.params.get("results", [])
                parsed_results: list[TaskExecutionResult] = []
                for r in raw_results:
                    if isinstance(r, TaskExecutionResult):
                        parsed_results.append(r)
                    elif isinstance(r, dict):
                        # 从 dict 重建 TaskExecutionResult
                        rt = r.get("task", {})
                        routed = RoutedTask(
                            id=rt.get("id", 0),
                            agent=rt.get("agent", "unknown"),
                            task=rt.get("task", ""),
                            description=rt.get("description", ""),
                            params=rt.get("params", {}),
                            depends_on=rt.get("depends_on", []),
                        )
                        parsed_results.append(TaskExecutionResult(
                            task=routed,
                            success=r.get("success", False),
                            output=str(r.get("output", "")),
                            error=str(r.get("error", "")),
                            duration_ms=float(r.get("duration_ms", 0)),
                        ))
                output = await self._aggregate(
                    task.params.get("user_input", ""),
                    parsed_results,
                    task.params.get("analysis", ""),
                )

            else:
                raise ValueError(
                    f"Unknown internal task: {task.task}. "
                    f"Available: route_task, manage_agents, discover_capabilities, aggregate_results"
                )

        except Exception as e:
            duration_ms = (time_mod.monotonic() - start_time) * 1000
            logger.error("内部任务 %s 失败: %s", task.task, e, exc_info=True)
            return TaskExecutionResult(
                task=task, success=False, error=str(e), duration_ms=duration_ms,
            )

        duration_ms = (time_mod.monotonic() - start_time) * 1000
        return TaskExecutionResult(
            task=task, success=True, output=str(output), duration_ms=duration_ms,
        )

    async def _handle_manage_agents(
        self, action: str, agent_names: list[str],
    ) -> str:
        """处理 manage_agents 内部任务。

        Args:
            action: start | stop | restart | status
            agent_names: Agent 名列表（空 = 全部）
        """
        agents = self._agents if self._agents else self._load_agents()

        if agent_names:
            targets = {n: m for n, m in agents.items() if n in agent_names}
        else:
            targets = agents

        if not targets:
            return "No matching agents found."

        if action == "status":
            lines = []
            for name in sorted(targets):
                info = self.bridge.registry.get(name)
                status = info.status if info else "unknown"
                pid = str(info.pid) if info and info.pid else "N/A"
                lines.append(f"{name}: {status} (pid={pid})")
            return "\n".join(lines) if lines else "No agents found."

        elif action == "start":
            results = await self.bridge.start_all(list(targets.values()))
            ok = sum(1 for r in results.values() if r.is_running)
            return f"Started {ok}/{len(results)} agents."

        elif action == "stop":
            for name in list(targets):
                await self.bridge.stop_agent(name)
            return f"Stopped {len(targets)} agents."

        elif action == "restart":
            for name in list(targets):
                await self.bridge.stop_agent(name)
            results = await self.bridge.start_all(list(targets.values()))
            ok = sum(1 for r in results.values() if r.is_running)
            return f"Restarted {ok}/{len(results)} agents."

        else:
            return f"Unknown action: {action}. Available: start, stop, restart, status"

    # ── Cron 集成 ──────────────────────────────────────────────

    def _start_cron_if_needed(self) -> None:
        """如 .agent_hub/cron_jobs.json 存在且有任务，则启动 cron 循环。"""
        if self._cron is not None:
            return  # 已启动

        self._cron = CronScheduler()
        if not self._cron.list_jobs():
            return  # 无定时任务，不启动循环

        asyncio.create_task(
            self._cron.start_loop(self._execute_cron_job),
            name="cron-scheduler-bg",
        )
        logger.info("Cron 调度器已自动启动 (%d 个任务)", len(self._cron.list_jobs()))

    async def _execute_cron_job(self, job: CronJob) -> CronRunRecord:
        """执行单个定时任务（cron 回调）。"""
        import time as _time
        start = _time.monotonic()

        # 构造 RoutedTask
        task = RoutedTask(
            id=0,
            agent=job.agent,
            task=job.task,
            description=f"定时任务: {job.name}",
            params=job.params,
            depends_on=[],
        )

        try:
            # 加载 agents 并执行
            agents = self._load_agents()
            if job.agent not in agents:
                return CronRunRecord(
                    job_name=job.name,
                    agent=job.agent,
                    task=job.task,
                    success=False,
                    error=f"Agent '{job.agent}' 未注册",
                    duration_ms=(_time.monotonic() - start) * 1000,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

            result = await self._execute_single_task(task, agents, dash=None)

            return CronRunRecord(
                job_name=job.name,
                agent=job.agent,
                task=job.task,
                success=result.success,
                output=result.output[:500],
                error=result.error[:500],
                duration_ms=result.duration_ms,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            return CronRunRecord(
                job_name=job.name,
                agent=job.agent,
                task=job.task,
                success=False,
                error=str(e),
                duration_ms=(_time.monotonic() - start) * 1000,
                timestamp=datetime.now(timezone.utc).isoformat(),
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

        # 内部协议 Agent 走进程内调度
        if agent.protocol == "internal":
            task = RoutedTask(
                id=0, agent=agent_name, task=task_name,
                description=f"直接调用 {task_name}",
                params=params or {},
            )
            exec_result = await self._run_internal_task(task, agent)
            return exec_result.output if exec_result.success else exec_result.error

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
