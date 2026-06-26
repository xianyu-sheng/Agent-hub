"""Model Config Store — Agent-hub 模型配置管理器。

管理 Agent-hub 自身的 LLM 模型配置，持久化到 models.yaml。
通过 agent-hub models 命令进行增删改查，支持：
- 多模型供应商（deepseek, anthropic, openai...）
- API Key 明文存储或环境变量引用 ${VAR_NAME}
- 模型优先级排序（用于路由和汇总时的 fallback 顺序）

不管理其他 Agent 的模型 — 每个专业 Agent 在各自的 agent.yaml 中声明模型。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── 环境变量引用模式 ──────────────────────────────────────────────

_ENV_VAR_PATTERN = re.compile(r"^\$\{(\w+)\}$")


def _resolve_env_var(value: str) -> str:
    """解析 ${VAR_NAME} 格式的环境变量引用。

    Args:
        value: 可能包含 ${VAR_NAME} 的字符串

    Returns:
        解析后的值。若 value 是不匹配的明文则原样返回。
    """
    if not value:
        return value
    m = _ENV_VAR_PATTERN.match(value)
    if m:
        env_name = m.group(1)
        return os.environ.get(env_name, "")
    return value


# ── 数据模型 ──────────────────────────────────────────────────────


@dataclass
class ModelEntry:
    """单个 LLM 模型的配置条目。

    Attributes:
        name: 模型标识名（如 "deepseek-v4-pro"），作为唯一 key
        provider: 供应商名（deepseek, anthropic, openai...），用于分类
        api_base: API endpoint URL
        api_key: API Key（明文或 ${ENV_VAR} 格式的环境变量引用）
        models: 该供应商支持的模型 ID 列表（可能有多个变体）
        default: 是否为默认模型（优先级列表的第一候选）
    """

    name: str
    provider: str = ""
    api_base: str = ""
    api_key: str = ""
    models: list[str] = field(default_factory=list)
    default: bool = False

    @property
    def resolved_api_key(self) -> str:
        """获取解析后的 API Key（展开环境变量引用）。"""
        return _resolve_env_var(self.api_key)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.provider:
            d["provider"] = self.provider
        if self.api_base:
            d["api_base"] = self.api_base
        if self.api_key:
            d["api_key"] = self.api_key
        if self.models:
            d["models"] = self.models
        if self.default:
            d["default"] = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelEntry:
        return cls(
            name=str(data.get("name", "")),
            provider=str(data.get("provider", "")),
            api_base=str(data.get("api_base", "")),
            api_key=str(data.get("api_key", "")),
            models=[str(m) for m in (data.get("models") or [])],
            default=bool(data.get("default", False)),
        )


# ── 配置存储 ──────────────────────────────────────────────────────


class ModelConfigStore:
    """Agent-hub 模型配置的持久化管理器。

    读写 models.yaml，提供 CRUD 操作和优先级管理。
    线程不安全 — 调用者负责同步。

    使用方式：
        store = ModelConfigStore()
        store.add(ModelEntry(name="deepseek-v4-pro", ...))
        store.save()
        models = store.list_all()
    """

    # models.yaml 的默认位置（Agent-hub 项目根目录）
    _DEFAULT_PATH = Path(__file__).parent.parent / "models.yaml"

    # 默认的 models.yaml 模板（首次使用时自动生成）
    _DEFAULT_TEMPLATE = """# Agent-hub 模型配置
# 由 agent-hub models 命令管理，也可以手动编辑。
# 修改后运行: agent-hub agent reload
#
# API Key 支持两种格式：
#   明文:   sk-xxxxxxxxxxxxxxxx
#   环境变量: ${DEEPSEEK_API_KEY}     ← 推荐，更安全

models:
  - name: deepseek-v4-pro
    provider: deepseek
    api_base: https://api.deepseek.com
    api_key: ${DEEPSEEK_API_KEY}
    models:
      - deepseek-v4-pro
    default: true

model_priority:
  - deepseek-v4-pro
