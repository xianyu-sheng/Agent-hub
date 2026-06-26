"""PID 文件存储 — 跨 CLI 调用的进程状态持久化。

问题：agent-hub start 和 agent-hub stop 是两次独立的 CLI 调用，
每次创建新的 CLIBridge 实例，进程注册表是空的，stop 无法找到任何进程。

方案：将 Agent 进程的 PID 持久化到 JSON 文件。
start 命令写入 PID → stop 命令读取并终止进程 → 清理文件。

使用 taskkill (Windows) / os.kill (Unix) 来终止持久化存储中的进程。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StoredProcess:
    """持久化存储中的进程记录。"""

    name: str
    pid: int
    started_at: float
    protocol: str = "cli"


class PidFileStore:
    """跨 CLI 调用的进程存储。

    使用 JSON 文件持久化 Agent 进程的 PID，
    使 agent-hub start 和 agent-hub stop（不同进程）能够共享状态。
    """

    # 默认存储路径：Agent-hub 项目根目录下的 .agent_hub/
    _DEFAULT_DIR: Path = Path(__file__).parent.parent / ".agent_hub"

    def __init__(self, store_dir: str | Path | None = None) -> None:
        self.store_dir = Path(store_dir or self._DEFAULT_DIR)
        self._store_path = self.store_dir / "process_registry.json"

    # ── 读写 ──────────────────────────────────────────────────────

    def save(self, name: str, pid: int, protocol: str = "cli") -> None:
        """保存进程 PID 到持久化存储。

        Args:
            name: Agent 名称
            pid: 进程 PID
            protocol: Agent 协议（cli/mcp/http/internal）
        """
        self.store_dir.mkdir(parents=True, exist_ok=True)
        registry = self._load_raw()
        registry[name] = {
            "name": name,
            "pid": pid,
            "started_at": time.time(),
            "protocol": protocol,
        }
        self._write_raw(registry)
        logger.info("PID 已保存: %s (pid=%d)", name, pid)

    def remove(self, name: str) -> None:
        """从持久化存储中移除进程记录。

        Args:
            name: Agent 名称
        """
        registry = self._load_raw()
        if name in registry:
            registry.pop(name)
            self._write_raw(registry)
            logger.info("PID 已移除: %s", name)

    def load_all(self) -> dict[str, StoredProcess]:
        """加载所有持久化的进程记录。

        Returns:
            {agent_name: StoredProcess} 字典
        """
        raw = self._load_raw()
        result: dict[str, StoredProcess] = {}
        for name, data in raw.items():
            result[name] = StoredProcess(
                name=data.get("name", name),
                pid=int(data.get("pid", 0)),
                started_at=float(data.get("started_at", 0)),
                protocol=str(data.get("protocol", "cli")),
            )
        return result

    # ── 进程终止 ──────────────────────────────────────────────────

    async def stop_all(self, grace_period: int = 5) -> int:
        """终止所有持久化存储中的 Agent 进程。

        使用 taskkill (Windows) 或 SIGTERM (Unix) 终止进程，
        等待 grace_period 秒后强制终止未响应的进程。

        Args:
            grace_period: 优雅终止等待秒数（仅 Unix 有效）

        Returns:
            成功终止的进程数
        """
        processes = self.load_all()
        if not processes:
            logger.info("没有需要停止的进程")
            return 0

        stopped = 0

        if sys.platform == "win32":
            # Windows: 使用 taskkill
            for name, proc in processes.items():
                try:
                    # /F: 强制终止, /T: 终止子进程树
                    result = subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                        capture_output=True, timeout=10,
                    )
                    if result.returncode == 0:
                        logger.info("已终止: %s (pid=%d)", name, proc.pid)
                        stopped += 1
                    else:
                        stderr = result.stderr.decode(errors="replace").strip()
                        if "not found" in stderr.lower() or "不存在" in stderr:
                            # 进程已经不存在了
                            logger.info("进程已不存在: %s (pid=%d)", name, proc.pid)
                            stopped += 1
                        else:
                            logger.warning("终止 %s (pid=%d) 失败: %s", name, proc.pid, stderr)
                except subprocess.TimeoutExpired:
                    logger.warning("终止 %s (pid=%d) 超时", name, proc.pid)
                except Exception as e:
                    logger.warning("终止 %s (pid=%d) 异常: %s", name, proc.pid, e)
        else:
            # Unix: 使用 os.kill
            for name, proc in processes.items():
                try:
                    os.kill(proc.pid, signal.SIGTERM)
                    # 等待优雅终止
                    for _ in range(grace_period * 2):
                        try:
                            os.kill(proc.pid, 0)  # 检查是否还活着
                            await asyncio.sleep(0.5)
                        except OSError:
                            break  # 进程已退出
                    else:
                        # 仍未退出，强制终止
                        try:
                            os.kill(proc.pid, signal.SIGKILL)
                        except OSError:
                            pass
                    stopped += 1
                    logger.info("已终止: %s (pid=%d)", name, proc.pid)
                except OSError:
                    # 进程已不存在
                    logger.info("进程已不存在: %s (pid=%d)", name, proc.pid)
                    stopped += 1
                except Exception as e:
                    logger.warning("终止 %s (pid=%d) 异常: %s", name, proc.pid, e)

        # 清理存储文件
        self._clear()
        return stopped

    def _clear(self) -> None:
        """清除持久化存储文件。"""
        try:
            self._store_path.unlink(missing_ok=True)
        except Exception:
            pass

    # ── 内部方法 ──────────────────────────────────────────────────

    def _load_raw(self) -> dict[str, Any]:
        """从 JSON 文件加载原始数据。"""
        if not self._store_path.exists():
            return {}
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.warning("读取进程注册表失败，将重建", exc_info=True)
            return {}

    def _write_raw(self, registry: dict[str, Any]) -> None:
        """将原始数据写入 JSON 文件。"""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._store_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
