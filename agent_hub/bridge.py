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

    @property
    def is_running(self) -> bool:
        return self.status == "running" and self.process is not None

    @property
    def uptime_seconds(self) -> float:
        if self.started_at <= 0:
            return 0.0
        return time.monotonic() - self.started_at


# ── Agent 进程注册表 ────────────────────────────────────────────────


class AgentProcessRegistry:
    """管理所有 Agent 子进程的生命周期。

    提供类似 systemd 的 start/stop/status 接口。
    """

    def __init__(self) -> None:
        self._processes: dict[str, ProcessInfo] = {}

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
        """从注册表移除 Agent。"""
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

        # 检查 manifest 约束中的 timeout
        if "timeout" in manifest.capabilities.constraints:
            timeout = min(timeout, manifest.capabilities.constraints["timeout"])

        # 构建命令
        cmd = self._build_command(manifest, task_name, params)
        env = self._build_env(manifest, extra_env)

        logger.info(
            "执行 Agent: %s task=%s timeout=%ds cmd=%s",
            manifest.name, task_name, timeout, " ".join(cmd),
        )

        start_time = time.monotonic()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            # 启动子进程
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=manifest.source_path or None,
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
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

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

    def _build_command(
        self,
        manifest: AgentManifest,
        task_name: str,
        params: dict[str, Any],
    ) -> list[str]:
        """根据 manifest 的 interface.command 模板构建 CLI 命令。

        支持的占位符：
        - {task} → 任务名
        - {goal} → params["goal"]
        - {mode} → params["mode"] 或 "react"
        - {project} → params["project"] 或 ""
        - {params_json} → 完整 params 的 JSON 字符串
        """
        template = manifest.interface.command
        goal = str(params.get("goal", params.get("task_description", "")))
        mode = str(params.get("mode", "react"))
        project = str(params.get("project", params.get("project_path", "")))

        command_str = template.replace("{task}", task_name)
        command_str = command_str.replace("{goal}", goal)
        command_str = command_str.replace("{mode}", mode)
        command_str = command_str.replace("{project}", project)
        command_str = command_str.replace(
            "{params_json}", json.dumps(params, ensure_ascii=False)
        )

        # 简单的 shell 分词（支持引号）
        import shlex
        try:
            return shlex.split(command_str)
        except ValueError:
            # 回退：按空格分割
            return command_str.split()

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
        # 对于 CLI Agent，使用"守护模式"——持续运行的 REPL
        cmd = self._build_daemon_command(manifest)
        env = self._build_env(manifest, None)

        logger.info("启动 Agent: %s cmd=%s", name, " ".join(cmd))

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=manifest.source_path or None,
            )

            info = self.registry.register(name, process, manifest)

            # 启动后台任务监控进程
            asyncio.create_task(self._monitor_agent_process(name, on_stdout))

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

        对于 CLI Agent，使用 --daemon 或 --serve 标志。
        如果没有守护模式，则启动一个交互式 REPL。
        """
        template = manifest.interface.command
        # 尝试替换为守护模式
        # 常见模式：--mode {mode} → --mode daemon
        daemon_cmd = template.replace("{mode}", "daemon")
        daemon_cmd = daemon_cmd.replace("{goal}", "serve")
        daemon_cmd = daemon_cmd.replace("{task}", "serve")

        import shlex
        try:
            return shlex.split(daemon_cmd)
        except ValueError:
            return daemon_cmd.split()

    async def _monitor_agent_process(
        self,
        name: str,
        on_stdout: Callable[[str], None] | None = None,
    ) -> None:
        """后台监控 Agent 进程的 stdout/stderr 和退出状态。"""
        info = self.registry.get(name)
        if not info or not info.process:
            return

        process = info.process

        async def read_and_callback(
            stream: asyncio.StreamReader | None,
            callback: Callable[[str], None] | None,
        ) -> None:
            if stream is None or callback is None:
                # 静默消费
                if stream:
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                return
            while True:
                line_bytes = await stream.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
                callback(line)

        stdout_task = asyncio.create_task(
            read_and_callback(process.stdout, on_stdout)
        )
        stderr_task = asyncio.create_task(
            read_and_callback(process.stderr, None)  # stderr 记录到日志
        )

        # 等待进程退出
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)

        if process.returncode != 0:
            logger.warning(
                "Agent %s 退出 (exit=%d)", name, process.returncode,
            )
            self.registry.update_status(name, "stopped")
        else:
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
            self.registry.remove(name)
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
