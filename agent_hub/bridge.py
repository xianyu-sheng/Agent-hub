"""CLI Bridge — 通过子进程调用专业 Agent + Agent 生命周期管理。

核心职责：
1. 任务执行：根据 AgentManifest 启动子进程，传入 JSON 参数，捕获输出
2. 生命周期管理：启动/停止/健康检查 Agent 后台进程
3. 流式回调：stdout 逐行回调 → Dashboard 实时更新

设计原则：
- 零侵入：通过 CLI 调用，Agent 项目无需引入任何 SDK
- asyncio 子进程：非阻塞 I/O，与 omniagent 的 asyncio 并发模式一致
- 超时安全：每个任务有独立超时，异常可捕获
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_hub.manifest import AgentManifest

logger = logging.getLogger(__name__)

# ── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class AgentResult:
    """Agent 任务执行结果。"""

    agent_name: str
    task_name: str
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0
    exit_code: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.success and self.exit_code == 0

    @property
    def summary(self) -> str:
        status = "✅" if self.success else "❌"
        duration = f"({self.duration_ms:.0f}ms)" if self.duration_ms > 0 else ""
        return f"{status} [{self.agent_name}] {self.task_name} {duration}"


@dataclass
class ProcessInfo:
    """Agent 进程信息。"""

    name: str
    process: asyncio.subprocess.Process | None = None
    manifest: AgentManifest | None = None
    status: str = "stopped"  # starting | running | stopping | stopped | failed
    started_at: float = 0.0
    pid: int = 0
    desired_state: str = "stopped"  # running | stopped — 用户期望的状态（用于 Watchdog 判断）
    restart_count: int = 0          # 本次会话内累计重启次数
    last_restart_time: float = 0.0  # 上次重启的时间戳（monotonic）

    # Watchdog 限制常量
    MAX_RESTARTS = 3                # 5 分钟内最多重启次数
    RESTART_WINDOW = 300.0          # 重启计数窗口（秒）

    @property
    def is_running(self) -> bool:
        return self.status == "running" and self.process is not None

    @property
    def uptime_seconds(self) -> float:
        if self.started_at <= 0:
            return 0.0
        return time.monotonic() - self.started_at

    @property
    def can_restart(self) -> bool:
        """是否还允许自动重启（未超过窗口内最大次数）。"""
        now = time.monotonic()
        if now - self.last_restart_time > self.RESTART_WINDOW:
            # 窗口外 — 重置计数
            return True  # 允许重启（调用处会重置计数）
        return self.restart_count < self.MAX_RESTARTS


# ── Agent 进程注册表 ────────────────────────────────────────────────


class AgentProcessRegistry:
    """管理所有 Agent 子进程的生命周期。

    提供类似 systemd 的 start/stop/status 接口。
    """

    def __init__(self) -> None:
        self._processes: dict[str, ProcessInfo] = {}
        self._monitor_tasks: dict[str, asyncio.Task] = {}

    def register(
        self,
        name: str,
        process: asyncio.subprocess.Process,
        manifest: AgentManifest,
    ) -> ProcessInfo:
        """注册一个新启动的 Agent 进程。"""
        info = ProcessInfo(
            name=name,
            process=process,
            manifest=manifest,
            status="starting",
            started_at=time.monotonic(),
            pid=process.pid or 0,
        )
        self._processes[name] = info
        return info

    def get(self, name: str) -> ProcessInfo | None:
        """获取指定 Agent 的进程信息。"""
        return self._processes.get(name)

    def update_status(self, name: str, status: str) -> None:
        """更新 Agent 进程状态。"""
        if name in self._processes:
            self._processes[name].status = status

    def remove(self, name: str) -> None:
        """从注册表移除 Agent 及其监控任务。"""
        # 取消该 Agent 的后台监控任务，防止资源泄漏
        task = self._monitor_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
        self._processes.pop(name, None)

    def list_all(self) -> list[ProcessInfo]:
        """列出所有已注册的 Agent 进程（按名称排序）。"""
        return sorted(self._processes.values(), key=lambda p: p.name)

    def list_running(self) -> list[ProcessInfo]:
        """列出正在运行的 Agent 进程。"""
        return [p for p in self._processes.values() if p.is_running]

    def list_by_status(self, status: str) -> list[ProcessInfo]:
        """按状态列出 Agent 进程。"""
        return [p for p in self._processes.values() if p.status == status]

    @property
    def running_count(self) -> int:
        return len(self.list_running())

    @property
    def total_count(self) -> int:
        return len(self._processes)

    def status_summary(self) -> str:
        """生成状态摘要文本。"""
        lines = []
        for info in self.list_all():
            icon = {
                "running": "🟢",
                "starting": "🟡",
                "stopping": "🟠",
                "stopped": "⚫",
                "failed": "🔴",
            }.get(info.status, "❓")
            uptime = f" {info.uptime_seconds:.0f}s" if info.is_running else ""
            lines.append(f"  {icon} {info.name}: {info.status}{uptime}")
        return "\n".join(lines) if lines else "  (无已注册进程)"


# ── CLI Bridge ──────────────────────────────────────────────────────


class CLIBridge:
    """通过 CLI 子进程调用专业 Agent。

    支持：
    - 命令模板变量替换（{goal}, {task}, {mode} 等）
    - stdout 流式回调（→ Dashboard 实时输出）
    - 超时控制
    - 错误捕获和 JSON 解析

    使用方式：
        bridge = CLIBridge()
        result = await bridge.execute(
            manifest, "analyze_code",
            params={"goal": "分析项目结构"},
            on_stdout=lambda line: print(line),
        )
    """

    def __init__(
        self,
        *,
        default_timeout: int = 300,
        registry: AgentProcessRegistry | None = None,
    ) -> None:
        self.default_timeout = default_timeout
        self.registry = registry or AgentProcessRegistry()

    # ── 任务执行 ─────────────────────────────────────────────────

    async def execute(
        self,
        manifest: AgentManifest,
        task_name: str,
        params: dict[str, Any] | None = None,
        *,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        timeout: int | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> AgentResult:
        """通过 CLI 子进程调用 Agent 执行任务。

        Args:
            manifest: Agent 清单
            task_name: 要执行的任务名
            params: 任务参数（将序列化为 JSON 或命令行参数）
            on_stdout: 每行 stdout 的回调（用于实时 UI 更新）
            on_stderr: 每行 stderr 的回调
            timeout: 超时秒数（默认使用 manifest 中的配置）
            extra_env: 额外的环境变量

        Returns:
            AgentResult 包含执行结果
        """
        params = params or {}
        timeout = timeout or self.default_timeout

        # internal 协议防御：内部 Agent 不应通过子进程执行
        # 这由 AgentScheduler._execute_single_task 在调度层拦截，
        # 此处的检查是防御深度——若有人直接调用 bridge.execute(internal_agent, ...)
        # 会得到明确的错误信息而非未定义行为。
        if manifest.protocol == "internal":
            return AgentResult(
                agent_name=manifest.name,
                task_name=task_name,
                success=False,
                error=(
                    f"Agent '{manifest.name}' 使用 internal 协议，"
                    f"不能通过 CLI Bridge 子进程执行。"
                    f"任务 '{task_name}' 应由 AgentScheduler 在进程内分派。"
                ),
            )

        # 未实现协议防御：mcp / http 协议尚未实现，此处拦截防止静默失败。
        # 当这些协议实现时（如 MCPBridge / HTTPBridge），移除此检查即可。
        UNSUPPORTED = {"mcp", "http"}
        if manifest.protocol in UNSUPPORTED:
            return AgentResult(
                agent_name=manifest.name,
                task_name=task_name,
                success=False,
                error=(
                    f"协议 '{manifest.protocol}' 尚未实现。"
                    f"当前仅支持 cli 和 internal 协议。"
                    f"Agent '{manifest.name}' 的任务 '{task_name}' 无法通过 CLI Bridge 执行。"
                ),
            )

        # 检查 manifest 约束中的 timeout
        if "timeout" in manifest.capabilities.constraints:
            timeout = min(timeout, manifest.capabilities.constraints["timeout"])

        # 构建命令
        raw_cmd = self._build_command(manifest, task_name, params)
        env = self._build_env(manifest, extra_env)

        # 处理 shell 语法：提取 cd <path> && 前缀，设置 cwd
        cwd = manifest.source_path or None
        cmd = self._strip_shell_prefix(raw_cmd)
        extracted_cwd = self._extract_cd_path(raw_cmd)
        if extracted_cwd:
            cwd = extracted_cwd

        logger.info(
            "执行 Agent: %s task=%s timeout=%ds cwd=%s cmd=%s",
            manifest.name, task_name, timeout, cwd, " ".join(cmd),
        )

        start_time = time.monotonic()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            # 启动子进程（不使用 shell，直接调用可执行文件）
            # stdin=DEVNULL 防止子进程竞争终端输入（如 omniagent REPL 的 Prompt.ask()）
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=cwd,
            )

            # 流式读取 stdout + stderr
            async def read_stream(
                stream: asyncio.StreamReader | None,
                collector: list[str],
                callback: Callable[[str], None] | None,
            ) -> None:
                if stream is None:
                    return
                while True:
                    line_bytes = await stream.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
                    collector.append(line)
                    if callback:
                        try:
                            callback(line)
                        except Exception:
                            pass  # 回调异常不中断执行

            # 并行读取 stdout 和 stderr
            stdout_task = asyncio.create_task(
                read_stream(process.stdout, stdout_lines, on_stdout)
            )
            stderr_task = asyncio.create_task(
                read_stream(process.stderr, stderr_lines, on_stderr)
            )

            # 等待进程完成（带超时）
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("Agent %s 任务 %s 超时 (%ds)，正在终止", manifest.name, task_name, timeout)

                # 优雅终止 → 强制终止 → 确认进程退出
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except (asyncio.TimeoutError, Exception):
                    # SIGTERM 未响应，发送 SIGKILL
                    try:
                        process.kill()
                        # 必须 await 确保进程被回收，否则会成为僵尸进程
                        await asyncio.wait_for(process.wait(), timeout=3)
                    except Exception:
                        pass  # 最终尝试，忽略所有错误

                # 取消流读取任务 — 进程已终止，继续读取只会无限等待
                for task in (stdout_task, stderr_task):
                    if not task.done():
                        task.cancel()

                duration_ms = (time.monotonic() - start_time) * 1000
                return AgentResult(
                    agent_name=manifest.name,
                    task_name=task_name,
                    success=False,
                    output="\n".join(stdout_lines),
                    error=f"任务超时 ({timeout}s)",
                    duration_ms=duration_ms,
                    exit_code=-1,
                )

            # 等待流读取完成
            await asyncio.gather(stdout_task, stderr_task)

            duration_ms = (time.monotonic() - start_time) * 1000
            exit_code = process.returncode or 0

            stdout_text = "\n".join(stdout_lines)
            stderr_text = "\n".join(stderr_lines)

            # 尝试从 stdout 解析 JSON 结果
            parsed_result = self._try_parse_json_output(stdout_text)

            if exit_code == 0 and parsed_result:
                return AgentResult(
                    agent_name=manifest.name,
                    task_name=task_name,
                    success=True,
                    output=parsed_result.get("output", parsed_result.get("result", stdout_text)),
                    duration_ms=duration_ms,
                    exit_code=exit_code,
                    extra=parsed_result,
                )

            if exit_code != 0:
                return AgentResult(
                    agent_name=manifest.name,
                    task_name=task_name,
                    success=False,
                    output=stdout_text,
                    error=stderr_text or f"进程退出码: {exit_code}",
                    duration_ms=duration_ms,
                    exit_code=exit_code,
                )

            # 成功但无 JSON 输出 — 返回原始文本
            return AgentResult(
                agent_name=manifest.name,
                task_name=task_name,
                success=True,
                output=stdout_text,
                error=stderr_text,
                duration_ms=duration_ms,
                exit_code=exit_code,
            )

        except FileNotFoundError as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            return AgentResult(
                agent_name=manifest.name,
                task_name=task_name,
                success=False,
                error=f"命令未找到: {e}",
                duration_ms=duration_ms,
                exit_code=-2,
            )
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            logger.error("Agent %s 执行异常: %s", manifest.name, e, exc_info=True)
            return AgentResult(
                agent_name=manifest.name,
                task_name=task_name,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                exit_code=-3,
            )

    # ── 命令构建 ─────────────────────────────────────────────────

    # ── Goal 截断阈值 ──────────────────────────────────────────────
    # CLI 参数（如 --concern "{goal}"）不应超过此长度，否则会导致 argparse
    # 解析失败或 shell 参数溢出。典型失败：smartbench 的 --concern 收到
    # 200+ 字任务描述时秒退（exit code 1，耗时 < 500ms）。
    _MAX_GOAL_LENGTH = 120

    @staticmethod
    def _sanitize_goal(goal: str, max_len: int = 120) -> str:
        """清理并截断 goal 字符串，使其适合作为 CLI 参数。

        1. 移除换行符和多余空白
        2. 移除 shell 命令注入字符（管道、后台、分隔符、命令替换等）
        3. 保留反斜杠（Windows 路径如 D:\\OmniAgent_CLI 需要）
        4. 保留引号（shlex.split 会正确处理）
        5. 截断到 max_len（在词边界处截断）
        """
        # 合并空白，移除换行
        cleaned = " ".join(goal.split())
        # 仅移除 shell 命令注入和 I/O 重定向字符
        # 注意：必须保留反斜杠 (\\)——否则 Windows 路径 D:\\project 会变成 D:project
        for char in ['|', '&', ';', '$', '`', '!', '<', '>']:
            cleaned = cleaned.replace(char, "")
        if len(cleaned) <= max_len:
            return cleaned
        # 在词边界处截断
        truncated = cleaned[:max_len].rsplit(" ", 1)[0]
        return truncated

    @staticmethod
    def _extract_project_path(text: str) -> str | None:
        """从文本中提取项目路径。

        支持 Windows 绝对路径（D:\\project）和 Unix 风格路径（/home/user/project）。
        返回正斜杠格式的路径（兼容 Windows 和 Unix），或 None。
        """
        if not text:
            return None
        # Windows 绝对路径: C:\path\to\project 或 C:/path/to/project
        m = re.search(r"([A-Za-z]:[\\/][^\s,，。；;]+)", text)
        if m:
            raw = m.group(1)
            # 规范化为正斜杠
            return raw.replace("\\", "/")
        # Unix 绝对路径: /home/user/project (需要至少 2 层深度以避免误匹配)
        m = re.search(r"(/[^\s,，。；;]{2,}(?:/[^\s,，。；;]+)+)", text)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _ensure_project_dir(project: str) -> str:
        """将文件路径转换为项目目录（如果传入的是文件而非目录）。

        某些 Agent（如 smartbench）的 --project 参数要求传入项目根目录，
        若传入具体文件（如 scheduler.py）会导致 resolve_project_path() 的
        is_dir() 检查失败，输出 "Cannot access"。

        策略：
        - 如果路径以 .py/.js/.ts/.java/.go/.rs 等常见源码扩展名结尾 → 取父目录
        - 否则原样返回
        """
        if not project:
            return project
        import os
        # 常见源码文件扩展名
        _SRC_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go",
                     ".rs", ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
                     ".swift", ".kt", ".scala", ".r", ".m", ".mm"}
        _, ext = os.path.splitext(project)
        if ext.lower() in _SRC_EXTS:
            parent = os.path.dirname(project)
            if parent and os.path.isdir(parent):
                logger.debug(
                    "文件路径 → 项目目录: %s → %s", project, parent
                )
                return parent.replace("\\", "/")
        return project

    def _build_command(
        self,
        manifest: AgentManifest,
        task_name: str,
        params: dict[str, Any],
    ) -> list[str]:
        """根据 manifest 的 interface.command 模板构建 CLI 命令。

        支持的占位符：
        - {task} → 任务名
        - {goal} → params["goal"]（自动截断至 120 字符，防止 CLI 参数溢出）
        - {mode} → params["mode"] 或 "react"
        - {project} → params["project"] 或 ""
        - {params_json} → 完整 params 的 JSON 字符串
        """
        template = manifest.interface.command
        raw_goal = str(params.get("goal", params.get("task_description", "")))
        mode = str(params.get("mode", "react"))
        project = str(params.get("project", params.get("project_path", "")))

        # 从 goal 文本自动提取项目路径（覆盖 LLM 路由可能产生的损坏路径）
        extracted = self._extract_project_path(raw_goal)
        if extracted:
            if project and project != extracted:
                logger.debug(
                    "覆盖路由提供的 project: %s → %s", project, extracted
                )
            project = extracted
        elif not project:
            project = ""

        # 文件路径 → 项目目录转换
        project = self._ensure_project_dir(project)

        # 项目路径 → 项目名转换（用于 resume-sync 等按名称索引的 Agent）
        if project and "{project}" in template:
            if "--project" not in template and "-p " not in template:
                import os as _os
                project_name = _os.path.basename(project.rstrip("/\\"))
                if project_name:
                    logger.debug(
                        "项目路径 → 项目名: %s → %s", project, project_name
                    )
                    project = project_name

        # 注入 Pipeline 上下文 — 在所有拼接完成后统一截断
        pipeline_ctx = str(params.get("_pipeline_context", ""))
        if pipeline_ctx:
            raw_goal = f"{raw_goal}\n\n{pipeline_ctx}"

        max_len = self._MAX_GOAL_LENGTH * 3 if pipeline_ctx else self._MAX_GOAL_LENGTH
        goal = self._sanitize_goal(raw_goal, max_len)
        if len(raw_goal) > max_len:
            logger.debug(
                "Goal 从 %d 字符截断至 %d (max=%d)",
                len(raw_goal), len(goal), max_len,
            )

        command_str = template.replace("{task}", task_name)
        command_str = command_str.replace("{goal}", goal)
        command_str = command_str.replace("{mode}", mode)
        command_str = command_str.replace("{project}", project)
        # 从 params_json 中移除内部字段，避免泄露到 Agent CLI
        clean_params = {k: v for k, v in params.items() if not k.startswith("_")}
        command_str = command_str.replace(
            "{params_json}", json.dumps(clean_params, ensure_ascii=False)
        )

        # 简单的 shell 分词（支持引号）
        try:
            return shlex.split(command_str)
        except ValueError:
            # 回退：按空格分割
            return command_str.split()

    @staticmethod
    def _extract_cd_path(cmd: list[str]) -> str | None:
        """从命令列表中提取 cd <path> && 中的路径。

        例: ['cd', 'D:/proj', '&&', 'tool', '--flag']
            → 'D:/proj'
        """
        if len(cmd) >= 3 and cmd[0] == "cd" and cmd[2] in ("&&", ";"):
            path = cmd[1]
            if path and Path(path).is_dir():
                return path
        return None

    @staticmethod
    def _strip_shell_prefix(cmd: list[str]) -> list[str]:
        """去除命令中的 shell 前缀（cd <path> &&/;）。

        例: ['cd', 'D:/proj', '&&', 'tool', '--flag']
            → ['tool', '--flag']
        """
        if len(cmd) >= 3 and cmd[0] == "cd" and cmd[2] in ("&&", ";"):
            return cmd[3:]
        return cmd

    def _build_env(
        self,
        manifest: AgentManifest,
        extra_env: dict[str, str] | None,
    ) -> dict[str, str]:
        """构建子进程环境变量。"""
        env = os.environ.copy()

        # manifest 中声明的环境变量
        for k, v in manifest.interface.env.items():
            env[k] = v

        # 调用时额外传入的环境变量
        if extra_env:
            for k, v in extra_env.items():
                env[k] = v

        # 保证 UTF-8 编码
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")

        return env

    # ── 输出解析 ─────────────────────────────────────────────────

    @staticmethod
    def _try_parse_json_output(stdout: str) -> dict[str, Any] | None:
        """尝试从 stdout 中提取 JSON 结果。

        支持两种格式：
        1. 整行是 JSON：{"result": "...", "output": "..."}
        2. stdout 末尾有 JSON 块（omniagent 的 --json-output 模式）
        """
        if not stdout or not stdout.strip():
            return None

        # 尝试解析整个输出为 JSON
        try:
            data = json.loads(stdout.strip())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # 尝试在输出中查找 JSON 块（按行）
        lines = stdout.strip().split("\n")
        # 从最后一行开始反向查找
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and any(
                        k in data for k in ("result", "output", "summary", "error")
                    ):
                        return data
                except (json.JSONDecodeError, ValueError):
                    continue

        return None

    # ── Agent 生命周期管理 ────────────────────────────────────────

    async def start_agent(
        self,
        manifest: AgentManifest,
        *,
        on_stdout: Callable[[str], None] | None = None,
    ) -> ProcessInfo:
        """启动一个 Agent 为后台进程。

        Agent 进程将保持运行，监听后续请求（通过 HTTP/MCP 等协议）。
        对于 CLI 协议的 Agent，此方法启动一个守护进程。

        Args:
            manifest: Agent 清单
            on_stdout: stdout 回调

        Returns:
            ProcessInfo 描述启动的进程
        """
        name = manifest.name

        # 检查是否已在运行
        existing = self.registry.get(name)
        if existing and existing.is_running:
            logger.info("Agent %s 已在运行 (pid=%d)，跳过启动", name, existing.pid)
            return existing

        # 构建启动命令
        raw_cmd = self._build_daemon_command(manifest)
        env = self._build_env(manifest, None)

        # 处理 shell 语法（cd <path> &&）
        cwd = manifest.source_path or None
        cmd = self._strip_shell_prefix(raw_cmd)
        extracted_cwd = self._extract_cd_path(raw_cmd)
        if extracted_cwd:
            cwd = extracted_cwd

        logger.info("启动 Agent: %s cmd=%s cwd=%s", name, " ".join(cmd), cwd)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=cwd,
            )

            info = self.registry.register(name, process, manifest)

            # 启动后台任务监控进程 — 存储引用防止异常静默丢失
            task = asyncio.create_task(
                self._monitor_agent_process(name, on_stdout),
                name=f"monitor-{name}",
            )
            self.registry._monitor_tasks[name] = task

            # 等待短暂时间检查进程是否存活
            await asyncio.sleep(0.5)
            if process.returncode is not None:
                info.status = "failed"
                stderr_data = await process.stderr.read() if process.stderr else b""
                logger.error(
                    "Agent %s 启动后立即退出 (exit=%d): %s",
                    name, process.returncode, stderr_data.decode(errors="replace")[:200],
                )
                return info

            info.status = "running"
            info.desired_state = "running"  # Watchdog: 标记用户期望此 Agent 保持运行
            info.restart_count = 0           # 手动启动 → 重置重启计数
            info.last_restart_time = time.monotonic()
            # 保存 stdout 回调，供 Watchdog 自动重启时复用
            self._watchdog_stdout_cb = on_stdout
            # 同步 PidFileStore — 确保 agent-hub stop 能发现此进程
            try:
                from agent_hub.pid_store import PidFileStore
                PidFileStore().save(name, process.pid, manifest.protocol)
            except Exception:
                pass
            logger.info("Agent %s 启动成功 (pid=%d)", name, process.pid)
            return info

        except Exception as e:
            logger.error("启动 Agent %s 失败: %s", name, e)
            info = ProcessInfo(
                name=name,
                manifest=manifest,
                status="failed",
                started_at=time.monotonic(),
            )
            self.registry._processes[name] = info
            return info

    def _build_daemon_command(self, manifest: AgentManifest) -> list[str]:
        """构建 Agent 守护进程的启动命令。

        注意：不是所有 Agent 都支持守护模式。当前策略：
        1. 如果 command 模板不含占位符 → 直接使用
        2. 如果含 {mode} → 替换为 daemon 再试
        3. 回退 → 提取基础命令（去掉占位符部分），尽量让进程跑起来
        """
        template = manifest.interface.command

        # 策略 1：无占位符则直接使用
        if "{" not in template:
            try:
                return shlex.split(template)
            except ValueError:
                return template.split()

        # 策略 2：尝试守护模式替换
        daemon_cmd = template
        if "{mode}" in template:
            daemon_cmd = daemon_cmd.replace("{mode}", "daemon")
        if "{goal}" in template:
            daemon_cmd = daemon_cmd.replace("{goal}", "serve")
        if "{task}" in template:
            daemon_cmd = daemon_cmd.replace("{task}", "serve")
        if "{project}" in template:
            daemon_cmd = daemon_cmd.replace("{project}", "")

        # 策略 3：清理残留占位符并尝试
        daemon_cmd = re.sub(r"\{[^}]*\}", "", daemon_cmd).strip()

        if daemon_cmd:
            try:
                return shlex.split(daemon_cmd)
            except ValueError:
                return daemon_cmd.split()

        # 完全不支持的模板 — 至少返回可执行文件名
        try:
            first = shlex.split(template)[0]
            return [first]
        except Exception:
            return ["echo", f"Agent {manifest.name} daemon command not configured"]

    async def _monitor_agent_process(
        self,
        name: str,
        on_stdout: Callable[[str], None] | None = None,
    ) -> None:
        """后台监控 Agent 进程的 stdout/stderr 和退出状态。

        异常安全：内部捕获所有异常，确保监控任务不会静默失败。
        """
        info = self.registry.get(name)
        if not info or not info.process:
            return

        process = info.process

        async def read_and_callback(
            stream: asyncio.StreamReader | None,
            callback: Callable[[str], None] | None,
        ) -> None:
            if stream is None or callback is None:
                # 静默消费输出流，防止子进程管道阻塞
                if stream:
                    try:
                        while True:
                            line = await stream.readline()
                            if not line:
                                break
                    except Exception:
                        pass  # 管道关闭等异常，安全忽略
                return
            try:
                while True:
                    line_bytes = await stream.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
                    try:
                        callback(line)
                    except Exception:
                        pass  # 回调异常不中断流读取
            except Exception:
                pass  # 管道关闭等异常

        stdout_task = asyncio.create_task(
            read_and_callback(process.stdout, on_stdout)
        )
        stderr_task = asyncio.create_task(
            read_and_callback(process.stderr, None)  # stderr 静默消费
        )

        try:
            # 等待进程退出
            await process.wait()
        except Exception:
            logger.warning("Agent %s 进程等待异常", name, exc_info=True)

        # 等待流读取任务完成（避免资源泄漏）
        try:
            await asyncio.gather(stdout_task, stderr_task)
        except Exception:
            pass

        if process.returncode != 0:
            logger.warning(
                "Agent %s 退出 (exit=%d)", name, process.returncode,
            )
        self.registry.update_status(name, "stopped")

    async def stop_agent(self, name: str) -> bool:
        """优雅终止指定 Agent。

        先发 SIGTERM，等待 5 秒，然后 SIGKILL。
        """
        info = self.registry.get(name)
        if not info or not info.process:
            logger.info("Agent %s 未在运行，跳过停止", name)
            return True

        process = info.process
        self.registry.update_status(name, "stopping")

        try:
            # SIGTERM（优雅终止）
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                # 强制终止
                logger.warning("Agent %s 未响应 SIGTERM，发送 SIGKILL", name)
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=3)
                except Exception:
                    pass

            self.registry.update_status(name, "stopped")
            if info:
                info.desired_state = "stopped"  # Watchdog: 用户主动停止，不再自动重启
                info.restart_count = 0
            self.registry.remove(name)
            # 同步 PidFileStore — 确保 agent-hub stop 也能清理此进程
            try:
                from agent_hub.pid_store import PidFileStore
                PidFileStore().remove(name)
            except Exception:
                pass
            logger.info("Agent %s 已停止", name)
            return True

        except Exception as e:
            logger.error("停止 Agent %s 失败: %s", name, e)
            self.registry.update_status(name, "failed")
            return False

    async def health_check(self, name: str) -> bool:
        """检查 Agent 进程是否健康运行。"""
        info = self.registry.get(name)
        if not info or not info.process:
            return False

        process = info.process

        # 检查进程是否已退出
        if process.returncode is not None:
            self.registry.update_status(name, "stopped")
            return False

        # 进程仍在运行
        return True

    async def start_all(
        self,
        manifests: list[AgentManifest],
        *,
        on_stdout: Callable[[str], None] | None = None,
    ) -> dict[str, ProcessInfo]:
        """启动所有注册的 Agent。

        Returns:
            {agent_name: ProcessInfo} 启动结果
        """
        results: dict[str, ProcessInfo] = {}
        # 串行启动（避免资源竞争）
        for manifest in manifests:
            try:
                info = await self.start_agent(manifest, on_stdout=on_stdout)
                results[manifest.name] = info
            except Exception as e:
                logger.error("启动 %s 失败: %s", manifest.name, e)
                results[manifest.name] = ProcessInfo(
                    name=manifest.name,
                    manifest=manifest,
                    status="failed",
                )
        return results

    async def stop_all(self) -> None:
        """停止所有 Agent。"""
        names = [p.name for p in self.registry.list_all()]
        # 并行停止
        tasks = [self.stop_agent(name) for name in names]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── Watchdog — Agent 健康自愈 ──────────────────────────────────

    def start_watchdog(self, interval: int = 15) -> None:
        """启动后台 Watchdog 任务（自动重启异常退出的 Agent）。

        仅启动一次：若已有 Watchdog 在运行则跳过。
        """
        existing = getattr(self, "_watchdog_task", None)
        if existing and not existing.done():
            logger.debug("Watchdog 已在运行，跳过重复启动")
            return

        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(interval),
            name="agent-watchdog",
        )
        logger.info("Watchdog 已启动 (间隔=%ds)", interval)

    async def _watchdog_loop(self, interval: int = 15) -> None:
        """Watchdog 主循环 — 定期检查并自动重启异常退出的 Agent。

        每 `interval` 秒扫描一次注册表：
        - desired_state == "running" 但 status 为 stopped/failed → 自动重启
        - 5 分钟内最多重启 MAX_RESTARTS 次 → 超过则标记 failed 并停止尝试
        """
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("Watchdog 已停止")
                return

            for name, info in list(self.registry._processes.items()):
                # 仅处理用户期望运行但状态异常的 Agent
                if info.desired_state != "running":
                    continue
                if info.status in ("running", "starting"):
                    continue  # 健康运行中

                # 需要重启 — 检查是否达到限制
                if not info.can_restart:
                    if info.restart_count >= info.MAX_RESTARTS:
                        logger.error(
                            "Agent '%s' 在 %.0fs 内崩溃 %d 次（上限 %d），"
                            "已停止自动重启。请手动检查: agent-hub agent status",
                            name,
                            info.RESTART_WINDOW,
                            info.restart_count,
                            info.MAX_RESTARTS,
                        )
                        info.status = "failed"
                    continue

                # 执行自动重启
                logger.warning(
                    "Agent '%s' 状态=%s，尝试自动重启 (第 %d/%d 次)...",
                    name, info.status, info.restart_count + 1, info.MAX_RESTARTS,
                )

                try:
                    # 清理旧进程引用
                    if info.process and info.process.returncode is None:
                        try:
                            info.process.kill()
                        except Exception:
                            pass

                    # 重用 start_agent 逻辑
                    new_info = await self.start_agent(
                        info.manifest,
                        on_stdout=getattr(self, "_watchdog_stdout_cb", None),
                    )

                    if new_info.status == "running":
                        # 更新重启统计
                        now = time.monotonic()
                        if now - info.last_restart_time > ProcessInfo.RESTART_WINDOW:
                            info.restart_count = 0  # 窗口外重置
                        info.restart_count += 1
                        info.last_restart_time = now
                        info.status = "running"
                        info.process = new_info.process
                        info.pid = new_info.pid
                        info.started_at = new_info.started_at
                        logger.info(
                            "Agent '%s' 自动重启成功 (pid=%d, 重启计数=%d)",
                            name, info.pid, info.restart_count,
                        )
                    else:
                        logger.error("Agent '%s' 自动重启失败: %s", name, new_info.status)

                except Exception as e:
                    logger.error("Agent '%s' 自动重启异常: %s", name, e, exc_info=True)
