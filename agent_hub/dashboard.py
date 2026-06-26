"""Visual Dashboard — Rich 多面板 TUI 可视化仪表盘。

在终端中实时展示多 Agent 调度过程：
- TaskGraph: 任务 DAG 节点+依赖边，实时状态更新
- AgentPanels: 每个活跃 Agent 的实时输出面板
- DataFlowLog: Hub ↔ Agent 数据流转时间线
- ResultPanel: 最终汇总结果

基于 Rich Live + Layout，与 omniagent 的 REPL 风格一致。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
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

from rich.align import Align
from rich.console import Console, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logger = logging.getLogger(__name__)

# ── Agent 配色 ──────────────────────────────────────────────────────

AGENT_COLORS: dict[str, str] = {
    "omniagent": "cyan",
    "resume-sync": "magenta",
    "smartbench": "yellow",
    "agent-hub": "green",
}

AGENT_ICONS: dict[str, str] = {
    "omniagent": "🔍",
    "resume-sync": "📄",
    "smartbench": "🧪",
    "agent-hub": "🔄",
}

STATUS_ICONS: dict[str, str] = {
    "pending": "⬡",
    "running": "🔄",
    "done": "✅",
    "failed": "❌",
    "skipped": "⏭️",
}

STATUS_STYLES: dict[str, str] = {
    "pending": "dim",
    "running": "bold cyan",
    "done": "bold green",
    "failed": "bold red",
    "skipped": "dim yellow",
}


def _agent_color(name: str) -> str:
    """获取 Agent 的配色（回退到默认色）。"""
    for key, color in AGENT_COLORS.items():
        if key in name.lower():
            return color
    return "white"


def _agent_icon(name: str) -> str:
    for key, icon in AGENT_ICONS.items():
        if key in name.lower():
            return icon
    return "🤖"


# ── 数据流日志条目 ─────────────────────────────────────────────────


@dataclass
class DataFlowEntry:
    """一条数据流转日志条目。"""

    timestamp: float
    direction: str  # "→" (Hub→Agent) 或 "←" (Agent→Hub)
    agent: str
    message: str

    def format(self) -> str:
        icon = _agent_icon(self.agent)
        t = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        color = _agent_color(self.agent)
        return f"[dim]{t}[/dim] Hub {self.direction} [bold {color}]{icon} {self.agent}[/] : {self.message}"


class DataFlowLog:
    """数据流转时间线（线程安全）。"""

    MAX_ENTRIES = 50

    def __init__(self) -> None:
        self._entries: list[DataFlowEntry] = []
        self._lock = threading.Lock()

    def log_request(self, agent: str, task: str, params: dict | None = None) -> None:
        """记录 Hub → Agent 请求。"""
        params_str = ""
        if params:
            goal = params.get("goal", params.get("task_description", ""))
            if goal:
                params_str = f" ({goal[:60]})"
        entry = DataFlowEntry(
            timestamp=time.time(),
            direction="→",
            agent=agent,
            message=f"{task}{params_str}",
        )
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.MAX_ENTRIES:
                self._entries = self._entries[-self.MAX_ENTRIES:]

    def log_response(self, agent: str, result_summary: str) -> None:
        """记录 Agent → Hub 响应。"""
        entry = DataFlowEntry(
            timestamp=time.time(),
            direction="←",
            agent=agent,
            message=result_summary[:120],
        )
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.MAX_ENTRIES:
                self._entries = self._entries[-self.MAX_ENTRIES:]

    def render(self) -> RenderableType:
        """渲染为 Rich renderable。"""
        with self._lock:
            entries = list(self._entries[-20:])  # 最近 20 条

        if not entries:
            return Text("  (暂无数据流转记录)", style="dim")

        lines = [entry.format() for entry in entries]
        return Text("\n".join(lines))


# ── Agent 输出面板 ─────────────────────────────────────────────────


class AgentOutputPanel:
    """单个 Agent 的实时输出面板。

    维护一个行缓冲区，新行追加到末尾，自动滚动到最新内容。
    """

    MAX_LINES = 30

    def __init__(self, agent_name: str, title: str = "") -> None:
        self.agent_name = agent_name
        self.title = title or f"{_agent_icon(agent_name)} {agent_name}"
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self.status = "idle"

    def append(self, line: str) -> None:
        """追加一行输出。"""
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self.MAX_LINES:
                self._lines = self._lines[-self.MAX_LINES:]

    def set_status(self, status: str) -> None:
        self.status = status

    def render(self, width: int = 60) -> Panel:
        """渲染为 Rich Panel。"""
        with self._lock:
            lines = list(self._lines[-self.MAX_LINES:])

        color = _agent_color(self.agent_name)
        status_icon = {
            "idle": "⚫",
            "running": "🟢",
            "done": "✅",
            "failed": "❌",
        }.get(self.status, "⚫")

        title = f"{status_icon} {self.title}"

        if not lines:
            content: RenderableType = Text("  (等待输出...)", style="dim")
        else:
            content = Text("\n".join(f"  {line}" for line in lines))

        return Panel(
            content,
            title=title,
            border_style=color,
            width=width,
        )


# ── 任务图 ─────────────────────────────────────────────────────────


@dataclass
class TaskNode:
    """DAG 中的一个任务节点。"""

    id: int
    agent: str
    task: str
    description: str = ""
    status: str = "pending"  # pending | running | done | failed
    depends_on: list[int] = field(default_factory=list)


class TaskGraph:
    """任务 DAG 可视化面板。

    显示节点列表 + 依赖关系，实时更新状态。
    """

    def __init__(self) -> None:
        self.nodes: dict[int, TaskNode] = {}
        self._lock = threading.Lock()

    def set_nodes(self, nodes: list[TaskNode]) -> None:
        with self._lock:
            self.nodes = {n.id: n for n in nodes}

    def update_status(self, node_id: int, status: str) -> None:
        with self._lock:
            if node_id in self.nodes:
                self.nodes[node_id].status = status

    @property
    def all_done(self) -> bool:
        with self._lock:
            if not self.nodes:
                return False
            return all(n.status in ("done", "failed") for n in self.nodes.values())

    @property
    def done_count(self) -> int:
        with self._lock:
            return sum(1 for n in self.nodes.values() if n.status == "done")

    @property
    def total_count(self) -> int:
        return len(self.nodes)

    def render(self) -> RenderableType:
        """渲染任务图。"""
        with self._lock:
            nodes = list(self.nodes.values())

        if not nodes:
            return Text("  (暂无任务)", style="dim")

        # 按 ID 排序
        nodes.sort(key=lambda n: n.id)

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("ID", width=4, style="dim")
        table.add_column("状态", width=3)
        table.add_column("Agent", width=14)
        table.add_column("任务", width=25)
        table.add_column("依赖", width=12, style="dim")

        for node in nodes:
            icon = STATUS_ICONS.get(node.status, "❓")
            style = STATUS_STYLES.get(node.status, "dim")
            agent_color = _agent_color(node.agent)
            agent_icon = _agent_icon(node.agent)

            deps_str = ", ".join(str(d) for d in node.depends_on) if node.depends_on else "—"

            table.add_row(
                str(node.id),
                icon,
                f"[{agent_color}]{agent_icon} {node.agent}[/]",
                f"[{style}]{node.task}[/]",
                deps_str,
            )

        return table


# ── Dashboard 主类 ────────────────────────────────────────────────


class AgentDashboard:
    """Rich 多面板仪表盘 — 集成任务图、Agent 面板、数据流日志。

    使用方式：
        dashboard = AgentDashboard(console=Console())
        with dashboard.run():
            # 更新任务图
            dashboard.task_graph.set_nodes([...])
            # Agent 输出
            dashboard.get_panel("omniagent").append("list_files: done")
            # 数据流
            dashboard.dataflow.log_request("omniagent", "analyze_code")
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        title: str = "Agent Hub",
        refresh_per_second: int = 8,
    ) -> None:
        self.console = console or Console(force_terminal=True)
        self.title = title
        self.refresh_per_second = refresh_per_second

        # 子组件
        self.task_graph = TaskGraph()
        self.dataflow = DataFlowLog()
        self.agent_panels: dict[str, AgentOutputPanel] = {}
        self.result_text: str = ""

        # Rich Live 实例（在 run() 中创建）
        self._live: Live | None = None

    def get_panel(self, agent_name: str) -> AgentOutputPanel:
        """获取或创建 Agent 输出面板。"""
        if agent_name not in self.agent_panels:
            self.agent_panels[agent_name] = AgentOutputPanel(agent_name)
        return self.agent_panels[agent_name]

    def ensure_panels(self, agent_names: list[str]) -> None:
        """确保所有指定 Agent 都有面板。"""
        for name in agent_names:
            self.get_panel(name)

    def set_result(self, text: str) -> None:
        """设置最终结果文本。"""
        self.result_text = text

    def run(self) -> Live:
        """创建 Rich Live 上下文管理器。

        使用方式：
            with dashboard.run():
                # 持续更新 dashboard 组件
                ...
        """
        self._live = Live(
            self._build_layout(),
            console=self.console,
            refresh_per_second=self.refresh_per_second,
            transient=False,
        )
        return self._live

    def refresh(self) -> None:
        """手动刷新仪表盘。"""
        if self._live:
            self._live.update(self._build_layout())

    def _build_layout(self) -> Layout:
        """构建 Rich Layout。"""
        root = Layout()

        # 顶部：标题栏
        root.split(
            Layout(name="header", size=1),
            Layout(name="body"),
        )

        # 标题
        root["header"].update(
            Panel(
                Align.center(f"[bold white]{self.title}[/] — 多 Agent 调度仪表盘"),
                style="bold blue",
            )
        )

        # 主体分为：左列（任务图 + Agent 面板）+ 右列（数据流 + 结果）
        body = Layout()
        body.split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )

        # 左列：任务图 + Agent 面板（上下分）
        left = Layout()
        # 动态分配：任务图固定 6 行，剩下给 Agent 面板
        left.split(
            Layout(name="task_graph", size=min(6 + len(self.task_graph.nodes), 12)),
            Layout(name="agent_outputs"),
        )

        left["task_graph"].update(
            Panel(
                self.task_graph.render(),
                title="📋 任务 DAG",
                border_style="cyan",
            )
        )

        # Agent 面板区
        agent_renderables: list[RenderableType] = []
        if self.agent_panels:
            for name, panel in self.agent_panels.items():
                agent_renderables.append(panel.render(width=50))
        else:
            agent_renderables.append(Text("  (等待 Agent 启动...)", style="dim"))

        left["agent_outputs"].update(
            Panel(
                _join_vertically(agent_renderables),
                title="🤖 Agent 实时输出",
                border_style="green",
            )
        )

        # 右列：数据流 + 结果
        right = Layout()
        right.split(
            Layout(name="dataflow", size=12),
            Layout(name="result"),
        )

        right["dataflow"].update(
            Panel(
                self.dataflow.render(),
                title="📡 数据流转日志",
                border_style="yellow",
            )
        )

        result_content: RenderableType
        if self.result_text:
            result_content = Text(self.result_text[:2000])
        else:
            result_content = Text("  (等待任务完成...)", style="dim")

        right["result"].update(
            Panel(
                result_content,
                title="📊 最终输出",
                border_style="white",
            )
        )

        body["left"] = left
        body["right"] = right
        root["body"] = body

        return root


