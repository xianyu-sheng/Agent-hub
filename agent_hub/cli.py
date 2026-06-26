"""CLI Entry Point — Agent-hub 命令行入口。

提供完整的 Agent 系统管理命令：
- start/stop/status — 一键启停所有 Agent
- agent list/info/register/reload/validate — 配置管理
- run — 执行多 Agent 任务

使用方式：
    agent-hub start
    agent-hub status
    agent-hub run "分析 omniagent 代码质量并更新简历"
    agent-hub agent list
    agent-hub agent info omniagent
    agent-hub agent validate
    agent-hub stop
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# ── Windows UTF-8 编码修复 ──────────────────────────────────────────
# Rich 在 Windows GBK 终端下输出 emoji 会触发 UnicodeEncodeError
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent_hub.manifest import AgentManifest, discover_from_registry
from agent_hub.bridge import AgentProcessRegistry, CLIBridge
from agent_hub.dashboard import _agent_icon

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

console = Console(force_terminal=True)


# ── 辅助函数 ────────────────────────────────────────────────────────


def _resolve_registry_dir() -> Path:
    """解析 agents.d/ 注册目录路径。"""
    # 优先使用环境变量
    env_dir = os.environ.get("AGENT_HUB_REGISTRY")
    if env_dir:
        return Path(env_dir)

    # 默认：agent-hub 项目根目录下的 agents.d/
    return Path(__file__).parent.parent / "agents.d"


def _load_all_agents() -> dict[str, AgentManifest]:
    """加载所有已注册的 Agent 清单。"""
    registry_dir = _resolve_registry_dir()
    if not registry_dir.is_dir():
        console.print(f"[yellow]⚠ 注册目录不存在: {registry_dir}[/yellow]")
        return {}
    return discover_from_registry(registry_dir)


def _get_model_priority() -> list[str]:
    """获取模型优先级列表。"""
    return os.environ.get(
        "AGENT_HUB_MODELS",
        "deepseek-v4-pro,claude-sonnet-4-6",
    ).split(",")


def _start_repl() -> None:
    """启动 Agent-hub 交互式命令中心（默认命令）。

    显示系统概览后进入 REPL 循环，用户可直接输入命令：
    - models add/remove/list    — 配置 LLM 模型
    - agent register/list/info  — 管理专业 Agent
    - start/stop/status         — 控制 Agent 系统
    - run <任务>                — 执行多 Agent 任务
    - help                      — 命令帮助
    - quit                      — 退出
    """
    import shlex

    from click.testing import CliRunner
    from rich.prompt import Prompt as RichPrompt

    # ── 欢迎页（静态渲染一次）──────────────────────────────────────
    _print_welcome_banner()

    # ── REPL 循环 ──────────────────────────────────────────────────
    runner = CliRunner()

    while True:
        try:
            # 使用 Rich Prompt 以正确渲染颜色/样式标记
            raw = RichPrompt.ask("[bold cyan]agent-hub[/] [bold green]>[/]")
            line = raw.strip()
            if not line:
                continue

            # 退出
            if line.lower() in ("quit", "exit", "q"):
                console.print("[dim]👋 再见！[/dim]")
                break

            # 本地命令（不需要走 Click）
            if line.lower() == "help" or line == "?":
                _repl_help()
                continue
            if line.lower() == "status":
                _print_welcome_banner()
                continue
            if line.lower() == "clear" or line.lower() == "cls":
                console.clear()
                _print_welcome_banner()
                continue

            # ── 委托给 Click CliRunner ──
            try:
                args = shlex.split(line)
            except ValueError as e:
                console.print(f"[red]✗ 参数解析错误: {e}[/red]")
                console.print("[dim]提示: 包含空格的参数请用引号包裹[/dim]")
                continue

            # CliRunner 捕获 SystemExit，不会杀死 REPL
            try:
                result = runner.invoke(
                    main, args,
                    catch_exceptions=False,  # 让 click.confirm 等能直面用户
                    standalone_mode=False,   # 不调用 sys.exit
                )
            except SystemExit:
                # Click 命令内部调用 sys.exit(1) 等，不要杀死 REPL
                continue
            except Exception as exc:
                console.print(f"[red]✗ 命令执行失败: {exc}[/red]")
                continue

        except KeyboardInterrupt:
            console.print("\n[dim]按 Ctrl+C 再次或输入 quit 退出[/dim]")
            continue
        except SystemExit:
            # 某些命令可能仍触发了 sys.exit，不要杀死 REPL
            continue
        except Exception as e:
            console.print(f"[red]✗ 内部错误: {e}[/red]")
            continue


def _print_welcome_banner() -> None:
    """打印系统概览欢迎横幅（静态，不阻塞）。"""
    from agent_hub.model_config import ModelConfigStore

    agents_dict = _load_all_agents()
    model_store = ModelConfigStore()
    model_entries = model_store.list_all()

    # 检查运行状态
    system_running = False
    try:
        from agent_hub.pid_store import PidFileStore
        pid_store = PidFileStore()
        existing = pid_store.load_all()
        system_running = len(existing) > 0
    except Exception:
        pass

    # ── Header ──
    console.print()
    console.rule("[bold white]🔄 Agent Hub — 多 Agent 中央调度系统[/]")
    console.print(f"[dim]v0.1.0 · 直接输入命令操作 · 输入 help 查看帮助 · quit 退出[/dim]")
    console.print()

    # ── 系统概览表 ──
    table = Table(title="📋 系统概览", border_style="cyan")
    table.add_column("项目", style="bold", width=14)
    table.add_column("详情")

    # Agent 行
    if agents_dict:
        agent_lines = []
        for name, m in sorted(agents_dict.items()):
            icon = _agent_icon(name)
            status_icon = "✅" if m.is_valid else "❌"
            agent_lines.append(
                f"{icon} [cyan]{name}[/cyan] {status_icon} "
                f"[dim]({m.protocol}, {len(m.capabilities.tasks)} tasks)[/dim]"
            )
        table.add_row("已注册 Agent", "\n".join(agent_lines))
    else:
        table.add_row("已注册 Agent", "[dim](无) — 使用 agent register <路径> 注册[/dim]")

    # 模型行
    if model_entries:
        model_lines = []
        for entry in model_entries:
            star = "⭐ " if entry.default else "  "
            prov = f" ({entry.provider})" if entry.provider else ""
            model_lines.append(f"{star}[green]{entry.name}[/green]{prov}")
        table.add_row("已配置模型", "\n".join(model_lines))
    else:
        table.add_row("已配置模型", "[dim](无) — 使用 models add ... 配置[/dim]")

    # 系统状态行
    if system_running:
        table.add_row("系统状态", "[green]🟢 运行中[/green]")
    else:
        table.add_row("系统状态", "[dim]⚫ 未启动[/dim]")

    console.print(table)

    # ── 上下文提示 ──
    if not model_entries:
        tip = (
            "[yellow]💡 检测到未配置模型，请先添加:[/yellow]\n"
            "   models add [cyan]deepseek-v4-pro[/cyan] "
            "--api-base [green]https://api.deepseek.com[/green] "
            "--api-key-env [green]DEEPSEEK_API_KEY[/green]"
        )
    elif not agents_dict or len(agents_dict) <= 1:
        tip = (
            "[yellow]💡 提示:[/yellow] "
            "使用 [bold]agent register <项目路径>[/bold] 注册专业 Agent，"
            "或 [bold]models add[/bold] 添加更多模型"
        )
    elif not system_running:
        tip = (
            "[yellow]💡 已就绪:[/yellow] "
            "输入 [bold]start[/bold] 启动 Agent 系统，"
            "或 [bold]run \"任务描述\"[/bold] 直接执行任务"
        )
    else:
        tip = (
            "[yellow]💡 系统运行中:[/yellow] "
            "输入 [bold]run \"任务描述\"[/bold] 执行任务，"
            "[bold]stop[/bold] 停止系统，[bold]status[/bold] 查看状态"
        )
    console.print(f"\n{tip}")
    console.print()


def _repl_help() -> None:
    """打印 REPL 帮助信息。"""
    from rich.columns import Columns

    console.print()
    console.rule("[bold]📖 可用命令[/bold]")
    console.print()

    commands = [
        ("[bold cyan]模型配置[/bold cyan]", ""),
        ("  models list", "列出已配置的 LLM 模型"),
        ("  models add <name> --api-base <url> --api-key-env <VAR>", "添加模型（支持 --provider, --set-default）"),
        ("  models remove <name>", "删除模型"),
        ("  models info <name>", "模型详情"),
        ("  models priority [--set a,b,c]", "查看/设置模型优先级"),
        ("", ""),
        ("[bold cyan]Agent 管理[/bold cyan]", ""),
        ("  agent list", "列出所有已注册 Agent"),
        ("  agent info <name>", "查看 Agent 详情"),
        ("  agent register <项目路径>", "注册新 Agent"),
        ("  agent validate", "验证所有 agent.yaml"),
        ("  agent models [name]", "查看 Agent 声明的模型"),
        ("", ""),
        ("[bold cyan]系统控制[/bold cyan]", ""),
        ("  start", "启动所有 Agent（进入健康监控面板）"),
        ("  stop", "停止所有 Agent"),
        ("  status", "刷新系统概览"),
        ("", ""),
        ("[bold cyan]任务执行[/bold cyan]", ""),
        ("  run <任务描述>", "执行多 Agent 任务（自然语言）"),
        ("  run", "进入交互式任务模式"),
        ("", ""),
        ("[bold cyan]其他[/bold cyan]", ""),
        ("  help / ?", "显示此帮助"),
        ("  clear / cls", "清屏"),
        ("  quit / exit / q", "退出 Agent-hub"),
    ]

    for cmd, desc in commands:
        if cmd:
            console.print(f"  {cmd:<52} [dim]{desc}[/dim]")
        else:
            console.print()

    console.print()
    console.print("[dim]提示: 包含空格的参数请用引号包裹，如 run \"分析代码并更新简历\"[/dim]")
    console.print()


# ── CLI 入口组 ──────────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.version_option(version="0.1.0", prog_name="agent-hub")
@click.pass_context
def main(ctx):
    """Agent Hub — 解耦的多 Agent 调度系统 + 可视化数据流仪表盘。

    通过 agent.yaml 清单发现专业 Agent，支持 DAG 并行调度和实时可视化。

    \b
    直接运行 agent-hub（无子命令）进入启动欢迎页，查看系统状态和快速开始指南。
    """
    if ctx.invoked_subcommand is None:
        # 无子命令 → 启动交互式命令中心 (REPL)
        _start_repl()


# ── start / stop / status ────────────────────────────────────────────


@main.command()
@click.option(
    "--agents", "-a",
    default=None,
    help="只启动指定 Agent（逗号分隔），默认启动全部",
)
def start(agents: str | None):
    """启动 Agent-hub + 自动拉起所有注册的专业 Agent。"""
    registry_dir = _resolve_registry_dir()

    if not registry_dir.is_dir():
        console.print(f"[red]✗ 注册目录不存在: {registry_dir}[/red]")
        console.print("[dim]请先使用 'agent-hub agent register <项目路径>' 注册 Agent[/dim]")
        sys.exit(1)

    agents_dict = _load_all_agents()
    if not agents_dict:
        console.print("[red]✗ 未发现任何 Agent[/red]")
        console.print(f"[dim]请在 {registry_dir}/ 中添加注册文件[/dim]")
        sys.exit(1)

    console.print(f"[dim]发现 {len(agents_dict)} 个 Agent:[/dim]")
    for name, manifest in agents_dict.items():
        console.print(f"  • {name}: {manifest.display_name}")

    # 过滤 Agent
    if agents:
        agent_names = set(a.strip() for a in agents.split(","))
        agents_dict = {k: v for k, v in agents_dict.items() if k in agent_names}
        if not agents_dict:
            console.print(f"[red]✗ 指定的 Agent 未找到: {agents}[/red]")
            sys.exit(1)

    async def _start():
        from agent_hub.dashboard import HealthDashboard
        from agent_hub.pid_store import PidFileStore

        bridge = CLIBridge()
        pid_store = PidFileStore()

        # 跳过 internal 协议的 Agent（Agent-hub 自身不需要作为进程启动）
        external_agents = {
            name: m for name, m in agents_dict.items()
            if m.protocol != "internal"
        }

        if not external_agents:
            console.print("[yellow]⚠ 没有需要通过子进程启动的外部 Agent[/yellow]")
            console.print("[dim]internal 协议的 Agent（如 agent-hub）在进程内运行，无需额外启动[/dim]")
            return

        # 创建健康监控仪表盘
        dash = HealthDashboard(console, external_agents, title="Agent Hub — Health Monitor")

        with dash.run():
            dash.log_event("正在启动 Agent...")
            dash.refresh()

            manifest_list = list(external_agents.values())
            results = await bridge.start_all(manifest_list)

            success_count = sum(1 for r in results.values() if r.status == "running")

            # 更新仪表盘 + 持久化 PID
            for name, info in results.items():
                dash.update_agent(name, info.status, info.pid)
                if info.is_running:
                    pid_store.save(
                        name, info.pid,
                        protocol=info.manifest.protocol if info.manifest else "cli",
                    )
                dash.log_event(
                    f"{name}: {'🟢 started' if info.is_running else '🔴 failed'} "
                    f"(pid={info.pid})"
                )

            dash.log_event(f"启动完成: {success_count}/{len(results)} 个 Agent 运行中")
            dash.refresh()

            if success_count == 0:
                dash.log_event("警告: 所有 Agent 启动失败")

            # 健康检查循环（在仪表盘内运行）
            try:
                while True:
                    for name in list(results.keys()):
                        healthy = await bridge.health_check(name)
                        info = bridge.registry.get(name)
                        status = "running" if healthy else "failed"
                        uptime = info.uptime_seconds if info else 0.0

                        dash.update_agent(name, status, info.pid if info else 0, uptime)

                        if not healthy and results.get(name) and results[name].status == "running":
                            dash.log_event(f"⚠ {name} 健康检查失败")

                    dash.log_event("Health check ✓")
                    dash.refresh()
                    await asyncio.sleep(5)

            except (KeyboardInterrupt, asyncio.CancelledError):
                dash.log_event("⏸ 正在停止所有 Agent...")
                dash.refresh()
                await bridge.stop_all()
                # 清理 PID 文件
                for name in results:
                    pid_store.remove(name)
                dash.log_event("✅ 所有 Agent 已停止")
                dash.refresh()

    asyncio.run(_start())


@main.command()
def stop():
    """停止通过 agent-hub start 启动的所有 Agent 进程。"""
    async def _stop():
        from agent_hub.pid_store import PidFileStore

        pid_store = PidFileStore()
        existing = pid_store.load_all()

        if not existing:
            console.print("[dim]没有持久化的 Agent 进程记录[/dim]")
            console.print("[dim]提示: agent-hub stop 只能停止通过 agent-hub start 启动的进程[/dim]")
            return

        console.print(f"[dim]发现 {len(existing)} 个 Agent 进程记录[/dim]")
        for name, proc in existing.items():
            console.print(f"  • {name} (pid={proc.pid}, protocol={proc.protocol})")

        console.print("\n[yellow]⏸ 正在停止所有 Agent...[/yellow]")
        count = await pid_store.stop_all()
        console.print(f"[green]✅ 已停止 {count} 个 Agent 进程[/green]")

    asyncio.run(_stop())


@main.command()
def status():
    """查看所有 Agent 的运行状态。"""
    agents_dict = _load_all_agents()

    if not agents_dict:
        console.print("[dim]未发现任何注册的 Agent[/dim]")
        return

    # 这里展示注册状态（非进程状态，因为进程状态需要运行时注册表）
    table = Table(title="Agent 注册状态")
    table.add_column("Agent", style="cyan")
    table.add_column("显示名")
    table.add_column("协议")
    table.add_column("任务数")
    table.add_column("配置路径")

    for name, manifest in agents_dict.items():
        table.add_row(
            name,
            manifest.display_name,
            manifest.protocol,
            str(len(manifest.capabilities.tasks)),
            manifest.source_path or "—",
        )

    console.print(table)
    console.print("[dim]提示: 运行 'agent-hub start' 启动所有 Agent[/dim]")


# ── agent 子命令组 ──────────────────────────────────────────────────


@main.group()
def agent():
    """Agent 注册与配置管理（只读）。"""
    pass


@agent.command("list")
def agent_list():
    """列出所有已注册 Agent 及状态。"""
    agents_dict = _load_all_agents()

    if not agents_dict:
        console.print("[dim]未发现任何注册的 Agent[/dim]")
        console.print(f"[dim]注册目录: {_resolve_registry_dir()}[/dim]")
        return

    table = Table(title="已注册 Agent")
    table.add_column("名称", style="cyan bold")
    table.add_column("显示名")
    table.add_column("协议")
    table.add_column("任务")
    table.add_column("状态")

    for name, m in sorted(agents_dict.items()):
        valid = "✅" if m.is_valid else "❌"
        tasks_str = ", ".join(t.name for t in m.capabilities.tasks)
        table.add_row(name, m.display_name, m.protocol, tasks_str, valid)

    console.print(table)
    console.print(f"\n[dim]共 {len(agents_dict)} 个 Agent | 注册目录: {_resolve_registry_dir()}[/dim]")


@agent.command("info")
@click.argument("name")
def agent_info(name: str):
    """查看指定 Agent 的完整配置。"""
    agents_dict = _load_all_agents()

    if name not in agents_dict:
        console.print(f"[red]✗ 未找到 Agent: {name}[/red]")
        available = ", ".join(sorted(agents_dict.keys()))
        console.print(f"[dim]可用: {available}[/dim]")
        sys.exit(1)

    manifest = agents_dict[name]

    import json

    console.print(Panel(
        f"[bold cyan]{manifest.display_name}[/bold cyan] (name: {manifest.name})\n\n"
        f"[dim]描述:[/dim] {manifest.description}\n\n"
        f"[dim]协议:[/dim] {manifest.protocol}\n"
        f"[dim]版本:[/dim] {manifest.version}\n"
        f"[dim]项目路径:[/dim] {manifest.source_path or '—'}\n\n"
        f"[dim]CLI 命令:[/dim] {manifest.interface.command}\n\n"
        f"[bold]任务列表:[/bold]\n" +
        "\n".join(
            f"  [cyan]• {t.name}[/cyan]: {t.description}"
            for t in manifest.capabilities.tasks
        ),
        title=f"Agent: {name}",
        border_style="cyan",
    ))


@agent.command("register")
@click.argument("project_path")
def agent_register(project_path: str):
    """注册新 Agent。

    PROJECT_PATH: 专业 Agent 的项目目录路径（如 D:/OmniAgent_CLI）
    """
    project = Path(project_path)
    if not project.is_dir():
        console.print(f"[red]✗ 目录不存在: {project}[/red]")
        sys.exit(1)

    agent_yaml = project / "agent.yaml"
    if not agent_yaml.exists():
        console.print(f"[yellow]⚠ {project}/ 中没有找到 agent.yaml[/yellow]")
        console.print("[dim]正在生成 agent.yaml 模板...[/dim]")

        # 自动检测项目类型并生成模板
        project_name = project.name.lower().replace("-", "_").replace(" ", "_")
        template = _generate_agent_yaml_template(project_name, project)
        agent_yaml.write_text(template, encoding="utf-8")
        console.print(f"[green]✅ 已生成 {agent_yaml}[/green]")
        console.print("[dim]请编辑此文件以完善 Agent 描述，然后运行 'agent-hub agent reload'[/dim]")

    # 在 agents.d/ 中创建注册文件
    registry_dir = _resolve_registry_dir()
    registry_dir.mkdir(parents=True, exist_ok=True)

    manifest = None
    if agent_yaml.exists():
        try:
            manifest = AgentManifest.from_yaml(str(agent_yaml))
        except Exception as e:
            console.print(f"[yellow]⚠ 解析 agent.yaml 失败: {e}[/yellow]")
            console.print("[dim]将创建基本注册文件，请修正后重载[/dim]")

    reg_name = manifest.name if manifest else project.name.lower().replace(" ", "_")
    reg_file = registry_dir / f"{reg_name}.yaml"

    if reg_file.exists():
        console.print(f"[yellow]⚠ 注册文件已存在: {reg_file}[/yellow]")
        if not click.confirm("是否覆盖？"):
            console.print("[dim]已取消[/dim]")
            return

    reg_content = f"# Agent Hub 注册文件 — {reg_name}\n"
    reg_content += f"project_path: {str(project.resolve())}\n"
    if manifest:
        reg_content += f"# Agent: {manifest.display_name}\n"
        reg_content += f"# 任务数: {len(manifest.capabilities.tasks)}\n"

    reg_file.write_text(reg_content, encoding="utf-8")
    console.print(f"[green]✅ 已注册: {reg_file}[/green]")
    console.print(f"[dim]  指向 → {project.resolve()}[/dim]")
    console.print("[dim]运行 'agent-hub agent reload' 加载新配置[/dim]")


@agent.command("reload")
def agent_reload():
    """重载所有 Agent 配置（清除缓存，重新读取 agents.d/）。"""
    registry_dir = _resolve_registry_dir()
    if not registry_dir.is_dir():
        console.print(f"[yellow]⚠ 注册目录不存在: {registry_dir}[/yellow]")
        return

    agents_dict = _load_all_agents()
    console.print(f"[green]✅ 已重载 {len(agents_dict)} 个 Agent 配置[/green]")

    for name, manifest in agents_dict.items():
        valid_icon = "✅" if manifest.is_valid else "❌"
        tasks_count = len(manifest.capabilities.tasks)
        console.print(f"  {valid_icon} {name}: {tasks_count} 个任务")


@agent.command("models")
@click.argument("name", required=False)
def agent_models(name: str | None):
    """查看已注册 Agent 声明的模型（只读）。

    NAME: 可选，指定 Agent 名则只显示该 Agent 的模型详情。
    """
    agents_dict = _load_all_agents()

    if not agents_dict:
        console.print("[dim]未发现任何注册的 Agent[/dim]")
        return

    if name:
        # 查看单个 Agent 的模型详情
        if name not in agents_dict:
            console.print(f"[red]✗ 未找到 Agent: {name}[/red]")
            available = ", ".join(sorted(agents_dict.keys()))
            console.print(f"[dim]可用: {available}[/dim]")
            sys.exit(1)

        manifest = agents_dict[name]
        models = manifest.capabilities.models

        table = Table(title=f"Agent '{name}' 模型配置")
        table.add_column("模型", style="cyan bold")
        for m in models:
            table.add_row(m)

        console.print(table)
        if not models:
            console.print("[dim]该 Agent 未在 agent.yaml 中声明 models 字段[/dim]")
            console.print("[dim]模型可能通过环境变量隐式配置[/dim]")

        console.print(Panel(
            f"[dim]配置位置:[/dim] {manifest.source_path}/agent.yaml\n"
            f"[dim]协议:[/dim] {manifest.protocol}",
            title=f"Agent: {manifest.display_name}",
            border_style="cyan",
        ))
    else:
        # 一览所有 Agent 的模型
        table = Table(title="所有 Agent 模型一览 (capabilities.models)")
        table.add_column("Agent", style="cyan bold")
        table.add_column("协议")
        table.add_column("模型", style="green")

        for agent_name, manifest in sorted(agents_dict.items()):
            models_str = ", ".join(manifest.capabilities.models) if manifest.capabilities.models else "(未声明)"
            table.add_row(
                f"{_agent_icon(agent_name)} {agent_name}",
                manifest.protocol,
                models_str,
            )

        console.print(table)
        console.print(f"\n[dim]共 {len(agents_dict)} 个 Agent | 模型声明在各项目的 agent.yaml 中[/dim]")
        console.print("[dim]提示: Agent-hub 自身的模型用 'agent-hub models list' 管理[/dim]")


@agent.command("validate")
def agent_validate():
    """验证所有 agent.yaml 的合法性（只读校验）。"""
    registry_dir = _resolve_registry_dir()
    if not registry_dir.is_dir():
        console.print(f"[yellow]⚠ 注册目录不存在: {registry_dir}[/yellow]")
        sys.exit(1)

    all_valid = True
    agent_count = 0

    for yaml_file in sorted(registry_dir.glob("*.yaml")):
        try:
            import yaml

            with open(yaml_file, "r", encoding="utf-8") as f:
                reg_data = yaml.safe_load(f) or {}

            project_path = reg_data.get("project_path", "")
            if not project_path:
                console.print(f"[yellow]⚠ {yaml_file.name}: 缺少 project_path[/yellow]")
                all_valid = False
                continue

            agent_yaml = Path(project_path) / "agent.yaml"
            if not agent_yaml.exists():
                console.print(f"[red]✗ {yaml_file.name}: agent.yaml 不存在 ({agent_yaml})[/red]")
                all_valid = False
                continue

            manifest = AgentManifest.from_yaml(str(agent_yaml))
            errors = manifest.validate()

            if errors:
                console.print(f"[red]✗ {manifest.name}:[/red]")
                for err in errors:
                    console.print(f"    {err}")
                all_valid = False
            else:
                console.print(
                    f"[green]✅ {manifest.name}[/green] — "
                    f"{len(manifest.capabilities.tasks)} 个任务, "
                    f"协议: {manifest.protocol}"
                )
                agent_count += 1

        except Exception as e:
            console.print(f"[red]✗ {yaml_file.name}: {e}[/red]")
            all_valid = False

    if all_valid:
        console.print(f"\n[green]✅ 全部 {agent_count} 个 Agent 验证通过[/green]")
    else:
        console.print(f"\n[yellow]⚠ 部分 Agent 验证失败，请修正对应 agent.yaml[/yellow]")
        console.print("[dim]提示: 手动编辑项目目录下的 agent.yaml，然后运行 'agent-hub agent reload'[/dim]")


# ── models 命令组 ────────────────────────────────────────────────────
# Agent-hub 自身的模型配置管理，持久化到 models.yaml


@main.group()
def models():
    """Agent-hub 模型配置管理（增删改查 + 优先级）。"""
    pass


@models.command("list")
def models_list():
    """列出 Agent-hub 已配置的所有模型。"""
    from agent_hub.model_config import ModelConfigStore

    store = ModelConfigStore()
    entries = store.list_all()

    if not entries:
        console.print("[dim]未配置任何模型[/dim]")
        console.print(f"[dim]使用 'agent-hub models add <name> --api-base <url> --api-key-env <VAR>' 添加模型[/dim]")
        return

    priority = store.get_priority()

    table = Table(title="Agent-hub 模型配置")
    table.add_column("优先级", style="dim", width=8)
    table.add_column("模型名", style="cyan bold")
    table.add_column("供应商")
    table.add_column("API Base")
    table.add_column("API Key")
    table.add_column("默认")

    for idx, entry in enumerate(entries):
        rank = priority.index(entry.name) + 1 if entry.name in priority else idx + 1
        key_display = "***" if entry.api_key and not entry.api_key.startswith("${") else (entry.api_key or "—")
        default_icon = "⭐" if entry.default else ""
        table.add_row(
            str(rank),
            entry.name,
            entry.provider or "—",
            entry.api_base or "—",
            key_display,
            default_icon,
        )

    console.print(table)
    console.print(f"\n[dim]共 {len(entries)} 个模型 | 配置文件: {store._config_path}[/dim]")
    console.print("[dim]API Key 为明文时显示 ***，环境变量引用显示 ${VAR_NAME}[/dim]")


@models.command("info")
@click.argument("name")
def models_info(name: str):
    """查看指定模型的完整配置。"""
    from agent_hub.model_config import ModelConfigStore

    store = ModelConfigStore()
    entry = store.get(name)

    if not entry:
        console.print(f"[red]✗ 未找到模型: {name}[/red]")
        available = ", ".join(e.name for e in store.list_all())
        if available:
            console.print(f"[dim]已配置: {available}[/dim]")
        else:
            console.print("[dim]使用 'agent-hub models add' 添加模型[/dim]")
        sys.exit(1)

    console.print(Panel(
        f"[bold cyan]{entry.name}[/bold cyan]\n\n"
        f"[dim]供应商:[/dim] {entry.provider or '—'}\n"
        f"[dim]API Base:[/dim] {entry.api_base or '—'}\n"
        f"[dim]API Key:[/dim] {'***' if entry.api_key else '—'} "
        f"{'(' + entry.api_key + ')' if entry.api_key.startswith('${') else ''}\n"
        f"[dim]支持的模型:[/dim] {', '.join(entry.models) if entry.models else entry.name}\n"
        f"[dim]默认模型:[/dim] {'⭐ 是' if entry.default else '否'}\n\n"
        f"[dim]解析后的 API Key:[/dim] "
        f"{'已设置' if entry.resolved_api_key else '⚠ 未设置'}",
        title=f"模型: {name}",
        border_style="cyan",
    ))


@models.command("add")
@click.argument("name")
@click.option("--api-base", required=True, help="API endpoint URL")
@click.option("--api-key", default=None, help="API Key（明文）")
@click.option("--api-key-env", default=None, help="API Key 环境变量名（推荐，如 DEEPSEEK_API_KEY）")
@click.option("--provider", default=None, help="模型供应商名（deepseek, anthropic, openai...）")
@click.option("--set-default", is_flag=True, help="设为默认模型（优先级最高）")
def models_add(
    name: str,
    api_base: str,
    api_key: str | None,
    api_key_env: str | None,
    provider: str | None,
    set_default: bool,
):
    """添加新的 LLM 模型配置。

    NAME: 模型标识名（如 deepseek-v4-pro）
    """
    from agent_hub.model_config import ModelConfigStore, ModelEntry

    if api_key and api_key_env:
        console.print("[red]✗ --api-key 和 --api-key-env 不能同时指定[/red]")
        sys.exit(1)
    if not api_key and not api_key_env:
        console.print("[red]✗ 必须指定 --api-key 或 --api-key-env[/red]")
        sys.exit(1)

    store = ModelConfigStore()
    entry = ModelEntry(
        name=name,
        provider=provider or _guess_provider(name),
        api_base=api_base,
        api_key=api_key or f"${{{api_key_env}}}",
        models=[name],
        default=set_default,
    )

    try:
        store.add(entry)
        console.print(f"[green]✅ 已添加模型: {name}[/green]")
        console.print(f"   API Base: {api_base}")
        console.print(f"   API Key:  {'${' + api_key_env + '}' if api_key_env else '***'}")
        if set_default:
            console.print(f"   ⭐ 已设为默认模型")
        console.print(f"\n[dim]配置文件: {store._config_path}[/dim]")
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)


@models.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="跳过确认")
def models_remove(name: str, yes: bool):
    """删除指定的模型配置。

    NAME: 要删除的模型名
    """
    from agent_hub.model_config import ModelConfigStore

    store = ModelConfigStore()
    entry = store.get(name)
    if not entry:
        console.print(f"[red]✗ 未找到模型: {name}[/red]")
        sys.exit(1)

    if not yes:
        console.print(f"[yellow]⚠ 确认删除模型 '{name}'？[/yellow]")
        if not click.confirm("删除后无法恢复，是否继续？"):
            console.print("[dim]已取消[/dim]")
            return

    store.remove(name)
    console.print(f"[green]✅ 已删除模型: {name}[/green]")


@models.command("update")
@click.argument("name")
@click.option("--api-key", default=None, help="新的 API Key（明文）")
@click.option("--api-key-env", default=None, help="新的 API Key 环境变量名")
@click.option("--api-base", default=None, help="新的 API Base URL")
@click.option("--provider", default=None, help="新的供应商名")
@click.option("--set-default", is_flag=True, help="设为默认模型")
def models_update(
    name: str,
    api_key: str | None,
    api_key_env: str | None,
    api_base: str | None,
    provider: str | None,
    set_default: bool,
):
    """更新指定模型的配置。

    NAME: 要更新的模型名
    """
    from agent_hub.model_config import ModelConfigStore

    if api_key and api_key_env:
        console.print("[red]✗ --api-key 和 --api-key-env 不能同时指定[/red]")
        sys.exit(1)

    store = ModelConfigStore()

    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if api_key_env:
        kwargs["api_key"] = f"${{{api_key_env}}}"
    if api_base:
        kwargs["api_base"] = api_base
    if provider:
        kwargs["provider"] = provider
    if set_default:
        kwargs["default"] = True

    if not kwargs:
        console.print("[yellow]⚠ 未指定要更新的字段[/yellow]")
        console.print("[dim]示例: agent-hub models update gpt-4o --api-key-env OPENAI_API_KEY[/dim]")
        return

    try:
        entry = store.update(name, **kwargs)
        console.print(f"[green]✅ 已更新模型: {name}[/green]")
        if set_default:
            console.print(f"   ⭐ 已设为默认模型")
        console.print(f"\n[dim]配置文件: {store._config_path}[/dim]")
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)


@models.command("priority")
@click.option("--set", "-s", "set_priority", default=None, help="设置优先级（逗号分隔，如 deepseek,claude）")
def models_priority(set_priority: str | None):
    """查看或设置模型优先级。

    不传 --set 则显示当前优先级。
    """
    from agent_hub.model_config import ModelConfigStore

    store = ModelConfigStore()
    priority = store.get_priority()

    if set_priority:
        names = [n.strip() for n in set_priority.split(",") if n.strip()]
        try:
            store.set_priority(names)
            console.print(f"[green]✅ 模型优先级已更新:[/green]")
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]")
            sys.exit(1)
    else:
        console.print("[bold]当前模型优先级:[/bold]")

    priority = store.get_priority()
    for idx, name in enumerate(priority):
        entry = store.get(name)
        provider = f" ({entry.provider})" if entry and entry.provider else ""
        default_mark = " ⭐" if entry and entry.default else ""
        console.print(f"  {idx + 1}. [cyan]{name}[/cyan]{provider}{default_mark}")

    if not priority:
        console.print("[dim]  未配置任何模型[/dim]")

    console.print(f"\n[dim]配置文件: {store._config_path}[/dim]")
    console.print("[dim]提示: 未包含在优先级列表中的模型不会被自动使用[/dim]")


def _guess_provider(name: str) -> str:
    """根据模型名猜测供应商。"""
    name_lower = name.lower()
    if "deepseek" in name_lower:
        return "deepseek"
    if "claude" in name_lower or "anthropic" in name_lower:
        return "anthropic"
    if "gpt" in name_lower or "openai" in name_lower:
        return "openai"
    if "qwen" in name_lower:
        return "qwen"
    if "glm" in name_lower:
        return "glm"
    if "doubao" in name_lower:
        return "doubao"
    if "moonshot" in name_lower or "kimi" in name_lower:
        return "moonshot"
    if "ollama" in name_lower:
        return "ollama"
    return "unknown"


# ── run ──────────────────────────────────────────────────────────────


@main.command()
@click.argument("task", required=False)
@click.option("--no-dashboard", "-n", is_flag=True, help="不显示仪表盘（纯命令行模式）")
@click.option("--timeout", "-t", default=300, help="每个 Agent 任务的超时秒数")
def run(task: str | None, no_dashboard: bool, timeout: int):
    """执行多 Agent 任务。

    TASK: 自然语言任务描述（如 "分析 omniagent 代码质量并更新简历"）
    """
    if not task:
        # 交互模式
        console.print("[bold cyan]Agent Hub — 交互模式[/bold cyan]")
        console.print("[dim]输入任务描述，或 'quit' 退出[/dim]")
        while True:
            task = click.prompt("任务", prompt_suffix="> ")
            if task.lower() in ("quit", "exit", "q"):
                break
            if task.strip():
                _run_task(task, no_dashboard, timeout)
    else:
        _run_task(task, no_dashboard, timeout)


def _run_task(task: str, no_dashboard: bool, timeout: int):
    """执行单个任务。"""
    from agent_hub.scheduler import AgentScheduler

    async def _exec():
        scheduler = AgentScheduler(
            model_priority=_get_model_priority(),
            registry_dir=str(_resolve_registry_dir()),
            default_timeout=timeout,
        )

        result = await scheduler.execute(
            task,
            show_dashboard=not no_dashboard,
        )

        if result.route_plan.tasks:
            console.print(f"\n[bold]📊 执行结果:[/bold]")
            console.print(result.summary_str())
        else:
            console.print(f"\n[yellow]{result.route_plan.analysis}[/yellow]")

    asyncio.run(_exec())


# ── 模板生成 ────────────────────────────────────────────────────────


def _generate_agent_yaml_template(project_name: str, project_path: Path) -> str:
    """为新项目生成 agent.yaml 模板。

    注意：Windows 路径使用 as_posix() 转换为正斜杠格式，
    避免反斜杠被 YAML 解析器解释为转义序列（如 \\r → 回车）。
    """
    # 转换为正斜杠路径，防止 YAML 转义问题
    # 例如 D:\\工作\\resume-sync 中的 \\r 会被 YAML 读成回车符
    safe_path = project_path.resolve().as_posix()

    return f"""# Agent Manifest — {project_name} 的自描述清单
# 此文件供 Agent-hub 读取，永不修改。配置变更请手动编辑。

name: {project_name}
display_name: "{project_name} Agent"
description: |
  描述此 Agent 的功能和特点（供 LLM 路由使用）。
protocol: cli
version: "1.0"

capabilities:
  tasks:
    - name: example_task
      description: 示例任务 — 请根据实际功能修改
      input:
        goal: "string — 任务目标"
      output:
        result: "string — 执行结果"

  # 可选：全局工具列表
  # tools: []
  # models: []
  # modes: [react]

interface:
  command: cd {safe_path} && python -m {project_name} --json-output "{{goal}}"
"""
