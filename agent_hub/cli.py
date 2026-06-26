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


# ── CLI 入口组 ──────────────────────────────────────────────────────


@click.group()
@click.version_option(version="0.1.0", prog_name="agent-hub")
def main():
    """Agent Hub — 解耦的多 Agent 调度系统 + 可视化数据流仪表盘。

    通过 agent.yaml 清单发现专业 Agent，支持 DAG 并行调度和实时可视化。
    """
    pass


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
        bridge = CLIBridge()

        console.print("\n[bold cyan]🚀 启动 Agent...[/bold cyan]")
        manifest_list = list(agents_dict.values())
        results = await bridge.start_all(manifest_list)

        success_count = sum(1 for r in results.values() if r.status == "running")
        fail_count = len(results) - success_count

        table = Table(title="Agent 启动状态")
        table.add_column("Agent", style="cyan")
        table.add_column("状态")
        table.add_column("PID")

        for name, info in results.items():
            status_icon = "🟢" if info.is_running else "🔴"
            pid_str = str(info.pid) if info.pid else "—"
            table.add_row(name, f"{status_icon} {info.status}", pid_str)

        console.print(table)

        if success_count > 0:
            console.print(f"\n[green]✅ {success_count} 个 Agent 已启动[/green]")
            console.print("[dim]按 Ctrl+C 停止所有 Agent[/dim]")

            # 保持运行直到用户中断
            try:
                while True:
                    # 健康检查
                    for name in list(results.keys()):
                        healthy = await bridge.health_check(name)
                        if not healthy and results[name].status == "running":
                            console.print(f"[yellow]⚠ {name} 已停止响应[/yellow]")

                    await asyncio.sleep(5)
            except KeyboardInterrupt:
                console.print("\n[yellow]⏸ 正在停止所有 Agent...[/yellow]")
                await bridge.stop_all()
                console.print("[green]✅ 所有 Agent 已停止[/green]")

    asyncio.run(_start())


@main.command()
def stop():
    """停止所有 Agent。"""
    async def _stop():
        bridge = CLIBridge()
        agents_dict = _load_all_agents()

        # 尝试停止所有已知 Agent
        console.print("[yellow]⏸ 正在停止所有 Agent...[/yellow]")
        await bridge.stop_all()
        console.print("[green]✅ 所有 Agent 已停止[/green]")

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