"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = Path(config_path or self._DEFAULT_PATH)
        self._data: dict[str, Any] = {}
        self._models: dict[str, ModelEntry] = {}
        self._priority: list[str] = []
        self._loaded = False

    # ── 持久化 ─────────────────────────────────────────────────

    def load(self) -> None:
        """从 models.yaml 加载配置。文件不存在时自动创建默认模板。"""
        if not self._config_path.exists():
            _create_parent(self._config_path)
            self._config_path.write_text(self._DEFAULT_TEMPLATE, encoding="utf-8")
            logger.info("创建默认模型配置: %s", self._config_path)

        try:
            raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("解析 models.yaml 失败: %s，使用默认配置", e)
            raw = {}

        self._data = raw
        self._models = {}
        self._priority = []

        for entry_data in raw.get("models") or []:
            if isinstance(entry_data, dict):
                entry = ModelEntry.from_dict(entry_data)
                if entry.name:
                    self._models[entry.name] = entry

        self._priority = [
            str(n) for n in (raw.get("model_priority") or []) if n in self._models
        ]

        # 没有显式优先级时，按 default 标记 + 声明顺序
        if not self._priority and self._models:
            default_first = sorted(
                self._models.values(), key=lambda e: (not e.default, list(self._models.keys()).index(e.name))
            )
            self._priority = [e.name for e in default_first]

        self._loaded = True
        logger.info(
            "加载模型配置: %d 个模型, 优先级=%s",
            len(self._models), self._priority,
        )

    def save(self) -> None:
        """将当前配置持久化到 models.yaml。"""
        models_data = [e.to_dict() for e in self._models.values()]
        output = {
            "models": models_data,
            "model_priority": self._priority,
        }
        _create_parent(self._config_path)
        self._config_path.write_text(
            yaml.dump(output, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("已保存模型配置: %s (%d 个模型)", self._config_path, len(self._models))

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ── CRUD ────────────────────────────────────────────────────

    def add(self, entry: ModelEntry) -> None:
        """添加模型配置。若 name 已存在则报错。"""
        self._ensure_loaded()
        if not entry.name:
            raise ValueError("模型 name 不能为空")
        if entry.name in self._models:
            raise ValueError(
                f"模型 '{entry.name}' 已存在。使用 'agent-hub models update {entry.name} ...' 更新，"
                f"或 'agent-hub models remove {entry.name}' 先删除。"
            )
        self._models[entry.name] = entry
        if entry.name not in self._priority:
            if entry.default:
                self._priority.insert(0, entry.name)
            else:
                self._priority.append(entry.name)
        self.save()

    def remove(self, name: str) -> ModelEntry | None:
        """删除模型配置。返回被删除的条目，不存在返回 None。"""
        self._ensure_loaded()
        entry = self._models.pop(name, None)
        if entry and name in self._priority:
            self._priority.remove(name)
        if entry:
            self.save()
        return entry

    def update(self, name: str, **kwargs: Any) -> ModelEntry:
        """更新指定模型的字段。

        支持的字段：api_key, api_base, provider, default
        返回更新后的 ModelEntry。
        """
        self._ensure_loaded()
        if name not in self._models:
            raise ValueError(f"模型 '{name}' 不存在。使用 'agent-hub models add {name} ...' 先添加。")

        entry = self._models[name]
        if "api_key" in kwargs:
            entry.api_key = str(kwargs["api_key"])
        if "api_base" in kwargs:
            entry.api_base = str(kwargs["api_base"])
        if "provider" in kwargs and kwargs["provider"]:
            entry.provider = str(kwargs["provider"])
        if "default" in kwargs:
            entry.default = bool(kwargs["default"])

        # 设为默认时提到优先级最前
        if entry.default and self._priority and self._priority[0] != name:
            if name in self._priority:
                self._priority.remove(name)
            self._priority.insert(0, name)

        self.save()
        return entry

    def get(self, name: str) -> ModelEntry | None:
        """获取指定模型配置。"""
        self._ensure_loaded()
        return self._models.get(name)

    def list_all(self) -> list[ModelEntry]:
        """列出所有模型（按优先级排序）。"""
        self._ensure_loaded()
        ordered = []
        for name in self._priority:
            if name in self._models:
                ordered.append(self._models[name])
        for name, entry in self._models.items():
            if name not in self._priority:
                ordered.append(entry)
        return ordered

    # ── 优先级 ──────────────────────────────────────────────────

    def get_priority(self) -> list[str]:
        """获取模型优先级列表。"""
        self._ensure_loaded()
        return list(self._priority)

    def set_priority(self, names: list[str]) -> None:
        """设置模型优先级（按名字列表排序）。"""
        self._ensure_loaded()
        for name in names:
            if name not in self._models:
                raise ValueError(f"模型 '{name}' 不在配置中。请先添加。")
        self._priority = list(names)
        # 清除旧的 default 标记，将第一个设为 default
        for e in self._models.values():
            e.default = False
        if self._priority:
            first = self._models.get(self._priority[0])
            if first:
                first.default = True
        self.save()

    def resolve_api_key(self, name: str) -> str:
        """解析指定模型的 API Key（展开环境变量引用）。"""
        entry = self.get(name)
        if not entry:
            return ""
        return entry.resolved_api_key

    @property
    def first_available(self) -> ModelEntry | None:
        """获取优先级最高的模型（作为默认模型）。"""
        entries = self.list_all()
        return entries[0] if entries else None


def _create_parent(path: Path) -> None:
    """确保父目录存在。"""
    parent = path.parent
    if parent and not parent.is_dir():
        parent.mkdir(parents=True, exist_ok=True)
