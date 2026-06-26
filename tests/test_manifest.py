"""Tests for AgentManifest — loading, validation, discovery, serialization."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_hub.manifest import (
    AgentCapabilities,
    AgentInterface,
    AgentManifest,
    AgentTask,
    discover_agents,
)


class TestAgentTask:
    """AgentTask 构造测试。"""

    def test_from_dict_basic(self):
        """基本字段解析。"""
        data = {
            "name": "analyze_code",
            "description": "只读分析代码",
            "input": {"goal": "string"},
            "output": {"report": "string"},
            "tools": ["read_file", "search_files"],
        }
        task = AgentTask.from_dict(data)
        assert task.name == "analyze_code"
        assert task.description == "只读分析代码"
        assert task.input == {"goal": "string"}
        assert task.output == {"report": "string"}
        assert task.tools == ["read_file", "search_files"]

    def test_from_dict_minimal(self):
        """最小字段 — 只有 name 和 description。"""
        task = AgentTask.from_dict({"name": "test", "description": "a test"})
        assert task.name == "test"
        assert task.input == {}
        assert task.output == {}
        assert task.tools == []

    def test_from_dict_null_fields(self):
        """空值处理。"""
        task = AgentTask.from_dict({
            "name": "test",
            "description": "desc",
            "input": None,
            "output": None,
            "tools": None,
        })
        assert task.input == {}
        assert task.output == {}
        assert task.tools == []

    def test_to_dict_roundtrip(self):
        """to_dict → from_dict 往返。"""
        task = AgentTask(
            name="search",
            description="搜索代码",
            input={"goal": "string"},
            output={"matches": "list"},
            tools=["search_files"],
        )
        data = task.to_dict()
        restored = AgentTask.from_dict(data)
        assert restored.name == task.name
        assert restored.description == task.description
        assert restored.input == task.input
        assert restored.tools == task.tools

    def test_to_dict_omits_empty_fields(self):
        """序列化时跳过空字段。"""
        task = AgentTask(name="test", description="desc")
        data = task.to_dict()
        assert "input" not in data
        assert "output" not in data
        assert "tools" not in data


class TestAgentCapabilities:
    """AgentCapabilities 构造测试。"""

    def test_from_dict_with_tasks(self):
        """包含任务列表。"""
        data = {
            "tasks": [
                {"name": "task1", "description": "任务1"},
                {"name": "task2", "description": "任务2", "tools": ["tool_a"]},
            ],
            "tools": ["tool_a", "tool_b"],
            "models": ["model_x"],
            "modes": ["react"],
            "constraints": {"max_concurrency": 3},
        }
        caps = AgentCapabilities.from_dict(data)
        assert len(caps.tasks) == 2
        assert caps.tasks[0].name == "task1"
        assert caps.tasks[1].tools == ["tool_a"]
        assert caps.tools == ["tool_a", "tool_b"]
        assert caps.models == ["model_x"]
        assert caps.modes == ["react"]
        assert caps.constraints == {"max_concurrency": 3}

    def test_from_dict_empty(self):
        """空能力。"""
        caps = AgentCapabilities.from_dict({})
        assert caps.tasks == []
        assert caps.tools == []
        assert caps.models == []

    def test_from_dict_null_tasks(self):
        """tasks 为 None/null。"""
        caps = AgentCapabilities.from_dict({"tasks": None})
        assert caps.tasks == []


class TestAgentInterface:
    """AgentInterface 构造测试。"""

    def test_from_dict_string(self):
        """interface 直接是命令字符串。"""
        iface = AgentInterface.from_dict("omniagent --json-output task")
        assert iface.command == "omniagent --json-output task"
        assert iface.working_dir is None

    def test_from_dict_object(self):
        """interface 是完整对象。"""
        iface = AgentInterface.from_dict({
            "command": "cd D:/proj && python -m app",
            "working_dir": "D:/proj",
            "env": {"PYTHONPATH": "src"},
        })
        assert iface.command == "cd D:/proj && python -m app"
        assert iface.working_dir == "D:/proj"
        assert iface.env == {"PYTHONPATH": "src"}

    def test_to_dict_minimal(self):
        """最小序列化。"""
        iface = AgentInterface(command="test")
        data = iface.to_dict()
        assert data == {"command": "test"}


class TestAgentManifest:
    """AgentManifest 加载、验证、序列化测试。"""

    # ── YAML 加载 ─────────────────────────────────────────────

    def test_from_yaml_valid(self):
        """加载合法的 agent.yaml。"""
        yaml_content = """
