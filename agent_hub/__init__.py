"""Agent Hub — 解耦的多 Agent 调度系统。

通过 Agent Manifest（agent.yaml）发现和路由任务到专业 Agent，
支持 DAG 并行调度、Rich TUI 可视化仪表盘。
"""

import os as _os
import sys as _sys

__version__ = "0.1.0"


def ensure_utf8() -> None:
    """确保 Windows 控制台使用 UTF-8 编码（解决中文乱码和 emoji 显示问题）。

    应在所有 agent_hub 入口模块的顶部调用一次。
    幂等 — 重复调用不会产生副作用。
    """
    if _sys.platform == "win32":
        _os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        _os.environ.setdefault("PYTHONUTF8", "1")
        try:
            _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
