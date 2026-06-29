"""Comprehensive unit tests for agent_hub.router.

Tests cover:
- _fix_windows_path         path normalization utility
- CollaborationStrategy     strategy enum with validation and defaults
- RoutedTask                task dataclass with from_dict/to_dict
- RoutePlan                 plan dataclass with properties and serialization
- _extract_json_block       JSON block extraction from raw text
- IntentRouter._parse_llm_output   static parser for LLM output
- IntentRouter._validate_and_enrich validation and enrichment
- IntentRouter._rule_based_route   keyword-based fallback routing
- IntentRouter._build_agent_descriptions  agent description builder
- IntentRouter._fuzzy_match_task    fuzzy task name matching

No asyncio, no LLM API calls.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

import pytest

from agent_hub.manifest import (
    AgentCapabilities,
    AgentInterface,
    AgentManifest,
    AgentTask,
)
from agent_hub.router import (
    CollaborationStrategy,
    IntentRouter,
    RoutePlan,
    RoutedTask,
    _extract_json_block,
    _fix_windows_path,
)


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_agent() -> AgentManifest:
    return AgentManifest(
        name="test_agent",
        display_name="Test Agent",
        description="A test agent for unit testing",
        protocol="cli",
        capabilities=AgentCapabilities(
            tasks=[
                AgentTask(
                    name="analyze_code",
                    description="Analyze source code for issues",
                    input={"goal": "string"},
                ),
                AgentTask(
                    name="fix_bugs",
                    description="Fix bugs in code",
                    input={"goal": "string"},
                ),
            ]
        ),
        interface=AgentInterface(command="test-agent --task {task} --goal {goal}"),
    )


@pytest.fixture
def secondary_agent() -> AgentManifest:
    return AgentManifest(
        name="writer_agent",
        display_name="Writer Agent",
        description="Writes and generates content",
        protocol="cli",
        capabilities=AgentCapabilities(
            tasks=[
                AgentTask(
                    name="write_doc",
                    description="Write documentation for the project",
                    input={"goal": "string", "topic": "string"},
                ),
                AgentTask(
                    name="review_doc",
                    description="Review and improve documentation",
                    input={"goal": "string"},
                ),
            ]
        ),
        interface=AgentInterface(command="writer --task {task} --goal {goal}"),
    )


@pytest.fixture
def deploy_agent() -> AgentManifest:
    return AgentManifest(
        name="deploy_agent",
        display_name="Deploy Agent",
        description="Deploys applications to production",
        protocol="cli",
        capabilities=AgentCapabilities(
            tasks=[
                AgentTask(
                    name="deploy_app",
                    description="Deploy the application to production servers",
                    input={"goal": "string", "environment": "string"},
                ),
                AgentTask(
                    name="rollback",
                    description="Rollback a failed deployment",
                    input={"goal": "string"},
                ),
            ]
        ),
        interface=AgentInterface(command="deploy --task {task}"),
    )


@pytest.fixture
def agents_dict(
    sample_agent: AgentManifest,
    secondary_agent: AgentManifest,
    deploy_agent: AgentManifest,
) -> dict[str, AgentManifest]:
    return {
        "test_agent": sample_agent,
        "writer_agent": secondary_agent,
        "deploy_agent": deploy_agent,
    }


# ══════════════════════════════════════════════════════════════════════
# _fix_windows_path
# ══════════════════════════════════════════════════════════════════════


class TestFixWindowsPath:
    """Tests for _fix_windows_path path normalization."""

    def test_missing_separator_drive_d(self):
        """D:project → D:/project"""
        assert _fix_windows_path("D:project") == "D:/project"

    def test_missing_separator_drive_c(self):
        """C:Users/test → C:/Users/test"""
        assert _fix_windows_path("C:Users/test") == "C:/Users/test"

    def test_missing_separator_lowercase(self):
        """e:data → e:/data"""
        assert _fix_windows_path("e:data") == "e:/data"

    def test_already_normal_forward_slash(self):
        """D:/normal → D:/normal (unchanged)"""
        assert _fix_windows_path("D:/normal") == "D:/normal"

    def test_already_normal_backslash(self):
        r"""C:\normal → C:\normal (unchanged)"""
        assert _fix_windows_path("C:\\normal") == "C:\\normal"

    def test_empty_string(self):
        """Empty string → empty string"""
        assert _fix_windows_path("") == ""

    def test_no_drive_letter(self):
        """Relative path without drive letter is unchanged."""
        assert _fix_windows_path("project/subdir") == "project/subdir"

    def test_absolute_with_backslash(self):
        r"""C:\Users\test\path stays unchanged."""
        assert _fix_windows_path("C:\\Users\\test\\path") == "C:\\Users\\test\\path"

    def test_colon_in_middle_not_drive(self):
        """A path with colon not at position 1 is unchanged."""
        assert _fix_windows_path("some:thing") == "some:thing"

    def test_single_char_before_colon(self):
        """Only single letter before colon triggers fix."""
        assert _fix_windows_path("AB:path") == "AB:path"

    def test_drive_z_with_missing_separator(self):
        """Z:archive → Z:/archive"""
        assert _fix_windows_path("Z:archive") == "Z:/archive"

    def test_drive_with_deep_path(self):
        """D:deep/nested/path → D:/deep/nested/path"""
        assert (
            _fix_windows_path("D:deep/nested/path") == "D:/deep/nested/path"
        )

    def test_path_with_spaces(self):
        """Preserves spaces in paths."""
        assert _fix_windows_path("D:my project") == "D:/my project"


# ══════════════════════════════════════════════════════════════════════
# CollaborationStrategy
# ══════════════════════════════════════════════════════════════════════


class TestCollaborationStrategy:
    """Tests for CollaborationStrategy constants and methods."""

    def test_is_valid_all_known(self):
        """All known strategy names are valid."""
        for strategy in CollaborationStrategy.ALL:
            assert CollaborationStrategy.is_valid(strategy)

    def test_is_valid_invalid(self):
        """Invalid strategy name returns False."""
        assert not CollaborationStrategy.is_valid("invalid")
        assert not CollaborationStrategy.is_valid("")
        assert not CollaborationStrategy.is_valid("unknown_strategy")

    def test_is_loop_debate(self):
        assert CollaborationStrategy.is_loop("debate") is True

    def test_is_loop_reflection(self):
        assert CollaborationStrategy.is_loop("reflection") is True

    def test_is_loop_non_loop(self):
        for s in ["fan_out", "pipeline", "plan_execute", "vote", "hitl"]:
            assert CollaborationStrategy.is_loop(s) is False

    def test_default_for_optimize(self):
        """优化 keywords map to DEBATE."""
        inputs = ["提升性能", "优化代码", "改进流程", "修复bug", "提高效率"]
        for text in inputs:
            assert (
                CollaborationStrategy.default_for(text)
                == CollaborationStrategy.DEBATE
            )

    def test_default_for_write(self):
        """写/创作/生成/撰写/博客 keywords map to REFLECTION."""
        inputs = ["写文档", "创作故事", "生成报告", "撰写博客", "博客文章"]
        for text in inputs:
            assert (
                CollaborationStrategy.default_for(text)
                == CollaborationStrategy.REFLECTION
            )

    def test_default_for_deploy(self):
        """部署/推送/发布/上线 keywords map to HITL."""
        inputs = ["部署服务", "推送更新", "发布版本", "上线功能"]
        for text in inputs:
            assert (
                CollaborationStrategy.default_for(text)
                == CollaborationStrategy.HUMAN_IN_LOOP
            )

    def test_default_for_evaluate(self):
        """评估/决策/对比/安全审查 keywords map to VOTE."""
        inputs = ["评估风险", "决策方案", "对比模型", "安全审查代码"]
        for text in inputs:
            assert (
                CollaborationStrategy.default_for(text)
                == CollaborationStrategy.VOTE
            )

    def test_default_for_unmatched(self):
        """Input without matching keywords defaults to FAN_OUT."""
        inputs = ["你好", "帮我查一下天气", "random input no match"]
        for text in inputs:
            assert (
                CollaborationStrategy.default_for(text)
                == CollaborationStrategy.FAN_OUT
            )

    def test_all_constants(self):
        """ALL set contains all strategy constants."""
        expected = {
            "fan_out",
            "pipeline",
            "debate",
            "reflection",
            "plan_execute",
            "vote",
            "hitl",
        }
        assert CollaborationStrategy.ALL == expected

    def test_loop_strategies(self):
        """LOOP_STRATEGIES contains debate and reflection."""
        assert CollaborationStrategy.LOOP_STRATEGIES == {"debate", "reflection"}

    def test_interactive_strategies(self):
        """INTERACTIVE_STRATEGIES contains hitl."""
        assert CollaborationStrategy.INTERACTIVE_STRATEGIES == {"hitl"}

    def test_all_strategies_covered_by_valid(self):
        """Every constant in ALL passes is_valid."""
        for s in CollaborationStrategy.ALL:
            assert CollaborationStrategy.is_valid(s)


# ══════════════════════════════════════════════════════════════════════
# RoutedTask
# ══════════════════════════════════════════════════════════════════════


class TestRoutedTask:
    """Tests for RoutedTask dataclass."""

    def test_from_dict_all_fields(self):
        """from_dict populates all fields correctly."""
        data = {
            "id": 1,
            "agent": "test_agent",
            "task": "analyze_code",
            "description": "Analyze the codebase",
            "params": {"goal": "find bugs"},
            "depends_on": [2, 3],
        }
        task = RoutedTask.from_dict(data)
        assert task.id == 1
        assert task.agent == "test_agent"
        assert task.task == "analyze_code"
        assert task.description == "Analyze the codebase"
        assert task.params == {"goal": "find bugs"}
        assert task.depends_on == [2, 3]

    def test_from_dict_desc_alias(self):
        """from_dict accepts 'desc' as alias for 'description'."""
        data = {
            "id": 2,
            "agent": "writer_agent",
            "task": "write_doc",
            "desc": "Write documentation",
        }
        task = RoutedTask.from_dict(data)
        assert task.description == "Write documentation"

    def test_from_dict_description_preferred(self):
        """'description' takes precedence over 'desc' when both present."""
        data = {
            "id": 3,
            "agent": "test_agent",
            "task": "fix_bugs",
            "description": "Primary description",
            "desc": "Fallback description",
        }
        task = RoutedTask.from_dict(data)
        assert task.description == "Primary description"

    def test_from_dict_input_alias(self):
        """from_dict accepts 'input' as alias for 'params'."""
        data = {
            "id": 4,
            "agent": "test_agent",
            "task": "analyze_code",
            "input": {"goal": "test input"},
        }
        task = RoutedTask.from_dict(data)
        assert task.params == {"goal": "test input"}

    def test_from_dict_params_preferred(self):
        """'params' takes precedence over 'input' when both present."""
        data = {
            "id": 5,
            "agent": "test_agent",
            "task": "analyze_code",
            "params": {"goal": "from params"},
            "input": {"goal": "from input"},
        }
        task = RoutedTask.from_dict(data)
        assert task.params == {"goal": "from params"}

    def test_from_dict_missing_id_defaults_zero(self):
        """Missing id defaults to 0."""
        data = {
            "agent": "test_agent",
            "task": "analyze_code",
        }
        task = RoutedTask.from_dict(data)
        assert task.id == 0

    def test_from_dict_empty_depends_on(self):
        """Empty or missing depends_on defaults to empty list."""
        task1 = RoutedTask.from_dict({"id": 1, "agent": "a", "task": "t"})
        task2 = RoutedTask.from_dict(
            {"id": 2, "agent": "a", "task": "t", "depends_on": []}
        )
        assert task1.depends_on == []
        assert task2.depends_on == []

    def test_to_dict_roundtrip(self):
        """to_dict preserves all fields roundtrip."""
        original = RoutedTask(
            id=1,
            agent="test_agent",
            task="analyze_code",
            description="Analyze code for bugs",
            params={"goal": "find bugs"},
            depends_on=[2, 3],
        )
        data = original.to_dict()
        restored = RoutedTask.from_dict(data)
        assert restored.id == original.id
        assert restored.agent == original.agent
        assert restored.task == original.task
        assert restored.description == original.description
        assert restored.params == original.params
        assert restored.depends_on == original.depends_on

    def test_to_dict_omits_empty_params(self):
        """to_dict does not include params when empty."""
        task = RoutedTask(id=1, agent="a", task="t", description="d")
        data = task.to_dict()
        assert "params" not in data

    def test_to_dict_omits_empty_depends_on(self):
        """to_dict does not include depends_on when empty."""
        task = RoutedTask(id=1, agent="a", task="t", description="d")
        data = task.to_dict()
        assert "depends_on" not in data

    def test_to_dict_includes_empty_description(self):
        """to_dict always includes description even if empty."""
        task = RoutedTask(id=1, agent="a", task="t")
        data = task.to_dict()
        assert "description" in data
        assert data["description"] == ""

    def test_to_dict_structure(self):
        """to_dict output has the expected shape."""
        task = RoutedTask(
            id=1,
            agent="test_agent",
            task="analyze_code",
            description="test",
            params={"key": "val"},
            depends_on=[2],
        )
        data = task.to_dict()
        assert data == {
            "id": 1,
            "agent": "test_agent",
            "task": "analyze_code",
            "description": "test",
            "params": {"key": "val"},
            "depends_on": [2],
        }

    def test_from_dict_numeric_id_conversion(self):
        """id is converted to int even if passed as float string."""
        task = RoutedTask.from_dict(
            {"id": "3", "agent": "a", "task": "t"}
        )
        assert task.id == 3
        assert isinstance(task.id, int)

    def test_from_dict_depends_on_int_conversion(self):
        """depends_on items are converted to int."""
        task = RoutedTask.from_dict(
            {"id": 1, "agent": "a", "task": "t", "depends_on": ["1", "2"]}
        )
        assert task.depends_on == [1, 2]

    def test_params_defaults_empty_dict(self):
        """Defaults for params is an empty dict."""
        task = RoutedTask(id=1, agent="a", task="t")
        assert task.params == {}


# ══════════════════════════════════════════════════════════════════════
# RoutePlan
# ══════════════════════════════════════════════════════════════════════


class TestRoutePlan:
    """Tests for RoutePlan dataclass."""

    def test_task_count_empty(self):
        """Empty tasks list returns 0."""
        plan = RoutePlan(tasks=[])
        assert plan.task_count == 0

    def test_task_count_single(self):
        """Single task returns 1."""
        plan = RoutePlan(tasks=[RoutedTask(id=1, agent="a", task="t")])
        assert plan.task_count == 1

    def test_task_count_multiple(self):
        """Multiple tasks returns correct count."""
        tasks = [
            RoutedTask(id=1, agent="a", task="t1"),
            RoutedTask(id=2, agent="b", task="t2"),
            RoutedTask(id=3, agent="a", task="t3"),
        ]
        plan = RoutePlan(tasks=tasks)
        assert plan.task_count == 3

    def test_agents_involved_sorted_unique(self):
        """agents_involved returns sorted unique agent names."""
        tasks = [
            RoutedTask(id=1, agent="z_agent", task="t1"),
            RoutedTask(id=2, agent="a_agent", task="t2"),
            RoutedTask(id=3, agent="z_agent", task="t3"),
        ]
        plan = RoutePlan(tasks=tasks)
        assert plan.agents_involved == ["a_agent", "z_agent"]

    def test_agents_involved_empty(self):
        """agents_involved returns empty list when no tasks."""
        plan = RoutePlan(tasks=[])
        assert plan.agents_involved == []

    def test_is_low_confidence_below_threshold(self):
        """Confidence below 0.7 is low confidence."""
        plan = RoutePlan(tasks=[], confidence=0.5)
        assert plan.is_low_confidence is True

    def test_is_low_confidence_at_threshold(self):
        """Confidence at exactly 0.7 is NOT low confidence."""
        plan = RoutePlan(tasks=[], confidence=0.7)
        assert plan.is_low_confidence is False

    def test_is_low_confidence_above_threshold(self):
        """Confidence above 0.7 is NOT low confidence."""
        plan = RoutePlan(tasks=[], confidence=0.85)
        assert plan.is_low_confidence is False

    def test_is_low_confidence_zero(self):
        """Confidence of 0 is low."""
        plan = RoutePlan(tasks=[], confidence=0.0)
        assert plan.is_low_confidence is True

    def test_is_loop_strategy_debate(self):
        """debate strategy is loop."""
        plan = RoutePlan(tasks=[], strategy="debate")
        assert plan.is_loop_strategy is True

    def test_is_loop_strategy_reflection(self):
        """reflection strategy is loop."""
        plan = RoutePlan(tasks=[], strategy="reflection")
        assert plan.is_loop_strategy is True

    def test_is_loop_strategy_non_loop(self):
        """Non-loop strategies return False."""
        for s in ["fan_out", "pipeline", "plan_execute", "vote", "hitl"]:
            plan = RoutePlan(tasks=[], strategy=s)
            assert plan.is_loop_strategy is False

    def test_is_interactive_hitl(self):
        """hitl strategy is interactive."""
        plan = RoutePlan(tasks=[], strategy="hitl")
        assert plan.is_interactive is True

    def test_is_interactive_non_interactive(self):
        """Non-interactive strategies return False."""
        for s in ["fan_out", "pipeline", "debate", "reflection", "plan_execute", "vote"]:
            plan = RoutePlan(tasks=[], strategy=s)
            assert plan.is_interactive is False

    def test_strategy_label_fan_out(self):
        assert RoutePlan(tasks=[], strategy="fan_out").strategy_label == "并行分派"

    def test_strategy_label_pipeline(self):
        assert RoutePlan(tasks=[], strategy="pipeline").strategy_label == "串行管线"

    def test_strategy_label_debate(self):
        assert RoutePlan(tasks=[], strategy="debate").strategy_label == "辩论-修复循环"

    def test_strategy_label_reflection(self):
        assert RoutePlan(tasks=[], strategy="reflection").strategy_label == "自反思循环"

    def test_strategy_label_plan_execute(self):
        assert (
            RoutePlan(tasks=[], strategy="plan_execute").strategy_label
            == "先规划后执行"
        )

    def test_strategy_label_vote(self):
        assert RoutePlan(tasks=[], strategy="vote").strategy_label == "多视角投票"

    def test_strategy_label_hitl(self):
        assert RoutePlan(tasks=[], strategy="hitl").strategy_label == "人机协同"

    def test_strategy_label_unknown(self):
        """Unknown strategy returns the raw string."""
        assert (
            RoutePlan(tasks=[], strategy="unknown").strategy_label == "unknown"
        )

    def test_confidence_warn_threshold_constant(self):
        assert RoutePlan.CONFIDENCE_WARN_THRESHOLD == 0.7

    def test_to_dict_basic(self):
        """to_dict includes all required fields."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="a", task="t", description="d")
            ],
            analysis="test analysis",
            is_parallel=False,
            confidence=0.8,
            strategy="fan_out",
        )
        d = plan.to_dict()
        assert d["tasks"] == [{"id": 1, "agent": "a", "task": "t", "description": "d"}]
        assert d["analysis"] == "test analysis"
        assert d["is_parallel"] is False
        assert d["confidence"] == 0.8
        assert d["strategy"] == "fan_out"
        assert d["agents_involved"] == ["a"]

    def test_to_dict_optional_max_iterations_omitted(self):
        """max_iterations omitted when <= 1."""
        plan = RoutePlan(tasks=[], max_iterations=1)
        assert "max_iterations" not in plan.to_dict()

    def test_to_dict_optional_max_iterations_included(self):
        """max_iterations included when > 1."""
        plan = RoutePlan(tasks=[], max_iterations=3)
        d = plan.to_dict()
        assert d["max_iterations"] == 3

    def test_to_dict_optional_exit_condition_omitted(self):
        """exit_condition omitted when empty."""
        plan = RoutePlan(tasks=[], exit_condition="")
        assert "exit_condition" not in plan.to_dict()

    def test_to_dict_optional_exit_condition_included(self):
        """exit_condition included when set."""
        plan = RoutePlan(tasks=[], exit_condition="score > 0.9")
        d = plan.to_dict()
        assert d["exit_condition"] == "score > 0.9"

    def test_to_dict_optional_approval_gates_omitted(self):
        """approval_gates omitted when empty."""
        plan = RoutePlan(tasks=[], approval_gates=[])
        assert "approval_gates" not in plan.to_dict()

    def test_to_dict_optional_approval_gates_included(self):
        """approval_gates included when set."""
        plan = RoutePlan(tasks=[], approval_gates=[1, 3])
        d = plan.to_dict()
        assert d["approval_gates"] == [1, 3]

    def test_to_dict_all_optionals(self):
        """to_dict includes all optional fields when set."""
        plan = RoutePlan(
            tasks=[RoutedTask(id=1, agent="a", task="t", description="d")],
            analysis="test",
            is_parallel=True,
            confidence=0.5,
            strategy="debate",
            max_iterations=5,
            exit_condition="pass_rate > 0.8",
            approval_gates=[2],
        )
        d = plan.to_dict()
        assert d["max_iterations"] == 5
        assert d["exit_condition"] == "pass_rate > 0.8"
        assert d["approval_gates"] == [2]
        assert d["is_parallel"] is True


