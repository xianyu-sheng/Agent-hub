"""Cron Scheduler — 轻量级定时任务调度。

纯 Python 实现的 5 字段 cron 解析器与后台调度循环。
不依赖 croniter 等外部库 — 字段匹配逻辑自包含。

支持的标准 cron 语法：
- 精确值: "0", "9", "30"
- 通配: "*"
- 步进: "*/5", "*/15"
- 列表: "0,30"
- 范围: "1-5" (星期几常用)

存储：定时任务配置保存在 .agent_hub/cron_jobs.json
执行历史：保存在 .agent_hub/cron_history.json (最近 100 条)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────

MAX_HISTORY_ENTRIES = 100  # cron_history 最多保留条数
MATCH_TOLERANCE_SEC = 65   # 时间匹配容忍度（秒），防止恰好错过


# ── Cron 表达式解析 ────────────────────────────────────────────────


def _match_field(value: str, current: int) -> bool:
    """检查单个 cron 字段是否匹配当前时间值。

    Args:
        value: cron 字段值（如 "*/5", "0,30", "1-5", "*", "9"）
        current: 当前时间对应字段的值（0-59 或 0-23 等）

    Returns:
        True 如果匹配
    """
    if value == "*":
        return True

    # 逗号分隔的列表: "0,30"
    if "," in value:
        return any(_match_field(part.strip(), current) for part in value.split(","))

    # 步进: "*/5"
    if value.startswith("*/"):
        try:
            step = int(value[2:])
            return step > 0 and current % step == 0
        except (ValueError, TypeError):
            return False

    # 范围: "1-5"
    if "-" in value:
        try:
            lo, hi = value.split("-", 1)
            return int(lo) <= current <= int(hi)
        except (ValueError, TypeError):
            return False

    # 精确值
    try:
        return int(value) == current
    except (ValueError, TypeError):
        return False


def cron_matches(cron_expr: str, dt: datetime | None = None) -> bool:
    """检查给定 cron 表达式是否匹配当前（或指定）时间。

    Args:
        cron_expr: 5 字段 cron 表达式（"min hour dom month dow"）
        dt: 要检查的时间（默认当前 UTC）

    Returns:
        True 如果匹配

    Raises:
        ValueError: 表达式字段数不为 5
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron 表达式必须是 5 字段 (min hour dom month dow)，"
            f"实际得到 {len(parts)} 字段: '{cron_expr}'"
        )

    minute_field, hour_field, dom_field, month_field, dow_field = parts

    # 星期几：Python weekday() 返回 0=Monday, 而 cron 的 dow 是 0=Sunday
    # 这里使用 ISO 映射：1=Monday...7=Sunday
    iso_dow = dt.isoweekday()  # 1-7 (Mon-Sun)
    # 也支持 0=Sunday 的 cron 习惯
    cron_dow = 7 if iso_dow == 7 else iso_dow  # Sunday = 7 (or 0)

    return (
        _match_field(minute_field, dt.minute)
        and _match_field(hour_field, dt.hour)
        and _match_field(dom_field, dt.day)
        and _match_field(month_field, dt.month)
        and (
            _match_field(dow_field, cron_dow)
            or _match_field(dow_field, 0 if iso_dow == 7 else iso_dow)  # 也尝试 0=Sunday
        )
    )


# ── 数据模型 ────────────────────────────────────────────────────────


@dataclass
class CronJob:
    """一个定时任务。

    Attributes:
        name: 任务唯一名（用于增删改查）
        cron: 5 字段 cron 表达式
        agent: 目标 Agent 名
        task: 要执行的任务名
        params: 任务参数
        enabled: 是否启用
        last_run: 上次执行时间（ISO）
        created_at: 创建时间（ISO）
    """

    name: str
    cron: str
    agent: str
    task: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    last_run: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "cron": self.cron,
            "agent": self.agent,
            "task": self.task,
        }
        if self.params:
            d["params"] = self.params
        if not self.enabled:
            d["enabled"] = False
        if self.last_run:
            d["last_run"] = self.last_run
        if self.created_at:
            d["created_at"] = self.created_at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronJob:
        return cls(
            name=str(data.get("name", "")),
            cron=str(data.get("cron", "")),
            agent=str(data.get("agent", "")),
            task=str(data.get("task", "")),
            params=data.get("params", {}),
            enabled=bool(data.get("enabled", True)),
            last_run=str(data.get("last_run", "")),
            created_at=str(data.get("created_at", "")),
        )