name: test-agent
display_name: 测试 Agent
description: 用于测试的 Agent
protocol: cli
capabilities:
  tasks:
    - name: do_something
      description: 执行某个操作
      input:
        goal: "string"
      output:
        result: "string"
interface:
  command: test-agent --run "{goal}"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            manifest = AgentManifest.from_yaml(tmp_path)
            assert manifest.name == "test-agent"
            assert manifest.display_name == "测试 Agent"
            assert manifest.protocol == "cli"
            assert len(manifest.capabilities.tasks) == 1
            assert manifest.capabilities.tasks[0].name == "do_something"
            assert manifest.interface.command == 'test-agent --run "{goal}"'
            assert manifest.source_path  # 应指向临时文件所在目录
        finally:
            Path(tmp_path).unlink()

    def test_from_yaml_missing_name_raises(self):
        """缺少必填字段 name。"""
        yaml_content = """
display_name: No Name
capabilities:
  tasks:
    - name: task1
      description: desc
interface:
  command: test
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            with pytest.raises(ValueError, match="name"):
                AgentManifest.from_yaml(tmp_path)
        finally:
            Path(tmp_path).unlink()

    def test_from_yaml_missing_interface_raises(self):
        """缺少 interface.command。"""
        yaml_content = """
name: no-iface
capabilities:
  tasks:
    - name: task1
      description: desc