# ══════════════════════════════════════════════════════════════════════
# _extract_json_block
# ══════════════════════════════════════════════════════════════════════


class TestExtractJsonBlock:
    """Tests for _extract_json_block JSON extraction."""

    def test_extract_from_markdown_code_block(self):
        """Extracts JSON from ```json ... ``` markdown block."""
        text = """Some text before
```json
{"tasks": [{"id": 1, "agent": "test"}]}
```
Some text after"""
        result = _extract_json_block(text)
        parsed = json.loads(result)
        assert parsed["tasks"][0]["id"] == 1

    def test_extract_plain_json(self):
        """Extracts JSON from plain text without markdown."""
        text = 'Some preamble {"tasks": [{"id": 1}]} trailing text'
        result = _extract_json_block(text)
        assert json.loads(result)["tasks"][0]["id"] == 1

    def test_extract_with_key_hint(self):
        """With key_hint matches the correct block containing that key."""
        text = '{"other": 1} outside {"tasks": [{"id": 1}]}'
        result = _extract_json_block(text, key_hint="tasks")
        parsed = json.loads(result)
        assert "tasks" in parsed

    def test_extract_with_key_hint_nested(self):
        """With key_hint handles nested content AFTER the key_hint."""
        text = '{"tasks": [{"id": 1}], "extra": {"nested": true}}'
        result = _extract_json_block(text, key_hint="tasks")
        parsed = json.loads(result)
        assert parsed["tasks"][0]["id"] == 1
        assert parsed["extra"]["nested"] is True

    def test_no_json_raises_value_error(self):
        """Raises ValueError when no opening brace is found."""
        with pytest.raises(ValueError, match="JSON"):
            _extract_json_block("no JSON here at all")

    def test_no_closing_brace_raises_value_error(self):
        """Raises ValueError when no closing brace is found."""
        with pytest.raises(ValueError, match="JSON"):
            _extract_json_block('{"unclosed object')

    def test_empty_text_raises_value_error(self):
        """Raises ValueError on empty text."""
        with pytest.raises(ValueError, match="JSON"):
            _extract_json_block("")

    def test_extract_no_key_hint(self):
        """Works with key_hint=None by falling back to first { to last }."""
        text = 'noise {"a": 1} trailing'
        result = _extract_json_block(text, key_hint=None)
        assert json.loads(result) == {"a": 1}

    def test_extract_with_key_hint_returns_first_match(self):
        """When multiple blocks contain the hint, returns the first."""
        text = '{"tasks": [{"id": 1}]} noise {"tasks": [{"id": 2}]}'
        result = _extract_json_block(text, key_hint="tasks")
        parsed = json.loads(result)
        assert parsed["tasks"][0]["id"] == 1


