"""Agent Manifest 协议 — 解耦的 Agent 自描述与发现。

每个专业 Agent 通过 agent.yaml 声明自己的能力、任务类型和 CLI 接口。
Agent-hub 只读取这些清单，永不修改它们。

设计原则：
- 零侵入：每个项目只需新增一个 agent.yaml，不改任何代码
- 自描述：Agent 自己声明能做什么，Agent-hub 不硬编码
- 可发现：扫描 agents.d/ 目录自动发现所有可用 Agent
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── 数据模型 ────────────────────────────────────────────────────────


@dataclass
class AgentTask:
    """Agent 能执行的一项任务。

    Attributes:
        name: 任务名（如 "analyze_code"），在 Agent 内唯一
        description: 自然语言描述，供 LLM 路由使用
        input: 期望的输入参数 schema
        output: 期望的输出 schema
        tools: 该任务可使用的工具列表（可选，用于 LLM 规划）
    """

    name: str
    description: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentTask:
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            input=data.get("input", {}) or {},
            output=data.get("output", {}) or {},
            tools=[str(t) for t in (data.get("tools") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.input:
            result["input"] = self.input
        if self.output:
            result["output"] = self.output
        if self.tools:
            result["tools"] = self.tools
        return result


@dataclass
class AgentCapabilities:
    """Agent 的能力集合。

    Attributes:
        tasks: 该 Agent 能执行的任务列表
        tools: 全局可用工具列表（可选，跨任务共享）
        models: 支持的模型列表
        modes: 支持的运行模式
        constraints: 约束条件（max_concurrency, timeout 等）
    """

    tasks: list[AgentTask] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentCapabilities:
        raw_tasks = data.get("tasks") or []
        tasks = [AgentTask.from_dict(t) for t in raw_tasks if isinstance(t, dict)]
        return cls(
            tasks=tasks,
            tools=[str(x) for x in (data.get("tools") or [])],
            models=[str(x) for x in (data.get("models") or [])],
            modes=[str(x) for x in (data.get("modes") or [])],
            constraints=data.get("constraints") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.tasks:
            result["tasks"] = [t.to_dict() for t in self.tasks]
        if self.tools:
            result["tools"] = self.tools
        if self.models:
            result["models"] = self.models
        if self.modes:
            result["modes"] = self.modes
        if self.constraints:
            result["constraints"] = self.constraints
        return result


@dataclass
class AgentInterface:
    """Agent 的 CLI 接口描述。

    Attributes:
        command: 调用此 Agent 的 CLI 命令模板。
            支持占位符：{goal}, {task}, {mode}, {project}
            示例: "omniagent --mode {mode} --json-output \"{goal}\""
        working_dir: 可选的工作目录（若 Agent 不在 PATH 中）
        env: 可选的额外环境变量
    """

    command: str
    working_dir: str | None = None
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentInterface:
        if isinstance(data, str):
            return cls(command=data)
        return cls(
            command=str(data.get("command", "")),
            working_dir=data.get("working_dir"),
            env=data.get("env") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"command": self.command}
        if self.working_dir:
            result["working_dir"] = self.working_dir
        if self.env:
            result["env"] = self.env
        return result


@dataclass
class AgentManifest:
    """Agent 的完整自描述清单。

    从 agent.yaml 反序列化而来，是 Agent-hub 与专业 Agent 之间的
    唯一契约。Agent-hub 只读取此文件，永不修改它。

    Attributes:
        name: Agent 唯一标识名（如 "omniagent"）
        display_name: 人类可读的名称（如 "通用 AI 编程 Agent"）
        description: 详细功能描述，供 LLM 路由使用
        capabilities: 能力集合（任务、工具、模型）
        protocol: 通信协议（"cli" | "mcp" | "http"），默认 "cli"
        interface: CLI 接口描述
        version: manifest 格式版本
        source_path: agent.yaml 文件所在目录的绝对路径（运行时填充）
        constraints: 全局约束（可被 capabilities.constraints 覆盖）
    """

    name: str
    display_name: str = ""
    description: str = ""
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    protocol: str = "cli"
    interface: AgentInterface = field(default_factory=lambda: AgentInterface(command=""))
    version: str = "1.0"
    source_path: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)

    # ── 工厂方法 ─────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source_path: str = "") -> AgentManifest:
        """从字典构造 AgentManifest。"""
        caps_data = data.get("capabilities") or {}
        iface_data = data.get("interface") or {}
        return cls(
            name=str(data.get("name", "")),
            display_name=str(data.get("display_name", data.get("displayName", ""))),
            description=str(data.get("description", "")),
            capabilities=AgentCapabilities.from_dict(caps_data),
            protocol=str(data.get("protocol", "cli")),
            interface=AgentInterface.from_dict(iface_data),
            version=str(data.get("version", "1.0")),
            source_path=source_path,
            constraints=data.get("constraints") or {},
        )

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> AgentManifest:
        """从 agent.yaml 文件加载 AgentManifest。

        Args:
            yaml_path: agent.yaml 文件的路径

        Returns:
            AgentManifest 实例

        Raises:
            FileNotFoundError: 文件不存在
            yaml.YAMLError: YAML 解析错误
            ValueError: 必填字段缺失
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Agent manifest 文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"agent.yaml 内容必须是 YAML 字典，实际为: {type(data).__name__}")

        # source_path 指向 agent.yaml 所在目录（即项目根目录）
        manifest = cls.from_dict(data, source_path=str(path.parent.resolve()))

        # 验证必填字段
        manifest._validate()

        logger.info("加载 Agent manifest: %s (v%s) from %s", manifest.name, manifest.version, path)
        return manifest

    # ── 验证 ─────────────────────────────────────────────────────

    def _validate(self) -> None:
        """验证必填字段。"""
        errors: list[str] = []

        if not self.name:
            errors.append("name 是必填字段")
        if not self.capabilities.tasks:
            errors.append("capabilities.tasks 不能为空 — 至少声明一个任务")
        # internal 协议不需要 CLI command（在进程内调度，不生成子进程）
        if self.protocol != "internal" and not self.interface.command:
            errors.append("interface.command 是必填字段 — 指定 CLI 调用命令")

        # 警告：mcp / http 协议尚未在 CLIBridge 中实现
        UNIMPLEMENTED = {"mcp", "http"}
        if self.protocol in UNIMPLEMENTED:
            logger.warning(
                "Agent '%s' 使用尚未实现的协议 '%s'，将无法通过 CLI Bridge 执行。"
                "当前仅支持 cli 和 internal。",
                self.name, self.protocol,
            )

        # 验证每个 task 的必填字段
        for task in self.capabilities.tasks:
            if not task.name:
                errors.append(f"capabilities.tasks 中存在未命名的任务")
            if not task.description:
                errors.append(f"任务 '{task.name}' 缺少 description — LLM 路由依赖此字段")

        if errors:
            raise ValueError(
                f"AgentManifest '{self.name}' 验证失败:\n  " + "\n  ".join(errors)
            )

    def validate(self) -> list[str]:
        """公开的验证方法（不抛异常）。返回错误列表，空列表表示合法。"""
        try:
            self._validate()
            return []
        except ValueError as e:
            return str(e).split("\n  ")

    # ── 序列化 ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于生成 AgentCard）。"""
        result: dict[str, Any] = {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "protocol": self.protocol,
            "version": self.version,
        }
        result["capabilities"] = self.capabilities.to_dict()
        result["interface"] = self.interface.to_dict()
        if self.constraints:
            result["constraints"] = self.constraints
        return result

    def to_agent_card(self) -> dict[str, Any]:
        """生成兼容 omniagent AgentCard 格式的字典。

        omniagent 的 AgentCard 格式与 AgentManifest 高度兼容，
        只需做少量字段映射。
        """
        card = self.to_dict()
        # AgentCard 用 constraints 在顶层（而非 capabilities 内）
        if self.constraints:
            card["constraints"] = self.constraints
        # AgentCard 兼容字段
        card["capabilities"]["read_only"] = (
            "read_only" in self.capabilities.constraints
            and self.capabilities.constraints["read_only"]
        )
        return card

    # ── 查询 ─────────────────────────────────────────────────────

    @property
    def is_valid(self) -> bool:
        """是否通过验证。"""
        return len(self.validate()) == 0

    def get_task(self, name: str) -> AgentTask | None:
        """按名称查找任务。"""
        for task in self.capabilities.tasks:
            if task.name == name:
                return task
        return None

    def has_task(self, name: str) -> bool:
        """是否支持某个任务。"""
        return self.get_task(name) is not None

    def format_for_llm(self) -> str:
        """生成供 LLM 路由使用的 Agent 描述文本。"""
        lines = [
            f"Agent: {self.name}",
            f"  显示名: {self.display_name}",
            f"  描述: {self.description}",
            f"  协议: {self.protocol}",
            f"  可执行的任务:",
        ]
        for task in self.capabilities.tasks:
            lines.append(f"    - {task.name}: {task.description}")
        return "\n".join(lines)