interface:
  working_dir: /tmp
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            with pytest.raises(ValueError, match="interface\\.command"):
                AgentManifest.from_yaml(tmp_path)
        finally:
            Path(tmp_path).unlink()

    def test_from_yaml_missing_tasks_raises(self):
        """capabilities.tasks 为空。"""
        yaml_content = """
name: no-tasks
capabilities:
  tools: [tool_a]
interface:
  command: test
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            with pytest.raises(ValueError, match="tasks"):
                AgentManifest.from_yaml(tmp_path)
        finally:
            Path(tmp_path).unlink()

    # ── 验证 ─────────────────────────────────────────────────

    def test_validate_valid(self):
        """合法 manifest 无错误。"""
        manifest = _make_valid_manifest()
        assert manifest.validate() == []
        assert manifest.is_valid is True

    def test_validate_task_no_description(self):
        """任务缺少 description。"""
        manifest = _make_valid_manifest()
        manifest.capabilities.tasks[0].description = ""
        errors = manifest.validate()
        assert any("description" in e for e in errors)

    # ── 任务查询 ─────────────────────────────────────────────

    def test_get_task(self):
        """按名称查找任务。"""
        manifest = _make_valid_manifest()
        task = manifest.get_task("analyze_code")
        assert task is not None
        assert task.name == "analyze_code"

        assert manifest.get_task("nonexistent") is None

    def test_has_task(self):
        """has_task 检查。"""
        manifest = _make_valid_manifest()
        assert manifest.has_task("analyze_code") is True
        assert manifest.has_task("fake_task") is False

    # ── 序列化 ───────────────────────────────────────────────

    def test_to_dict(self):
        """to_dict 输出完整字典。"""
        manifest = _make_valid_manifest()
        data = manifest.to_dict()
        assert data["name"] == "test-agent"
        assert "capabilities" in data
        assert "interface" in data
        assert len(data["capabilities"]["tasks"]) == 1

    def test_to_agent_card(self):
        """AgentCard 兼容格式。"""
        manifest = _make_valid_manifest()
        card = manifest.to_agent_card()
        assert card["name"] == "test-agent"
        assert "capabilities" in card
        assert "read_only" in card["capabilities"]

    def test_format_for_llm(self):
        """LLM 路由用的文本格式。"""
        manifest = _make_valid_manifest()
        text = manifest.format_for_llm()
        assert "test-agent" in text
        assert "analyze_code" in text
        assert "分析代码" in text

    # ── 显示名兼容性 ─────────────────────────────────────────

    def test_display_name_camel_case(self):
        """displayName (camelCase) 兼容性。"""
        manifest = AgentManifest.from_dict({
            "name": "test",
            "displayName": "Camel Display",
            "capabilities": {
                "tasks": [{"name": "t", "description": "d"}],
            },
            "interface": {"command": "test"},
        })
        assert manifest.display_name == "Camel Display"


# ── 辅助 ────────────────────────────────────────────────────────────


def _make_valid_manifest() -> AgentManifest:
    """创建合法的测试用 manifest。"""
    return AgentManifest(
        name="test-agent",
        display_name="测试 Agent",
        description="用于测试",
        capabilities=AgentCapabilities(
            tasks=[
                AgentTask(
                    name="analyze_code",
                    description="分析代码",
                    input={"goal": "string"},
                    output={"report": "string"},
                ),
            ],
        ),
        interface=AgentInterface(command="test-agent --run"),
    )


class TestDiscoverAgents:
    """Agent 发现测试。"""

    def test_discover_empty_registry(self):
        """空注册目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            agents = discover_agents(registry_dir=tmp)
            assert agents == {}

    def test_discover_from_registry(self):
        """从注册目录发现 Agent。"""
        with tempfile.TemporaryDirectory() as tmp:
            reg_dir = Path(tmp)

            # 创建项目目录 + agent.yaml
            project_dir = reg_dir / "test-project"
            project_dir.mkdir()
            agent_yaml = project_dir / "agent.yaml"
            agent_yaml.write_text("""
name: test-agent
display_name: Test
capabilities:
  tasks:
    - name: do_work
      description: Does work
interface:
  command: test-agent --work
""", encoding="utf-8")

            # 创建注册文件
            reg_file = reg_dir / "test-agent.yaml"
            reg_file.write_text(f"project_path: {project_dir}", encoding="utf-8")

            agents = discover_agents(registry_dir=str(reg_dir))
            assert "test-agent" in agents

            manifest = agents["test-agent"]
            assert manifest.name == "test-agent"
            assert len(manifest.capabilities.tasks) == 1
            assert manifest.capabilities.tasks[0].name == "do_work"

    def test_discover_missing_project_path(self):
        """注册文件缺少 project_path。"""
        with tempfile.TemporaryDirectory() as tmp:
            reg_dir = Path(tmp)
            reg_file = reg_dir / "bad.yaml"
            reg_file.write_text("name: orphan", encoding="utf-8")

            agents = discover_agents(registry_dir=str(reg_dir))
            assert agents == {}  # 跳过

    def test_discover_missing_agent_yaml(self):
        """注册文件指向的目录没有 agent.yaml。"""
        with tempfile.TemporaryDirectory() as tmp:
            reg_dir = Path(tmp)

            project_dir = reg_dir / "empty-project"
            project_dir.mkdir()

            reg_file = reg_dir / "empty.yaml"
            reg_file.write_text(f"project_path: {project_dir}", encoding="utf-8")

            agents = discover_agents(registry_dir=str(reg_dir))
            assert agents == {}  # 跳过


class TestDiscoverRealAgents:
    """测试实际项目中的 agent.yaml 是否能正确加载。"""

    @pytest.mark.parametrize("project_path,name,expected_tasks", [
        ("D:/Agent-hub", "agent-hub", None),  # Agent-hub 自身的 agent.yaml (可能还未创建)
        ("D:/OmniAgent_CLI", "omniagent", 4),
        ("D:/SmartBench", "smartbench", 3),
    ])
    def test_real_agent_yaml_loads(self, project_path, name, expected_tasks):
        """验证实际 agent.yaml 能成功加载。"""
        yaml_path = Path(project_path) / "agent.yaml"
        if not yaml_path.exists():
            pytest.skip(f"{yaml_path} 不存在")

        manifest = AgentManifest.from_yaml(str(yaml_path))
        assert manifest.name == name
        assert manifest.is_valid
        if expected_tasks is not None:
            assert len(manifest.capabilities.tasks) >= expected_tasks