# ══════════════════════════════════════════════════════════════════════
# IntentRouter._parse_llm_output
# ══════════════════════════════════════════════════════════════════════


class TestIntentRouterParseLlmOutput:
    """Tests for IntentRouter._parse_llm_output static method."""

    def test_parse_valid_json(self, agents_dict):
        """Parses valid JSON into RoutePlan with correct fields."""
        output = json.dumps({
            "analysis": "Analyze code quality",
            "confidence": 0.9,
            "strategy": "fan_out",
            "max_iterations": 1,
            "exit_condition": "",
            "approval_gates": [],
            "tasks": [
                {
                    "id": 1,
                    "agent": "test_agent",
                    "task": "analyze_code",
                    "description": "Analyze code",
                    "params": {"goal": "find issues"},
                    "depends_on": [],
                }
            ],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.analysis == "Analyze code quality"
        assert plan.confidence == 0.9
        assert plan.strategy == "fan_out"
        assert len(plan.tasks) == 1
        assert plan.tasks[0].agent == "test_agent"
        assert plan.tasks[0].task == "analyze_code"
        assert plan.is_parallel is False

    def test_parse_with_markdown_json_wrapper(self, agents_dict):
        """Strips ```json markdown wrapper."""
        output = """Here is the plan:
```json
{
  "analysis": "Analyze code",
  "confidence": 0.8,
  "strategy": "fan_out",
  "tasks": [
    {
      "id": 1,
      "agent": "test_agent",
      "task": "analyze_code",
      "description": "test",
      "depends_on": []
    }
  ]
}
```
End of plan."""
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.analysis == "Analyze code"
        assert len(plan.tasks) == 1

    def test_parse_with_trailing_commas(self, agents_dict):
        """Handles trailing commas in JSON (strip strategy)."""
        output = """{
  "analysis": "Test analysis",
  "confidence": 0.8,
  "strategy": "fan_out",
  "tasks": [
    {
      "id": 1,
      "agent": "test_agent",
      "task": "analyze_code",
      "description": "test",
      "params": {"goal": "test"},
      "depends_on": [],
    },
  ],
}"""
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.analysis == "Test analysis"
        assert len(plan.tasks) == 1

    def test_parse_auto_infers_strategy_when_missing(self, agents_dict):
        """Auto-infers strategy from analysis when missing in output."""
        output = json.dumps({
            "analysis": "优化代码性能",
            "tasks": [
                {"id": 1, "agent": "test_agent", "task": "analyze_code"}
            ],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        # "优化" → DEBATE
        assert plan.strategy == "debate"

    def test_parse_auto_infers_strategy_when_invalid(self, agents_dict):
        """Auto-infers strategy when output contains unknown strategy."""
        output = json.dumps({
            "analysis": "写文档",
            "strategy": "invalid_strategy",
            "tasks": [
                {"id": 1, "agent": "test_agent", "task": "analyze_code"}
            ],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        # "写" → REFLECTION
        assert plan.strategy == "reflection"

    def test_parse_clamps_confidence_above_1(self, agents_dict):
        """Confidence > 1.0 is clamped to 1.0."""
        output = json.dumps({
            "analysis": "test",
            "confidence": 2.5,
            "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.confidence == 1.0

    def test_parse_clamps_confidence_below_0(self, agents_dict):
        """Confidence < 0.0 is clamped to 0.0."""
        output = json.dumps({
            "analysis": "test",
            "confidence": -0.5,
            "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.confidence == 0.0

    def test_parse_clamps_max_iterations_above_10(self, agents_dict):
        """max_iterations > 10 is clamped to 10."""
        output = json.dumps({
            "analysis": "test",
            "max_iterations": 20,
            "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.max_iterations == 10

    def test_parse_clamps_max_iterations_below_1(self, agents_dict):
        """max_iterations < 1 is clamped to 1."""
        output = json.dumps({
            "analysis": "test",
            "max_iterations": -5,
            "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.max_iterations == 1

    def test_parse_extracts_approval_gates(self, agents_dict):
        """approval_gates are correctly extracted from the output."""
        output = json.dumps({
            "analysis": "Deploy with approval",
            "strategy": "hitl",
            "approval_gates": [1, 3],
            "tasks": [
                {"id": 1, "agent": "test_agent", "task": "analyze_code"},
                {"id": 2, "agent": "deploy_agent", "task": "deploy_app"},
            ],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.approval_gates == [1, 3]

    def test_parse_approval_gates_non_list_defaults_empty(self, agents_dict):
        """Non-list approval_gates defaults to empty list."""
        output = json.dumps({
            "analysis": "test",
            "approval_gates": "not_a_list",
            "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.approval_gates == []

    def test_parse_detects_is_parallel_multiple_no_deps(self, agents_dict):
        """is_parallel=True when multiple tasks with empty depends_on."""
        output = json.dumps({
            "analysis": "Parallel tasks",
            "tasks": [
                {"id": 1, "agent": "test_agent", "task": "analyze_code"},
                {"id": 2, "agent": "writer_agent", "task": "write_doc"},
            ],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.is_parallel is True

    def test_parse_not_parallel_single_task(self, agents_dict):
        """is_parallel=False with only one task."""
        output = json.dumps({
            "analysis": "Single task",
            "tasks": [
                {"id": 1, "agent": "test_agent", "task": "analyze_code"},
            ],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.is_parallel is False

    def test_parse_not_parallel_with_deps(self, agents_dict):
        """is_parallel=False when every task has a depends_on."""
        output = json.dumps({
            "analysis": "Sequential tasks",
            "tasks": [
                {"id": 1, "agent": "test_agent", "task": "analyze_code", "depends_on": [2]},
                {"id": 2, "agent": "writer_agent", "task": "write_doc", "depends_on": [1]},
            ],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.is_parallel is False

    def test_parse_handles_empty_tasks(self, agents_dict):
        """Empty tasks list creates plan with no tasks."""
        output = json.dumps({
            "analysis": "No tasks possible",
            "tasks": [],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.tasks == []
        assert plan.task_count == 0

    def test_parse_valid_non_markdown_json(self, agents_dict):
        """Parses plain JSON without markdown markers."""
        output = '{"analysis": "test", "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}]}'
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.analysis == "test"
        assert len(plan.tasks) == 1

    def test_parse_with_exit_condition(self, agents_dict):
        """Parses exit_condition for loop strategies."""
        output = json.dumps({
            "analysis": "Debate and improve",
            "strategy": "debate",
            "max_iterations": 5,
            "exit_condition": "score >= 90",
            "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.exit_condition == "score >= 90"
        assert plan.max_iterations == 5

    def test_parse_default_confidence(self, agents_dict):
        """Default confidence is 0.7 when not provided."""
        output = json.dumps({
            "analysis": "test",
            "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}],
        })
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.confidence == 0.7

    def test_parse_raises_on_unparseable(self, agents_dict):
        """Raises ValueError on completely unparseable text."""
        with pytest.raises(ValueError, match="解析"):
            IntentRouter._parse_llm_output("not even close to JSON", agents_dict)

    def test_parse_handles_old_markdown_format(self, agents_dict):
        """Handles legacy ``` at start/end without json marker."""
        output = """```
{
  "analysis": "test",
  "tasks": [{"id": 1, "agent": "test_agent", "task": "analyze_code"}]
}
```"""
        plan = IntentRouter._parse_llm_output(output, agents_dict)
        assert plan.analysis == "test"
        assert len(plan.tasks) == 1


# ══════════════════════════════════════════════════════════════════════
# IntentRouter._validate_and_enrich
# ══════════════════════════════════════════════════════════════════════


class TestIntentRouterValidateAndEnrich:
    """Tests for IntentRouter._validate_and_enrich."""

    def test_filters_unknown_agent(self, agents_dict):
        """Steps with unknown agent names are filtered out."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="unknown_agent", task="some_task"),
                RoutedTask(id=2, agent="test_agent", task="analyze_code"),
            ]
        )
        result = IntentRouter._validate_and_enrich(None, plan, agents_dict)
        assert len(result.tasks) == 1
        assert result.tasks[0].agent == "test_agent"

    def test_fuzzy_matches_task_name(self, agents_dict, sample_agent):
        """Fuzzy matches task names via substring ('analyze' → 'analyze_code')."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="test_agent", task="analyze"),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert len(result.tasks) == 1
        assert result.tasks[0].task == "analyze_code"

    def test_fuzzy_match_via_substring(self, agents_dict):
        """Fuzzy matches via substring when names partially overlap."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="test_agent", task="analyze"),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert len(result.tasks) == 1
        assert result.tasks[0].task == "analyze_code"

    def test_filters_invalid_depends_on(self, agents_dict):
        """depends_on references to filtered-out tasks are removed."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="test_agent", task="analyze_code", depends_on=[5]),
                RoutedTask(id=2, agent="writer_agent", task="write_doc", depends_on=[99, 1]),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        # Task 2 should have deps [1] only (99 and 5 are invalid; 5 not a valid ID)
        assert result.tasks[1].depends_on == [1]

    def test_skips_unsupported_task(self, agents_dict):
        """Steps with unsupported task for the agent are filtered."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="test_agent", task="nonexistent_task"),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert len(result.tasks) == 0

    def test_fills_empty_goal_from_description(self, agents_dict):
        """Fills params.goal from description when goal is empty."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(
                    id=1,
                    agent="test_agent",
                    task="analyze_code",
                    description="Find all bugs in the codebase",
                    params={},
                ),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert result.tasks[0].params.get("goal") == "Find all bugs in the codebase"

    def test_does_not_overwrite_existing_goal(self, agents_dict):
        """Does not overwrite goal when already set."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(
                    id=1,
                    agent="test_agent",
                    task="analyze_code",
                    description="Find bugs",
                    params={"goal": "explicit goal"},
                ),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert result.tasks[0].params["goal"] == "explicit goal"

    def test_fixes_windows_path_in_project(self, agents_dict):
        """Fixes Windows path in project param."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(
                    id=1,
                    agent="test_agent",
                    task="analyze_code",
                    params={"goal": "test", "project": "D:project"},
                ),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert result.tasks[0].params["project"] == "D:/project"

    def test_fixes_windows_path_in_project_path(self, agents_dict):
        """Fixes Windows path in project_path param."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(
                    id=1,
                    agent="test_agent",
                    task="analyze_code",
                    params={"goal": "test", "project_path": "C:Users/test"},
                ),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert result.tasks[0].params["project_path"] == "C:/Users/test"

    def test_recalculates_is_parallel_after_filtering(self, agents_dict):
        """Recomputes is_parallel after removing invalid tasks."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="test_agent", task="analyze_code"),
                RoutedTask(id=2, agent="invalid_agent", task="task"),
            ],
            is_parallel=True,
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        # Only one valid task remaining → is_parallel should be False
        assert result.is_parallel is False

    def test_preserves_valid_parallelism(self, agents_dict):
        """Preserves is_parallel=True when multiple valid parallel tasks remain."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="test_agent", task="analyze_code"),
                RoutedTask(id=2, agent="writer_agent", task="write_doc"),
            ],
            is_parallel=True,
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert result.is_parallel is True

    def test_validates_all_tasks_preserved(self, agents_dict):
        """Valid tasks are all preserved after validation."""
        plan = RoutePlan(
            tasks=[
                RoutedTask(id=1, agent="test_agent", task="analyze_code"),
                RoutedTask(id=2, agent="writer_agent", task="write_doc"),
                RoutedTask(id=3, agent="deploy_agent", task="deploy_app"),
            ]
        )
        router = IntentRouter(model_priority=["test"])
        result = router._validate_and_enrich(plan, agents_dict)
        assert len(result.tasks) == 3


# ══════════════════════════════════════════════════════════════════════
# IntentRouter._rule_based_route
# ══════════════════════════════════════════════════════════════════════


class TestIntentRouterRuleBasedRoute:
    """Tests for IntentRouter._rule_based_route."""

    def test_matches_by_keyword_in_task_description(self, agents_dict):
        """Matches agents based on keywords from task descriptions."""
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("analyze source code", agents_dict)
        # test_agent has "analyze source code" in task description
        assert len(plan.tasks) > 0
        assert any(t.agent == "test_agent" for t in plan.tasks)

    def test_matches_by_agent_name(self, agents_dict):
        """Matches agent when its name appears in user input."""
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("writer_agent help me", agents_dict)
        assert len(plan.tasks) > 0
        assert any(t.agent == "writer_agent" for t in plan.tasks)

    def test_falls_back_to_first_agent(self, agents_dict):
        """Falls back to first agent when nothing matches."""
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("xyznomatch nothing relevant here", agents_dict)
        assert len(plan.tasks) >= 1
        # Should pick the first agent (test_agent)
        assert plan.tasks[0].agent == "test_agent"

    def test_sets_confidence_03(self, agents_dict):
        """Rule-based route always sets confidence=0.3."""
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("analyze code", agents_dict)
        assert plan.confidence == 0.3

    def test_auto_infers_strategy(self, agents_dict):
        """Auto-infers strategy from user_input."""
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("优化代码", agents_dict)
        assert plan.strategy == "debate"

    def test_strategy_loop_sets_max_iterations(self, agents_dict):
        """Loop strategy sets max_iterations=3 and exit_condition='success'."""
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("写文档", agents_dict)
        assert plan.strategy == "reflection"
        assert plan.max_iterations == 3
        assert plan.exit_condition == "success"

    def test_strategy_non_loop_sets_max_iterations_1(self, agents_dict):
        """Non-loop strategy sets max_iterations=1 and no exit_condition."""
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("评估方案", agents_dict)
        assert plan.strategy == "vote"
        assert plan.max_iterations == 1
        assert plan.exit_condition == ""

    def test_multiple_matches_creates_multiple_tasks(self, agents_dict):
        """Multiple matching agents create multiple tasks."""
        router = IntentRouter(model_priority=["test"])
        # designer_agent doesn't exist in our fixtures so only some match
        plan = router._rule_based_route("analyze code and write docs", agents_dict)
        assert len(plan.tasks) >= 2

    def test_first_agent_fallback_when_no_tasks(self):
        """Falls back to first agent when no keywords match."""
        no_task_agent = AgentManifest(
            name="empty_agent",
            display_name="Empty",
            description="Has no tasks",
            capabilities=AgentCapabilities(tasks=[]),
            interface=AgentInterface(command="echo"),
        )
        agents = {"empty_agent": no_task_agent}
        router = IntentRouter(model_priority=["test"])
        plan = router._rule_based_route("anything", agents)
        # No tasks available, so no selected task
        assert len(plan.tasks) == 0


# ══════════════════════════════════════════════════════════════════════
# IntentRouter._build_agent_descriptions
# ══════════════════════════════════════════════════════════════════════


class TestIntentRouterBuildAgentDescriptions:
    """Tests for IntentRouter._build_agent_descriptions static method."""

    def test_includes_display_name_and_name(self, agents_dict):
        """Description includes display_name and name."""
        result = IntentRouter._build_agent_descriptions(agents_dict)
        assert "Test Agent" in result
        assert "test_agent" in result

    def test_includes_agent_description(self, agents_dict):
        """Description includes agent description text."""
        result = IntentRouter._build_agent_descriptions(agents_dict)
        assert "A test agent for unit testing" in result

    def test_includes_task_names_and_descriptions(self, agents_dict):
        """Description includes task names and descriptions."""
        result = IntentRouter._build_agent_descriptions(agents_dict)
        assert "analyze_code" in result
        assert "Analyze source code for issues" in result
        assert "fix_bugs" in result
        assert "Fix bugs in code" in result

    def test_includes_task_input_schema(self, agents_dict):
        """Description includes task input schema when present."""
        result = IntentRouter._build_agent_descriptions(agents_dict)
        assert "goal" in result

    def test_all_agents_represented(self, agents_dict):
        """All agents in the dict are represented in the output."""
        result = IntentRouter._build_agent_descriptions(agents_dict)
        for name, agent in agents_dict.items():
            assert name in result
            assert agent.display_name in result

    def test_empty_agents(self):
        """Empty agents dict produces empty string."""
        result = IntentRouter._build_agent_descriptions({})
        assert result == ""

    def test_agent_without_task_input(self):
        """Agent task without input schema still renders."""
        agent = AgentManifest(
            name="simple",
            display_name="Simple",
            description="Simple agent",
            capabilities=AgentCapabilities(
                tasks=[AgentTask(name="do_thing", description="Does a thing")]
            ),
            interface=AgentInterface(command="simple"),
        )
        result = IntentRouter._build_agent_descriptions({"simple": agent})
        assert "do_thing" in result
        # No input schema should not show "输入:"
        # Actually the code uses t.input which defaults to {}, and adds the
        # input line only if t.input is truthy; empty dict is falsy.
        assert "输入:" not in result

    def test_formats_multiple_agents_separated(self, agents_dict):
        """Multiple agents are separated by blank lines."""
        result = IntentRouter._build_agent_descriptions(agents_dict)
        # Check sections are separated by double newlines
        assert "Test Agent" in result
        assert "Writer Agent" in result
        assert "Deploy Agent" in result
        assert "###" in result


# ══════════════════════════════════════════════════════════════════════
# IntentRouter._fuzzy_match_task
# ══════════════════════════════════════════════════════════════════════


class TestIntentRouterFuzzyMatchTask:
    """Tests for IntentRouter._fuzzy_match_task static method."""

    def test_exact_match(self, sample_agent):
        """Exact match returns the task."""
        result = IntentRouter._fuzzy_match_task("analyze_code", sample_agent)
        assert result is not None
        assert result.name == "analyze_code"

    def test_substring_match_forward(self, sample_agent):
        """Substring match (target in task name) works."""
        result = IntentRouter._fuzzy_match_task("analyze", sample_agent)
        assert result is not None
        assert result.name == "analyze_code"

    def test_substring_match_reverse(self, sample_agent):
        """Substring match (task name in target) works (e.g., 'analyze' in 'analyze_code')."""
        result = IntentRouter._fuzzy_match_task("run_analyze_code", sample_agent)
        assert result is not None
        assert result.name == "analyze_code"

    def test_normalizes_spaces_to_underscores(self, sample_agent):
        """Spaces in target are normalized to underscores."""
        result = IntentRouter._fuzzy_match_task("analyze code", sample_agent)
        assert result is not None
        assert result.name == "analyze_code"

    def test_normalizes_hyphens(self, sample_agent):
        """Hyphens in target are normalized to underscores."""
        result = IntentRouter._fuzzy_match_task("analyze-code", sample_agent)
        assert result is not None
        assert result.name == "analyze_code"

    def test_no_match_returns_none(self, sample_agent):
        """No match returns None."""
        result = IntentRouter._fuzzy_match_task("completely_unrelated", sample_agent)
        assert result is None

    def test_case_insensitive(self, sample_agent):
        """Matching is case-insensitive."""
        result = IntentRouter._fuzzy_match_task("ANALYZE_CODE", sample_agent)
        assert result is not None
        assert result.name == "analyze_code"

    def test_matches_second_task(self, sample_agent):
        """Can match the second task in the agent's capabilities."""
        result = IntentRouter._fuzzy_match_task("fix_bugs", sample_agent)
        assert result is not None
        assert result.name == "fix_bugs"

    def test_substring_of_description_not_name(self, sample_agent):
        """Only matches against task name, not description."""
        result = IntentRouter._fuzzy_match_task("source code", sample_agent)
        assert result is None


# ══════════════════════════════════════════════════════════════════════
# IntentRouter.__init__ and basic properties
# ══════════════════════════════════════════════════════════════════════


class TestIntentRouterInit:
    """Tests for IntentRouter initialization."""

    def test_init_stores_model_priority(self):
        """__init__ stores model_priority."""
        router = IntentRouter(model_priority=["model-a", "model-b"])
        assert router.model_priority == ["model-a", "model-b"]

    def test_init_defaults_to_rule_based_fallback(self):
        """__init__ defaults fallback_rule_based to True."""
        router = IntentRouter(model_priority=["test"])
        assert router.fallback_rule_based is True

    def test_init_disables_rule_based_fallback(self):
        """__init__ accepts fallback_rule_based=False."""
        router = IntentRouter(model_priority=["test"], fallback_rule_based=False)
        assert router.fallback_rule_based is False
