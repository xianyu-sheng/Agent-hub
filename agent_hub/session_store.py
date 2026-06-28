"""Session Store — 跨轮次会话记忆。

Agent Hub 调度系统默认是无状态的（每次 run 是独立调用）。
SessionStore 提供轻量级会话持久化，使 Router 和 Scheduler 能够感知
"之前做了什么"，从而实现指代消解（"继续"、"再"、"也"）和上下文路由。

设计要点：
- 每轮保存一个 JSON 文件，按时间戳命名
- get_recent_context() 生成压缩上下文供 LLM prompt 注入
- 自动清理最旧的文件（保留最近 50 轮）
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────

MAX_SESSION_FILES = 50          # 最多保留的会话文件数
CONTEXT_MAX_CHARS = 1200        # get_recent_context 的字符上限
RESULT_TRUNCATE_CHARS = 200     # 每条结果截断到此长度


# ── 数据模型 ────────────────────────────────────────────────────────


@dataclass
class SessionRecord:
    """单轮调度会话的完整记录。

    Attributes:
        session_id: 唯一会话 ID（UUID）
        user_input: 用户原始输入
        route_plan: RoutePlan.to_dict() 序列化结果
        task_results: 精简版执行结果列表 [{agent, task, success, output, duration_ms}]
        aggregate: LLM 汇总结果（截断至 500 字）
        timestamp: ISO 8601 时间戳
        duration_ms: 总执行耗时
    """

    session_id: str
    user_input: str
    route_plan: dict = field(default_factory=dict)
    task_results: list[dict] = field(default_factory=list)
    aggregate: str = ""
    timestamp: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_input": self.user_input,
            "route_plan": self.route_plan,
            "task_results": self.task_results,
            "aggregate": self.aggregate[:500],  # 截断
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        return cls(
            session_id=str(data.get("session_id", "")),
            user_input=str(data.get("user_input", "")),
            route_plan=data.get("route_plan", {}),
            task_results=data.get("task_results", []),
            aggregate=str(data.get("aggregate", "")),
            timestamp=str(data.get("timestamp", "")),
            duration_ms=float(data.get("duration_ms", 0)),
        )

    @property
    def summary(self) -> str:
        """单行摘要 — 用于 CLI 展示。"""
        agents = ", ".join(
            sorted(set(r.get("agent", "?") for r in self.task_results))
        )
        success = sum(1 for r in self.task_results if r.get("success"))
        total = len(self.task_results)
        return (
            f"[{self.timestamp[:19]}] {self.user_input[:60]} → "
            f"{agents} ({success}/{total} 成功)"
        )


# ── Session 存储 ────────────────────────────────────────────────────


class SessionStore:
    """会话持久化存储。

    JSON 文件存储在 {store_dir}/ 目录下，文件名格式:
        {timestamp_iso}_{session_id[:8]}.json

    使用方式：
        store = SessionStore()
        store.save(record)
        context = store.get_recent_context(5)  # 最近 5 轮的压缩文本
    """

    def __init__(self, store_dir: str | Path | None = None) -> None:
        if store_dir is None:
            store_dir = Path(__file__).parent.parent / ".agent_hub" / "sessions"
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────

    def save(self, record: SessionRecord) -> Path:
        """保存一轮会话记录到 JSON 文件。"""
        # 生成安全时间戳
        ts = record.timestamp or datetime.now(timezone.utc).isoformat()
        safe_ts = ts.replace(":", "-").replace(" ", "_")[:19]
        filename = f"{safe_ts}_{record.session_id[:8]}.json"
        file_path = self.store_dir / filename

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2, ensure_ascii=False)

        logger.debug("会话已保存: %s (%s)", record.session_id[:8], file_path)

        # 自动清理最旧的文件
        self._prune()
        return file_path

    def load(self, session_id: str) -> SessionRecord | None:
        """按 session_id 前缀查找并加载会话记录。"""
        for f in sorted(self.store_dir.glob("*.json"), reverse=True):
            if session_id[:8] in f.name:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    return SessionRecord.from_dict(data)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("解析会话文件 %s 失败: %s", f.name, e)
        return None

    def list_sessions(self, limit: int = 20) -> list[SessionRecord]:
        """列出最近 N 轮会话（按时间倒序）。"""
        records: list[SessionRecord] = []
        for f in sorted(self.store_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                records.append(SessionRecord.from_dict(data))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("解析会话文件 %s 失败: %s", f.name, e)
        return records

    def get_recent_context(self, n: int = 5) -> str:
        """生成最近 N 轮的压缩上下文文本，供 LLM prompt 注入。

        返回格式：
            [1] 用户: "列出所有 agent"
                路由: agent-hub → 成功
                结果: 发现 4 个 Agent: omniagent, resume-sync, ...
            [2] 用户: "分析 omniagent 代码质量"
                路由: smartbench → diagnose_code → ✅
                结果: 代码质量评分 82/100...

        如果历史中存在失败的任务，会在对应条目中标注 ❌。
        """
        records = self.list_sessions(limit=n)
        if not records:
            return ""

        lines: list[str] = []
        total_chars = 0

        for i, r in enumerate(reversed(records), 1):
            agents = ", ".join(
                sorted(set(t.get("agent", "?") for t in r.task_results))
            )
            success_count = sum(1 for t in r.task_results if t.get("success"))
            fail_count = len(r.task_results) - success_count
            status = f"✅ {success_count}/{len(r.task_results)}" if not fail_count else f"⚠️ {success_count}/{len(r.task_results)} (❌ {fail_count})"

            # 汇总截断
            agg = r.aggregate[:RESULT_TRUNCATE_CHARS].replace("\n", " ")
            if len(r.aggregate) > RESULT_TRUNCATE_CHARS:
                agg += "..."

            entry = (
                f"[{i}] 用户: \"{r.user_input[:100]}\"\n"
                f"    路由: {agents} → {status}\n"
                f"    结果: {agg}"
            )

            if total_chars + len(entry) > CONTEXT_MAX_CHARS:
                lines.append(f"...（省略更早的 {len(records) - i + 1} 轮）")
                break

            lines.append(entry)
            total_chars += len(entry)

        return "\n".join(reversed(lines)) if lines else ""

    def _prune(self) -> None:
        """清理最旧的会话文件，保持总数不超过 MAX_SESSION_FILES。"""
        files = sorted(self.store_dir.glob("*.json"))
        if len(files) <= MAX_SESSION_FILES:
            return

        excess = len(files) - MAX_SESSION_FILES
        for f in files[:excess]:
            try:
                f.unlink()
                logger.debug("清理旧会话: %s", f.name)
            except OSError:
                pass


# ── 便捷函数 ────────────────────────────────────────────────────────


def new_session_record(
    user_input: str,
    route_plan: dict,
    task_results: list[dict],
    aggregate: str,
    duration_ms: float,
) -> SessionRecord:
    """辅助函数：创建会话记录。"""
    return SessionRecord(
        session_id=uuid.uuid4().hex,
        user_input=user_input,
        route_plan=route_plan,
        task_results=task_results,
        aggregate=aggregate,
        timestamp=datetime.now(timezone.utc).isoformat(),
        duration_ms=duration_ms,
    )