def _join_vertically(renderables: list[RenderableType]) -> RenderableType:
    """垂直拼接多个 renderable。"""
    from rich.columns import Columns
    from rich.console import Group

    if len(renderables) == 1:
        return renderables[0]

    # 超过 2 个面板时使用表格布局
    if len(renderables) <= 2:
        return Group(*renderables)

    # 多面板：2 列网格
    return Columns(renderables, equal=False, expand=False)


# ── 健康监控仪表盘 ────────────────────────────────────────────────


class HealthDashboard:
    """Agent 健康监控仪表盘 — 用于 agent-hub start。

    与 AgentDashboard（用于 agent-hub run 的任务执行可视化）不同，
    HealthDashboard 专注于 Agent 进程的健康状态监控：
    - 每个 Agent 的状态面板（🟢/🔴/🟡 + PID + 运行时间）
    - 健康检查事件日志（最近 20 条）
    - 按 Ctrl+C 优雅退出

    使用方式：
        dash = HealthDashboard(console, agents)
        with dash.run():
            dash.update_agent("omniagent", "running", pid=12345, uptime=12.5)
            dash.log_event("Health check cycle complete")
            dash.refresh()
    """

    MAX_LOG_ENTRIES = 20

    def __init__(
        self,
        console: Console,
        agents: dict[str, Any],  # dict[str, AgentManifest]
        *,
        title: str = "Agent Hub — Health Monitor",
        refresh_per_second: int = 4,
    ) -> None:
        self.console = console
        self.agent_names = list(agents.keys()) if agents else []
        self.title = title
        self.refresh_per_second = refresh_per_second

        # 每个 Agent 的健康状态
        self._agent_health: dict[str, dict] = {
            name: {"status": "pending", "pid": 0, "uptime": 0.0, "last_check": ""}
            for name in self.agent_names
        }

        # 健康事件日志
        self._health_log: list[str] = []

        # Rich Live 实例
        self._live: Live | None = None

    # ── 更新 ───────────────────────────────────────────────────────

    def update_agent(
        self,
        name: str,
        status: str,
        pid: int = 0,
        uptime: float = 0.0,
    ) -> None:
        """更新单个 Agent 的实时状态。

        Args:
            name: Agent 名称
            status: running | starting | stopped | failed | pending
            pid: 进程 PID
            uptime: 运行时间（秒）
        """
        if name in self._agent_health:
            entry = self._agent_health[name]
            entry.update(
                status=status,
                pid=pid,
                uptime=uptime,
                last_check=time.strftime("%H:%M:%S"),
            )

    def log_event(self, message: str) -> None:
        """追加一条带时间戳的健康事件。

        Args:
            message: 事件描述（如 "omniagent health check FAILED"）
        """
        t = time.strftime("%H:%M:%S")
        self._health_log.append(f"[dim]{t}[/dim] {message}")
        if len(self._health_log) > self.MAX_LOG_ENTRIES:
            self._health_log = self._health_log[-self.MAX_LOG_ENTRIES:]

    # ── Life ────────────────────────────────────────────────────────

    def run(self) -> Live:
        """创建 Rich Live 上下文管理器。"""
        self._live = Live(
            self._build_layout(),
            console=self.console,
            refresh_per_second=self.refresh_per_second,
            transient=False,
        )
        return self._live

    def refresh(self) -> None:
        """手动刷新仪表盘。"""
        if self._live:
            self._live.update(self._build_layout())

    # ── 布局 ────────────────────────────────────────────────────────

    def _build_layout(self) -> Layout:
        """构建 HealthDashboard 布局。

        布局结构:
        ┌────────────────────────────────────────┐
        │  Header: Agent Hub — Health Monitor     │
        ├──────────────────┬─────────────────────┤
        │  Agent Status     │  Health Events      │
        │  (2/3 width)     │  (1/3 width)        │
        │                  │                    │
        │  🟢 omniagent    │  12:00:01 Started  │
        │    running 12s   │  12:00:05 Check OK │
        │  🟡 resume-sync  │  12:00:10 Check OK │
        │    starting      │                    │
        │  ⚫ smartbench   │                    │
        │    stopped       │                    │
        └──────────────────┴─────────────────────┘
        """
        root = Layout()
        root.split(
            Layout(name="header", size=1),
            Layout(name="body"),
        )

        # Header
        root["header"].update(
            Panel(
                Align.center(f"[bold white]{self.title}[/] — 按 [bold yellow]Ctrl+C[/] 停止"),
                style="bold blue",
            )
        )

        # Body: Agent 状态（左）+ 健康事件日志（右）
        body = Layout()
        body.split_row(
            Layout(name="agent_status", ratio=2),
            Layout(name="health_log", ratio=1),
        )

        # ── Agent 状态面板 ──
        agent_panels: list[RenderableType] = []
        for name in self.agent_names:
            h = self._agent_health.get(name, {})
            status = h.get("status", "pending")

            # 状态图标和颜色
            icon_map = {
                "running": "🟢",
                "starting": "🟡",
                "stopped": "⚫",
                "failed": "🔴",
                "pending": "⬡",
            }
            color_map = {
                "running": "green",
                "starting": "yellow",
                "stopped": "dim",
                "failed": "red",
                "pending": "dim",
            }
            icon = icon_map.get(status, "❓")
            color = color_map.get(status, "white")

            # 构建内容行
            lines = [f"{icon} [bold {color}]{status}[/bold {color}]"]

            if h.get("pid"):
                lines.append(f"   PID: {h['pid']}")
            if status == "running" and h.get("uptime", 0) > 0:
                uptime = h["uptime"]
                if uptime >= 3600:
                    lines.append(f"   Uptime: {uptime / 3600:.1f}h")
                elif uptime >= 60:
                    lines.append(f"   Uptime: {uptime / 60:.1f}m")
                else:
                    lines.append(f"   Uptime: {uptime:.0f}s")
            if h.get("last_check"):
                lines.append(f"   Last check: {h['last_check']}")

            agent_icon = _agent_icon(name)
            panel = Panel(
                Text("\n".join(lines)),
                title=f"{agent_icon} {name}",
                border_style=color,
                width=45,
            )
            agent_panels.append(panel)

        if not agent_panels:
            agent_panels.append(Text("  (无已注册的 Agent)", style="dim"))

        body["agent_status"].update(
            Panel(
                _join_vertically(agent_panels),
                title="🤖 Agent Status",
                border_style="cyan",
            )
        )

        # ── 健康事件日志 ──
        if self._health_log:
            log_text = Text("\n".join(self._health_log[-15:]))
        else:
            log_text = Text("  (等待健康检查...)", style="dim")

        body["health_log"].update(
            Panel(
                log_text,
                title="📡 Health Events",
                border_style="yellow",
            )
        )

        root["body"] = body
        return root