@dataclass
class CronRunRecord:
    """定时任务执行记录。"""

    job_name: str
    agent: str
    task: str
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0
    timestamp: str = ""


# ── Cron 调度器 ────────────────────────────────────────────────────


class CronScheduler:
    """定时任务调度器。

    管理与持久化 cron jobs，提供后台循环检查并触发到期任务。

    使用方式：
        cs = CronScheduler()
        cs.add_job(CronJob(name="daily_check", cron="0 9 * * 1-5", ...))
        await cs.start_loop(execute_callback)
    """

    def __init__(
        self,
        jobs_path: str | Path | None = None,
        history_path: str | Path | None = None,
    ) -> None:
        if jobs_path is None:
            jobs_path = Path(__file__).parent.parent / ".agent_hub" / "cron_jobs.json"
        if history_path is None:
            history_path = Path(__file__).parent.parent / ".agent_hub" / "cron_history.json"

        self._jobs_path = Path(jobs_path)
        self._history_path = Path(history_path)
        self._jobs: dict[str, CronJob] = {}
        self._history: list[CronRunRecord] = []
        self._loop_task: asyncio.Task | None = None
        self._execute_callback: Any = None  # async callable(job) -> CronRunRecord

        self._load()

    @property
    def data_dir(self) -> str:
        """cron 数据目录（存放 jobs.json、history.json、哨兵文件）。"""
        return str(self._jobs_path.parent)

    def request_stop(self) -> None:
        """通过哨兵文件请求正在运行的 cron 循环优雅停止。

        即使调用进程与运行 cron 的进程不同，此方法也能工作。
        """
        stop_file = os.path.join(self.data_dir, ".cron_stop")
        Path(stop_file).touch()
        logger.info("已创建停止哨兵文件: %s", stop_file)

    # ── 持久化 ─────────────────────────────────────────────────

    def _load(self) -> None:
        """从 JSON 文件加载定时任务与历史。"""
        # 加载任务
        if self._jobs_path.exists():
            try:
                data = json.loads(self._jobs_path.read_text(encoding="utf-8"))
                for entry in (data if isinstance(data, list) else data.get("jobs", [])):
                    job = CronJob.from_dict(entry)
                    if job.name:
                        self._jobs[job.name] = job
                logger.info("加载 %d 个定时任务", len(self._jobs))
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("加载 cron_jobs.json 失败: %s", e)

        # 加载历史
        if self._history_path.exists():
            try:
                data = json.loads(self._history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._history = [
                        CronRunRecord(
                            job_name=r.get("job_name", ""),
                            agent=r.get("agent", ""),
                            task=r.get("task", ""),
                            success=r.get("success", False),
                            output=r.get("output", ""),
                            error=r.get("error", ""),
                            duration_ms=r.get("duration_ms", 0),
                            timestamp=r.get("timestamp", ""),
                        )
                        for r in data
                    ]
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("加载 cron_history.json 失败: %s", e)

    def _save_jobs(self) -> None:
        """持久化任务列表。"""
        self._jobs_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"jobs": [j.to_dict() for j in self._jobs.values()]}
        self._jobs_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_history(self) -> None:
        """持久化执行历史。"""
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "job_name": r.job_name,
                "agent": r.agent,
                "task": r.task,
                "success": r.success,
                "output": r.output[:500],
                "error": r.error[:500],
                "duration_ms": r.duration_ms,
                "timestamp": r.timestamp,
            }
            for r in self._history[-MAX_HISTORY_ENTRIES:]
        ]
        self._history_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── CRUD ────────────────────────────────────────────────────

    def add_job(self, job: CronJob) -> None:
        """添加定时任务。"""
        if not job.name:
            raise ValueError("定时任务 name 不能为空")
        if not job.cron:
            raise ValueError("定时任务 cron 表达式不能为空")
        if not job.agent:
            raise ValueError("定时任务 agent 不能为空")

        # 验证 cron 表达式
        cron_matches(job.cron)  # 会抛 ValueError 如果格式不对

        if not job.created_at:
            job.created_at = datetime.now(timezone.utc).isoformat()

        self._jobs[job.name] = job
        self._save_jobs()
        logger.info("添加定时任务: %s (cron=%s)", job.name, job.cron)

    def remove_job(self, name: str) -> CronJob | None:
        """删除定时任务。"""
        job = self._jobs.pop(name, None)
        if job:
            self._save_jobs()
            logger.info("删除定时任务: %s", name)
        return job

    def get_job(self, name: str) -> CronJob | None:
        """获取指定定时任务。"""
        return self._jobs.get(name)

    def list_jobs(self) -> list[CronJob]:
        """列出所有定时任务。"""
        return list(self._jobs.values())

    def history(self, limit: int = 20) -> list[CronRunRecord]:
        """获取最近的执行历史（倒序）。"""
        return list(reversed(self._history[-limit:]))

    # ── 调度循环 ──────────────────────────────────────────────

    def get_due_jobs(self) -> list[CronJob]:
        """返回当前时间应触发的任务（首次运行或距上次超过 60s）。"""
        now = datetime.now(timezone.utc)
        due: list[CronJob] = []

        for job in self._jobs.values():
            if not job.enabled:
                continue

            # 检查 cron 是否匹配
            if not cron_matches(job.cron, now):
                continue

            # 防止同一分钟重复触发：last_run 必须在 60s 之前
            if job.last_run:
                try:
                    last = datetime.fromisoformat(job.last_run)
                    if (now - last).total_seconds() < MATCH_TOLERANCE_SEC:
                        continue
                except (ValueError, TypeError):
                    pass

            due.append(job)

        return due

    async def start_loop(
        self,
        execute_callback: Any,
        interval: int = 30,
    ) -> None:
        """启动后台 cron 循环。

        Args:
            execute_callback: async callable(job: CronJob) -> CronRunRecord
            interval: 检查间隔（秒）
        """
        if self._loop_task and not self._loop_task.done():
            logger.debug("Cron 循环已在运行")
            return

        self._execute_callback = execute_callback
        self._loop_task = asyncio.create_task(
            self._cron_loop(interval),
            name="cron-scheduler",
        )
        logger.info("Cron 调度器已启动 (间隔=%ds, %d 个任务)", interval, len(self._jobs))

    async def _cron_loop(self, interval: int = 30) -> None:
        """Cron 主循环。每 interval 秒检查一次到期任务。

        支持优雅停止：
        - 收到 asyncio.CancelledError → 退出
        - 检测到 {data_dir}/.cron_stop 哨兵文件 → 退出并清理文件
        """
        _stop_file = os.path.join(self.data_dir, ".cron_stop")
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("Cron 调度器已停止")
                return

            # 检查停止哨兵文件
            if os.path.exists(_stop_file):
                logger.info("检测到停止哨兵文件，Cron 调度器正在退出...")
                try:
                    os.remove(_stop_file)
                except OSError:
                    pass
                return

            now = datetime.now(timezone.utc)
            for job in self.get_due_jobs():
                logger.info("触发定时任务: %s (%s)", job.name, job.cron)
                try:
                    if self._execute_callback:
                        record = await self._execute_callback(job)
                    else:
                        record = CronRunRecord(
                            job_name=job.name,
                            agent=job.agent,
                            task=job.task,
                            success=False,
                            error="未设置 execute_callback",
                            timestamp=now.isoformat(),
                        )

                    # 记录历史
                    self._history.append(record)
                    self._save_history()

                    # 更新 last_run
                    job.last_run = now.isoformat()
                    self._save_jobs()

                    icon = "✅" if record.success else "❌"
                    logger.info(
                        "%s 定时任务 %s 完成 (%dms): %s",
                        icon, job.name, record.duration_ms, record.output[:100],
                    )

                except Exception as e:
                    logger.error("定时任务 %s 执行异常: %s", job.name, e, exc_info=True)
                    self._history.append(CronRunRecord(
                        job_name=job.name,
                        agent=job.agent,
                        task=job.task,
                        success=False,
                        error=str(e),
                        timestamp=now.isoformat(),
                    ))
                    self._save_history()
                    job.last_run = now.isoformat()
                    self._save_jobs()

    def stop_loop(self) -> None:
        """停止 cron 循环。"""
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            logger.info("Cron 调度器已请求停止")


# ── 便捷函数 ────────────────────────────────────────────────────────


def parse_cron(cron_expr: str) -> dict[str, str]:
    """解析 5 字段 cron 表达式为各部分。

    Returns:
        {"minute": ..., "hour": ..., "dom": ..., "month": ..., "dow": ...}
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Cron 表达式必须是 5 字段: '{cron_expr}'")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "dom": parts[2],
        "month": parts[3],
        "dow": parts[4],
    }