# ── Agent 发现 ──────────────────────────────────────────────────────


def discover_agents(
    search_paths: list[str] | None = None,
    *,
    registry_dir: str | Path | None = None,
) -> dict[str, AgentManifest]:
    """扫描并发现所有可用的 Agent。

    两种发现方式（按优先级）：
    1. registry_dir (agents.d/): 每个 YAML 文件包含 project_path: 指向，
       然后从该项目的 agent.yaml 加载 manifest
    2. search_paths: 直接在这些目录中查找 agent.yaml

    Args:
        search_paths: 直接搜索 agent.yaml 的路径列表
        registry_dir: agents.d/ 注册目录路径（默认 D:/Agent-hub/agents.d/）

    Returns:
        {agent_name: AgentManifest} 字典，按名称索引
    """
    manifests: dict[str, AgentManifest] = {}

    # 方式 1：从 agents.d/ 注册目录加载
    if registry_dir is None:
        # 默认位置：项目根目录下的 agents.d/
        registry_dir = Path(__file__).parent.parent / "agents.d"

    reg_path = Path(registry_dir)
    if reg_path.is_dir():
        for yaml_file in sorted(reg_path.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    reg_data = yaml.safe_load(f) or {}

                project_path = reg_data.get("project_path", "")
                if not project_path:
                    logger.warning("注册文件 %s 缺少 project_path，跳过", yaml_file)
                    continue

                # 从项目目录加载 agent.yaml
                project_yaml = Path(project_path) / "agent.yaml"
                if not project_yaml.exists():
                    logger.warning(
                        "注册的 Agent %s 在 %s 中未找到 agent.yaml，跳过",
                        yaml_file.stem, project_path,
                    )
                    continue

                manifest = AgentManifest.from_yaml(str(project_yaml))
                manifests[manifest.name] = manifest

            except Exception as e:
                logger.warning("加载注册文件 %s 失败: %s", yaml_file, e)

    # 方式 2：从 search_paths 直接扫描
    if search_paths:
        for search_path in search_paths:
            path = Path(search_path)
            if not path.is_dir():
                # 可能是直接的 agent.yaml 路径
                if path.name == "agent.yaml" and path.exists():
                    try:
                        manifest = AgentManifest.from_yaml(str(path))
                        if manifest.name not in manifests:
                            manifests[manifest.name] = manifest
                    except Exception as e:
                        logger.warning("加载 %s 失败: %s", path, e)
                continue

            # 在目录中查找 agent.yaml
            agent_yaml = path / "agent.yaml"
            if agent_yaml.exists():
                try:
                    manifest = AgentManifest.from_yaml(str(agent_yaml))
                    if manifest.name not in manifests:
                        manifests[manifest.name] = manifest
                except Exception as e:
                    logger.warning("加载 %s 失败: %s", agent_yaml, e)

    logger.info("发现 %d 个 Agent: %s", len(manifests), sorted(manifests.keys()))
    return manifests


def discover_from_registry(
    registry_dir: str | Path | None = None,
) -> dict[str, AgentManifest]:
    """仅从 agents.d/ 注册目录发现 Agent（便捷函数）。"""
    return discover_agents(registry_dir=registry_dir)
