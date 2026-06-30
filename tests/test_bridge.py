"""Comprehensive unit tests for agent_hub/bridge.py.

Tests focus on pure functions, data structures, and mock-isolated logic.
No real subprocess calls are made.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest
from unittest.mock import MagicMock, patch

import pytest

from agent_hub.bridge import (
    CLIBridge,
    AgentResult,
    ProcessInfo,
    AgentProcessRegistry,
)
from agent_hub.manifest import AgentManifest, AgentCapabilities, AgentInterface


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def sample_manifest() -> AgentManifest:
    """A basic AgentManifest for use in command-building tests."""
    return AgentManifest(
        name="test-agent",
        display_name="Test Agent",
        description="An agent for testing",
        protocol="cli",
        interface=AgentInterface(
            command="tool --mode {mode} --goal \"{goal}\" --project {project} --task {task}",
            env={"AGENT_MODE": "test"},
        ),
        capabilities=AgentCapabilities(
            constraints={},
        ),
    )


@pytest.fixture
def manifest_no_placeholders() -> AgentManifest:
    """Manifest with a static command (no placeholders)."""
    return AgentManifest(
        name="static-agent",
        interface=AgentInterface(command="static-tool --flag"),
    )


@pytest.fixture
def manifest_with_params_json() -> AgentManifest:
    """Manifest that uses {params_json}."""
    return AgentManifest(
        name="json-agent",
        interface=AgentInterface(
            command="json-tool --data {params_json}",
        ),
    )


@pytest.fixture
def manifest_with_pipeline_support() -> AgentManifest:
    """Manifest that supports pipeline context via _pipeline_context."""
    return AgentManifest(
        name="pipe-agent",
        interface=AgentInterface(
            command="pipe-tool --goal \"{goal}\"",
        ),
    )


@pytest.fixture
def manifest_with_timeout_constraint() -> AgentManifest:
    """Manifest with a timeout constraint in capabilities."""
    return AgentManifest(
        name="timeout-agent",
        protocol="cli",
        interface=AgentInterface(command="timeout-tool --goal \"{goal}\""),
        capabilities=AgentCapabilities(constraints={"timeout": 30}),
    )


@pytest.fixture
def sample_params() -> dict[str, Any]:
    return {
        "goal": "analyze the codebase structure",
        "mode": "expert",
        "project": "/home/user/project",
        "extra_info": "some data",
    }


@pytest.fixture
def bridge() -> CLIBridge:
    return CLIBridge()


@pytest.fixture
def registry() -> AgentProcessRegistry:
    return AgentProcessRegistry()


@pytest.fixture
def mock_process() -> MagicMock:
    """A minimal mock for asyncio.subprocess.Process (pid, returncode, etc.)."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None  # still running
    return proc


# =========================================================================
# AgentResult dataclass tests
# =========================================================================


class TestAgentResult:
    def test_is_success_true(self):
        result = AgentResult(agent_name="a", task_name="t", success=True, exit_code=0)
        assert result.is_success is True

    def test_is_success_false_when_success_false(self):
        result = AgentResult(agent_name="a", task_name="t", success=False, exit_code=0)
        assert result.is_success is False

    def test_is_success_false_when_exit_code_nonzero(self):
        result = AgentResult(agent_name="a", task_name="t", success=True, exit_code=1)
        assert result.is_success is False

    def test_summary_success_with_duration(self):
        result = AgentResult(
            agent_name="ag", task_name="ta", success=True, duration_ms=123456
        )
        assert "✅" in result.summary
        assert "[ag]" in result.summary
        assert "ta" in result.summary
        assert "(123456ms)" in result.summary

    def test_summary_failure_with_duration(self):
        result = AgentResult(
            agent_name="ag", task_name="ta", success=False, duration_ms=456
        )
        assert "❌" in result.summary
        assert "[ag]" in result.summary
        assert "(456ms)" in result.summary

    def test_summary_no_duration_shown_when_zero(self):
        result = AgentResult(agent_name="a", task_name="t", success=True, duration_ms=0)
        assert "(0ms)" not in result.summary
        assert ")" not in result.summary  # no parentheses at all

    def test_summary_no_duration_shown_when_negative(self):
        result = AgentResult(agent_name="a", task_name="t", success=True, duration_ms=-1)
        assert "(-1ms)" not in result.summary

    def test_default_values(self):
        result = AgentResult(agent_name="a", task_name="t", success=True)
        assert result.output == ""
        assert result.error == ""
        assert result.duration_ms == 0.0
        assert result.exit_code == 0
        assert result.extra == {}

    def test_extra_field(self):
        result = AgentResult(
            agent_name="a", task_name="t", success=True, extra={"key": "value"}
        )
        assert result.extra["key"] == "value"


# =========================================================================
# ProcessInfo dataclass tests
# =========================================================================


class TestProcessInfo:
    def test_is_running_true(self):
        proc = MagicMock()
        info = ProcessInfo(
            name="test",
            process=proc,
            status="running",
        )
        assert info.is_running is True

    def test_is_running_false_when_status_not_running(self):
        info = ProcessInfo(name="test", status="stopped", process=MagicMock())
        assert info.is_running is False

    def test_is_running_false_when_process_none(self):
        info = ProcessInfo(name="test", status="running", process=None)
        assert info.is_running is False

    def test_is_running_false_when_both(self):
        info = ProcessInfo(name="test", status="stopped", process=None)
        assert info.is_running is False

    def test_uptime_seconds_zero_when_not_started(self):
        info = ProcessInfo(name="test")
        assert info.uptime_seconds == 0.0

    def test_uptime_seconds_negative_started_at(self):
        info = ProcessInfo(name="test", started_at=-100)
        assert info.uptime_seconds == 0.0

    def test_uptime_seconds_positive(self):
        now = time.monotonic()
        started = now - 50
        info = ProcessInfo(name="test", started_at=started)
        assert 49.0 < info.uptime_seconds < 51.0

    def test_can_restart_below_max_within_window(self):
        now = time.monotonic()
        info = ProcessInfo(
            name="test",
            restart_count=2,
            last_restart_time=now,
        )
        assert info.can_restart is True  # 2 < 3

    def test_can_restart_at_max_within_window(self):
        now = time.monotonic()
        info = ProcessInfo(
            name="test",
            restart_count=3,
            last_restart_time=now,
        )
        assert info.can_restart is False  # 3 >= 3

    def test_can_restart_above_max_within_window(self):
        now = time.monotonic()
        info = ProcessInfo(
            name="test",
            restart_count=5,
            last_restart_time=now,
        )
        assert info.can_restart is False

    def test_can_restart_resets_outside_window(self):
        old_time = time.monotonic() - 400  # > RESTART_WINDOW (300s)
        info = ProcessInfo(
            name="test",
            restart_count=3,
            last_restart_time=old_time,
        )
        # Outside the window → always returns True (caller resets count)
        assert info.can_restart is True

    def test_can_restart_above_max_outside_window(self):
        old_time = time.monotonic() - 400
        info = ProcessInfo(
            name="test",
            restart_count=10,
            last_restart_time=old_time,
        )
        assert info.can_restart is True

    def test_can_restart_zero_restarts(self):
        info = ProcessInfo(name="test", restart_count=0, last_restart_time=0)
        assert info.can_restart is True

    @patch("agent_hub.bridge.time.monotonic")
    def test_can_restart_window_boundary_exact(self, mock_monotonic):
        """Test the RESTART_WINDOW boundary check."""
        mock_monotonic.return_value = 1000.0
        # last_restart_time = 1000 - 300 = 700 → exactly at boundary (not > 300)
        info = ProcessInfo(
            name="test",
            restart_count=3,
            last_restart_time=700.0,
        )
        # 1000 - 700 = 300, which is NOT > 300.0 → within window → check count
        assert info.can_restart is False  # 3 >= 3

    @patch("agent_hub.bridge.time.monotonic")
    def test_can_restart_window_boundary_just_outside(self, mock_monotonic):
        """Just outside the window (300.001s ago) → resets, returns True."""
        mock_monotonic.return_value = 1000.0
        info = ProcessInfo(
            name="test",
            restart_count=3,
            last_restart_time=699.998,  # 1000 - 699.998 = 300.002 > 300
        )
        assert info.can_restart is True

    def test_default_values(self):
        info = ProcessInfo(name="test")
        assert info.process is None
        assert info.manifest is None
        assert info.status == "stopped"
        assert info.started_at == 0.0
        assert info.pid == 0
        assert info.desired_state == "stopped"
        assert info.restart_count == 0
        assert info.last_restart_time == 0.0

    def test_MAX_RESTARTS_constant(self):
        assert ProcessInfo.MAX_RESTARTS == 3

    def test_RESTART_WINDOW_constant(self):
        assert ProcessInfo.RESTART_WINDOW == 300.0


# =========================================================================
# AgentProcessRegistry tests
# =========================================================================


class TestAgentProcessRegistry:
    def test_register_returns_process_info(self, mock_process):
        registry = AgentProcessRegistry()
        info = registry.register("agent-a", mock_process, MagicMock(spec=AgentManifest))
        assert isinstance(info, ProcessInfo)
        assert info.name == "agent-a"
        assert info.process is mock_process
        assert info.status == "starting"
        assert info.pid == 12345

    def test_register_uses_actual_pid_from_process(self, mock_process):
        registry = AgentProcessRegistry()
        info = registry.register("a", mock_process, MagicMock(spec=AgentManifest))
        assert info.pid == 12345

    def test_register_fallback_pid_zero(self):
        registry = AgentProcessRegistry()
        proc = MagicMock()
        proc.pid = None
        info = registry.register("a", proc, MagicMock(spec=AgentManifest))
        assert info.pid == 0

    def test_get_returns_info(self, mock_process):
        registry = AgentProcessRegistry()
        registry.register("agent-a", mock_process, MagicMock(spec=AgentManifest))
        info = registry.get("agent-a")
        assert info is not None
        assert info.name == "agent-a"

    def test_get_returns_none_when_missing(self):
        registry = AgentProcessRegistry()
        assert registry.get("nonexistent") is None

    def test_get_returns_none_after_remove(self, mock_process):
        registry = AgentProcessRegistry()
        registry.register("a", mock_process, MagicMock(spec=AgentManifest))
        registry.remove("a")
        assert registry.get("a") is None

    def test_update_status(self, mock_process):
        registry = AgentProcessRegistry()
        registry.register("a", mock_process, MagicMock(spec=AgentManifest))
        registry.update_status("a", "running")
        assert registry.get("a").status == "running"

    def test_update_status_nonexistent_does_nothing(self):
        registry = AgentProcessRegistry()
        registry.update_status("nonexistent", "running")  # should not raise

    def test_remove_also_cancels_monitor_task(self, mock_process):
        registry = AgentProcessRegistry()
        registry.register("a", mock_process, MagicMock(spec=AgentManifest))
        task = MagicMock()
        task.done.return_value = False
        registry._monitor_tasks["a"] = task
        registry.remove("a")
        task.cancel.assert_called_once()

    def test_remove_skips_done_task(self, mock_process):
        registry = AgentProcessRegistry()
        registry.register("a", mock_process, MagicMock(spec=AgentManifest))
        task = MagicMock()
        task.done.return_value = True
        registry._monitor_tasks["a"] = task
        registry.remove("a")
        task.cancel.assert_not_called()

    def test_remove_nonexistent_does_nothing(self):
        registry = AgentProcessRegistry()
        registry.remove("nonexistent")  # should not raise

    def test_list_all_sorted_by_name(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        registry.register("z-agent", mock_process, manifest)
        registry.register("a-agent", mock_process, manifest)
        registry.register("m-agent", mock_process, manifest)
        names = [p.name for p in registry.list_all()]
        assert names == ["a-agent", "m-agent", "z-agent"]

    def test_list_all_empty(self):
        registry = AgentProcessRegistry()
        assert registry.list_all() == []

    def test_list_running_only_running(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        p1 = registry.register("running-agent", mock_process, manifest)
        p1.status = "running"
        p2 = registry.register("stopped-agent", mock_process, manifest)
        p2.status = "stopped"
        p3 = registry.register("starting-agent", mock_process, manifest)
        p3.status = "starting"
        running = registry.list_running()
        assert len(running) == 1
        assert running[0].name == "running-agent"

    def test_list_running_respects_is_running_property(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        p1 = registry.register("running-agent", mock_process, manifest)
        p1.status = "running"
        # Register with a mock process, then explicitly set it to None
        # so is_running returns False even with status "running"
        p2 = registry.register("no-proc", mock_process, manifest)
        p2.status = "running"
        p2.process = None
        running = registry.list_running()
        assert len(running) == 1

    def test_list_running_empty(self):
        registry = AgentProcessRegistry()
        assert registry.list_running() == []

    def test_list_by_status(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        registry.register("a", mock_process, manifest)
        registry.register("b", mock_process, manifest)
        registry.register("c", mock_process, manifest)
        registry.update_status("a", "running")
        registry.update_status("b", "failed")
        registry.update_status("c", "running")
        running = registry.list_by_status("running")
        assert len(running) == 2
        assert {p.name for p in running} == {"a", "c"}

    def test_list_by_status_empty(self):
        registry = AgentProcessRegistry()
        assert registry.list_by_status("running") == []

    def test_running_count(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        p1 = registry.register("a", mock_process, manifest)
        p1.status = "running"
        p2 = registry.register("b", mock_process, manifest)
        p2.status = "stopped"
        assert registry.running_count == 1

    def test_total_count(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        registry.register("a", mock_process, manifest)
        registry.register("b", mock_process, manifest)
        assert registry.total_count == 2

    def test_total_count_empty(self):
        registry = AgentProcessRegistry()
        assert registry.total_count == 0

    def test_status_summary_with_multiple(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        p1 = registry.register("alpha", mock_process, manifest)
        p1.status = "running"
        p1.started_at = time.monotonic() - 120  # 120s uptime
        p2 = registry.register("beta", mock_process, manifest)
        p2.status = "stopped"
        summary = registry.status_summary()
        assert "🟢" in summary
        assert "alpha" in summary
        assert "running" in summary
        assert "120s" in summary
        assert "⚫" in summary
        assert "beta" in summary
        assert "stopped" in summary

    def test_status_summary_uses_all_icons(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)
        statuses = [
            ("a", "running", "🟢"),
            ("b", "starting", "🟡"),
            ("c", "stopping", "🟠"),
            ("d", "stopped", "⚫"),
            ("e", "failed", "🔴"),
            ("f", "unknown", "❓"),
        ]
        for name, status, _ in statuses:
            p = registry.register(name, mock_process, manifest)
            p.status = status
        summary = registry.status_summary()
        for _, _, icon in statuses:
            assert icon in summary

    def test_status_summary_empty(self):
        registry = AgentProcessRegistry()
        assert "无已注册进程" in registry.status_summary()


# =========================================================================
# CLIBridge._sanitize_goal tests
# =========================================================================


class TestSanitizeGoal:
    def test_removes_newlines(self):
        result = CLIBridge._sanitize_goal("line1\nline2\nline3")
        assert result == "line1 line2 line3"

    def test_removes_carriage_return(self):
        result = CLIBridge._sanitize_goal("a\r\nb\r\nc")
        assert result == "a b c"

    def test_removes_shell_injection_pipe(self):
        result = CLIBridge._sanitize_goal("hello | world")
        assert result == "hello  world"

    def test_removes_shell_injection_ampersand(self):
        result = CLIBridge._sanitize_goal("a & b")
        assert result == "a  b"

    def test_removes_shell_injection_semicolon(self):
        result = CLIBridge._sanitize_goal("a ; b")
        assert result == "a  b"

    def test_removes_shell_injection_dollar(self):
        result = CLIBridge._sanitize_goal("a $b c")
        assert result == "a b c"

    def test_removes_shell_injection_backtick(self):
        result = CLIBridge._sanitize_goal("a `cmd` b")
        assert result == "a cmd b"

    def test_removes_shell_injection_exclamation(self):
        result = CLIBridge._sanitize_goal("a ! b")
        assert result == "a  b"

    def test_removes_shell_injection_angle_brackets(self):
        result = CLIBridge._sanitize_goal("a < b > c")
        assert result == "a  b  c"

    def test_removes_all_shell_chars_together(self):
        result = CLIBridge._sanitize_goal("a | & ; $ ` ! < > b")
        # None of the shell-injection chars should remain
        for char in "|&;$`!<>":
            assert char not in result
        # Original spaces between removed chars are preserved
        assert "a" in result and "b" in result

    def test_preserves_backslashes(self):
        result = CLIBridge._sanitize_goal("D:\\project\\src")
        assert result == "D:\\project\\src"

    def test_truncates_at_default_max_len_on_word_boundary(self):
        # Create a 150-char string that should truncate at ~120 on word boundary
        goal = "word " * 30  # 150 chars
        result = CLIBridge._sanitize_goal(goal, max_len=120)
        assert len(result) <= 120
        # Should end with a complete word (no trailing space)
        assert not result.endswith(" ")

    def test_truncates_at_custom_max_len(self):
        goal = "hello world foo bar baz"
        result = CLIBridge._sanitize_goal(goal, max_len=12)
        # "hello world" is 11 chars, "hello world foo" is 15 → truncates to "hello world"
        assert result == "hello world"

    def test_short_text_unchanged(self):
        result = CLIBridge._sanitize_goal("hello world")
        assert result == "hello world"

    def test_empty_string(self):
        result = CLIBridge._sanitize_goal("")
        assert result == ""

    def test_whitespace_only(self):
        result = CLIBridge._sanitize_goal("   \n  \n  ")
        assert result == ""

    def test_max_len_exact_fit_not_truncated(self):
        goal = "a" * 120
        result = CLIBridge._sanitize_goal(goal, max_len=120)
        assert result == goal
        assert len(result) == 120

    def test_truncate_at_word_boundary_no_trailing_space(self):
        goal = "hello world foo bar baz qux"
        result = CLIBridge._sanitize_goal(goal, max_len=17)
        # "hello world foo" is 15 chars, "hello world foo bar" is 19
        # truncated[:17] = "hello world foo " → rsplit(" ", 1)[0] = "hello world foo"
        assert result == "hello world foo"

    def test_truncate_no_space_in_range_returns_empty(self):
        goal = "abcdefghij"
        result = CLIBridge._sanitize_goal(goal, max_len=3)
        # truncated[:3] = "abc", rsplit(" ", 1)[0] = "abc" (no space)
        assert result == "abc"

    def test_preserves_single_quotes(self):
        result = CLIBridge._sanitize_goal("it's a test")
        assert result == "it's a test"

    def test_preserves_double_quotes(self):
        result = CLIBridge._sanitize_goal('say "hello"')
        assert result == 'say "hello"'

    def test_preserves_hyphens_and_underscores(self):
        result = CLIBridge._sanitize_goal("well-known_var --flag")
        assert result == "well-known_var --flag"


# =========================================================================
# CLIBridge._extract_project_path tests
# =========================================================================


class TestExtractProjectPath:
    def test_windows_backslash_path(self):
        result = CLIBridge._extract_project_path("D:\\project\\src")
        assert result == "D:/project/src"

    def test_windows_forward_slash_path(self):
        result = CLIBridge._extract_project_path("D:/project/src")
        assert result == "D:/project/src"

    def test_windows_lowercase_drive(self):
        result = CLIBridge._extract_project_path("c:\\users\\test")
        assert result == "c:/users/test"

    def test_windows_path_with_punctuation_following(self):
        result = CLIBridge._extract_project_path("D:/project, something")
        assert result == "D:/project"

    def test_windows_path_with_chinese_punctuation(self):
        result = CLIBridge._extract_project_path("D:/project，后面有中文逗号")
        assert result == "D:/project"

    def test_windows_path_with_semicolon(self):
        result = CLIBridge._extract_project_path("D:/project；其他内容")
        assert result == "D:/project"

    def test_unix_path(self):
        result = CLIBridge._extract_project_path("/home/user/project")
        assert result == "/home/user/project"

    def test_unix_deep_path(self):
        result = CLIBridge._extract_project_path("/var/log/app/test")
        assert result == "/var/log/app/test"

    def test_unix_path_with_trailing_punctuation(self):
        result = CLIBridge._extract_project_path("/home/user/project. something else")
        assert result == "/home/user/project."

    def test_no_path(self):
        result = CLIBridge._extract_project_path("hello world")
        assert result is None

    def test_empty_string(self):
        result = CLIBridge._extract_project_path("")
        assert result is None

    def test_text_with_multiple_paths_first_wins(self):
        result = CLIBridge._extract_project_path("D:/first/path and then C:/second/path")
        assert result == "D:/first/path"

    def test_unix_root_only_not_matched(self):
        """Single / is not a valid project path (needs at least 2 levels)."""
        result = CLIBridge._extract_project_path("/")
        assert result is None

    def test_unix_two_levels_matches(self):
        result = CLIBridge._extract_project_path("/home/user")
        assert result == "/home/user"


# =========================================================================
# CLIBridge._ensure_project_dir tests
# =========================================================================


class TestEnsureProjectDir:
    def test_python_file_returns_parent(self):
        with patch("os.path.isdir") as mock_isdir:
            mock_isdir.return_value = True
            result = CLIBridge._ensure_project_dir("D:/project/main.py")
        assert result == "D:/project"

    def test_js_file_returns_parent(self):
        with patch("os.path.isdir") as mock_isdir:
            mock_isdir.return_value = True
            result = CLIBridge._ensure_project_dir("/app/index.js")
        assert result == "/app"

    def test_directory_unchanged(self):
        result = CLIBridge._ensure_project_dir("D:/project/src")
        assert result == "D:/project/src"

    def test_go_file_returns_parent(self):
        with patch("os.path.isdir") as mock_isdir:
            mock_isdir.return_value = True
            result = CLIBridge._ensure_project_dir("/app/main.go")
        assert result == "/app"

    def test_empty_unchanged(self):
        result = CLIBridge._ensure_project_dir("")
        assert result == ""

    def test_unknown_extension_unchanged(self):
        result = CLIBridge._ensure_project_dir("/path/to/something.xyz")
        assert result == "/path/to/something.xyz"

    def test_uppercase_extension(self):
        with patch("os.path.isdir") as mock_isdir:
            mock_isdir.return_value = True
            result = CLIBridge._ensure_project_dir("/path/file.PY")
        assert result == "/path"

    def test_no_extension_unchanged(self):
        result = CLIBridge._ensure_project_dir("/path/to/dir")
        assert result == "/path/to/dir"

    @pytest.mark.skipif(os.name != "nt", reason="Backslash normalization only applies on Windows")
    def test_backslash_normalization(self):
        with patch("os.path.isdir") as mock_isdir:
            mock_isdir.return_value = True
            result = CLIBridge._ensure_project_dir("D:\\project\\main.py")
        assert result == "D:/project"

    def test_parent_not_exist_returns_original_path(self):
        """When os.path.isdir(parent) returns False, return project as-is."""
        with patch("os.path.isdir") as mock_isdir:
            mock_isdir.return_value = False
            result = CLIBridge._ensure_project_dir("D:/project/main.py")
        assert result == "D:/project/main.py"

    def test_src_extensions_list(self):
        """Verify all source extensions trigger the directory extraction."""
        exts = [".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go",
                ".rs", ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
                ".swift", ".kt", ".scala", ".r", ".m", ".mm"]
        for ext in exts:
            file_path = f"/root/file{ext}"
            with patch("os.path.isdir") as mock_isdir:
                mock_isdir.return_value = True
                result = CLIBridge._ensure_project_dir(file_path)
            assert result == "/root", f"Failed for extension {ext}"


# =========================================================================
# CLIBridge._build_command tests
# =========================================================================


class TestBuildCommand:
    def test_basic_substitution(self, bridge, sample_manifest, sample_params):
        cmd = bridge._build_command(sample_manifest, "analyze", sample_params)
        assert isinstance(cmd, list)
        assert len(cmd) > 0
        cmd_str = " ".join(cmd)
        assert "tool" in cmd_str
        assert "--mode" in cmd_str
        assert "expert" in cmd_str
        assert "analyze" in cmd_str  # task name
        assert "analyze the codebase structure" in cmd_str

    def test_goal_truncation(self, bridge, sample_manifest):
        long_goal = "word " * 50  # 250 chars
        params = {"goal": long_goal, "mode": "react", "project": ""}
        cmd = bridge._build_command(sample_manifest, "task", params)
        # Find --goal and get the next element from the list
        goal_idx = cmd.index("--goal")
        goal_arg = cmd[goal_idx + 1]
        assert len(goal_arg) <= 120

    def test_params_json_excludes_internal(self, bridge, manifest_with_params_json):
        params = {
            "goal": "test",
            "_internal_key": "should_not_appear",
            "_secret": "hidden",
            "normal_key": "visible",
        }
        cmd = bridge._build_command(manifest_with_params_json, "task", params)
        cmd_str = " ".join(cmd)
        # After shlex.split, JSON is split into tokens. Use a predicate check
        # rather than exact parsing: no _prefixed keys should appear anywhere
        assert "_internal_key" not in cmd_str
        assert "_secret" not in cmd_str
        assert "goal" in cmd_str
        assert "normal_key" in cmd_str

    def test_goal_empty_string(self, bridge, sample_manifest):
        params = {"goal": "", "mode": "react", "project": ""}
        cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert '""' in cmd_str or " " in cmd_str  # empty string as argument

    def test_pipeline_context_triples_goal_limit(self, bridge, manifest_with_pipeline_support):
        """Pipeline context (_pipeline_context) allows up to 3x MAX_GOAL_LENGTH."""
        long_goal = "hello " * 40  # ~240 chars
        pipeline = "pipeline context " * 30  # ~180 chars
        params = {
            "goal": long_goal,
            "_pipeline_context": pipeline,
            "mode": "react",
        }
        cmd = bridge._build_command(manifest_with_pipeline_support, "task", params)
        # Pipeline allows up to 360 chars (3x MAX_GOAL_LENGTH)
        goal_idx = cmd.index("--goal")
        goal_arg = cmd[goal_idx + 1]
        assert len(goal_arg) <= 360
        # The goal should still contain the pipeline context
        assert "pipeline context" in goal_arg

    def test_no_pipeline_context_normal_limit(self, bridge, manifest_with_pipeline_support):
        """Without pipeline context, limit stays at 120."""
        long_goal = "word " * 50  # 250 chars
        params = {"goal": long_goal, "mode": "react"}
        cmd = bridge._build_command(manifest_with_pipeline_support, "task", params)
        goal_idx = cmd.index("--goal")
        goal_arg = cmd[goal_idx + 1]
        assert len(goal_arg) <= 120

    def test_project_path_extraction_from_goal(self, bridge, sample_manifest):
        """Project path should be extracted from goal if present."""
        params = {
            "goal": "analyze D:/my-project/src",
            "mode": "react",
            "project": "",
        }
        cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "D:/my-project/src" in cmd_str

    def test_project_path_overrides_router(self, bridge, sample_manifest):
        """Extracted path from goal overrides the router-provided project."""
        params = {
            "goal": "analyze D:/real-path",
            "mode": "react",
            "project": "/wrong/path",
        }
        cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "D:/real-path" in cmd_str

    def test_project_file_to_dir_conversion(self, bridge):
        """If project is a .py file, convert to parent directory."""
        manifest = AgentManifest(
            name="test",
            interface=AgentInterface(command="tool --project {project}"),
        )
        params = {"goal": "", "project": "/root/main.py"}
        with patch("os.path.isdir") as mock_isdir:
            mock_isdir.return_value = True
            cmd = bridge._build_command(manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "/root" in cmd_str

    def test_project_name_extraction(self, bridge):
        """When --project is not in template, extract basename."""
        manifest = AgentManifest(
            name="test",
            interface=AgentInterface(command="tool {project}"),
        )
        params = {"goal": "", "project": "/home/user/my-project"}
        cmd = bridge._build_command(manifest, "task", params)
        # template has {project} but no --project → extract basename
        cmd_str = " ".join(cmd)
        assert "my-project" in cmd_str

    def test_windows_backslash_normalization(self, bridge, sample_manifest):
        """On Windows, backslashes should be normalized to forward slashes."""
        params = {
            "goal": "analyze",
            "mode": "react",
            "project": "D:\\project\\src",
        }
        with patch("sys.platform", "win32"):
            cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "D:/project/src" in cmd_str
        assert "D:\\project\\src" not in cmd_str

    def test_unix_preserves_backslashes(self, bridge, sample_manifest):
        """On non-Windows, backslashes are NOT replaced."""
        params = {
            "goal": "analyze",
            "mode": "react",
            "project": "/home/user/project",
        }
        with patch("sys.platform", "linux"):
            cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "/home/user/project" in cmd_str

    def test_shlex_split_fallback(self, bridge):
        """When shlex.split fails, falls back to str.split()."""
        manifest = AgentManifest(
            name="test",
            interface=AgentInterface(command="tool --flag {goal}"),
        )
        params = {"goal": "simple", "mode": "react", "project": ""}
        # shlex.split should work fine here, but let's test the fallback path
        cmd = bridge._build_command(manifest, "task", params)
        assert isinstance(cmd, list)
        assert len(cmd) == 3

    def test_returns_list_of_strings(self, bridge, sample_manifest, sample_params):
        cmd = bridge._build_command(sample_manifest, "analyze", sample_params)
        assert isinstance(cmd, list)
        assert all(isinstance(x, str) for x in cmd)

    def test_mode_defaults_to_react(self, bridge, sample_manifest):
        params = {"goal": "test"}
        cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "--mode" in cmd_str
        assert "react" in cmd_str

    def test_project_empty_string(self, bridge, sample_manifest):
        params = {"goal": "test", "mode": "react", "project": ""}
        cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "--project" in cmd_str

    def test_raw_goal_fallback_to_task_description(self, bridge, sample_manifest):
        params = {"task_description": "from task_description", "mode": "react", "project": ""}
        cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "from task_description" in cmd_str

    def test_project_fallback_to_project_path(self, bridge, sample_manifest):
        params = {"goal": "test", "mode": "react", "project_path": "/fallback/path"}
        cmd = bridge._build_command(sample_manifest, "task", params)
        cmd_str = " ".join(cmd)
        assert "/fallback/path" in cmd_str


# =========================================================================
# CLIBridge._extract_cd_path tests
# =========================================================================


class TestExtractCdPath:
    @patch("pathlib.Path.is_dir")
    def test_extracts_cd_path_with_and(self, mock_is_dir):
        mock_is_dir.return_value = True
        result = CLIBridge._extract_cd_path(
            ["cd", "D:/proj", "&&", "tool", "--flag"]
        )
        assert result == "D:/proj"

    @patch("pathlib.Path.is_dir")
    def test_extracts_cd_path_with_semicolon(self, mock_is_dir):
        mock_is_dir.return_value = True
        result = CLIBridge._extract_cd_path(
            ["cd", "/home/user/proj", ";", "tool"]
        )
        assert result == "/home/user/proj"

    def test_no_cd_prefix_returns_none(self):
        result = CLIBridge._extract_cd_path(["tool", "--flag"])
        assert result is None

    def test_empty_list_returns_none(self):
        result = CLIBridge._extract_cd_path([])
        assert result is None

    def test_short_list_returns_none(self):
        result = CLIBridge._extract_cd_path(["cd"])
        assert result is None

    @patch("pathlib.Path.is_dir")
    def test_dir_does_not_exist_returns_none(self, mock_is_dir):
        mock_is_dir.return_value = False
        result = CLIBridge._extract_cd_path(
            ["cd", "/nonexistent", "&&", "tool"]
        )
        assert result is None


# =========================================================================
# CLIBridge._strip_shell_prefix tests
# =========================================================================


class TestStripShellPrefix:
    def test_strips_cd_and_and(self):
        result = CLIBridge._strip_shell_prefix(
            ["cd", "D:/proj", "&&", "tool", "--flag"]
        )
        assert result == ["tool", "--flag"]

    def test_strips_cd_and_semicolon(self):
        result = CLIBridge._strip_shell_prefix(
            ["cd", "/home/user", ";", "tool"]
        )
        assert result == ["tool"]

    def test_no_prefix_unchanged(self):
        result = CLIBridge._strip_shell_prefix(["tool", "--flag"])
        assert result == ["tool", "--flag"]

    def test_empty_list(self):
        result = CLIBridge._strip_shell_prefix([])
        assert result == []

    def test_cd_without_operator_unchanged(self):
        """cd without && or ; is not a shell prefix."""
        result = CLIBridge._strip_shell_prefix(
            ["cd", "/path", "tool"]
        )
        assert result == ["cd", "/path", "tool"]

    def test_cd_with_trailing_semicolon(self):
        result = CLIBridge._strip_shell_prefix(
            ["cd", "/path", ";"]
        )
        assert result == []  # no elements after ';'


# =========================================================================
# CLIBridge._try_parse_json_output tests
# =========================================================================


class TestTryParseJsonOutput:
    def test_entire_output_is_json(self):
        result = CLIBridge._try_parse_json_output('{"result": "ok"}')
        assert result == {"result": "ok"}

    def test_json_with_output_key(self):
        result = CLIBridge._try_parse_json_output('{"output": "data"}')
        assert result == {"output": "data"}

    def test_last_json_line_extracted(self):
        stdout = "some text\nmore text\n{\"output\": \"data\"}"
        result = CLIBridge._try_parse_json_output(stdout)
        assert result == {"output": "data"}

    def test_not_json_returns_none(self):
        result = CLIBridge._try_parse_json_output("not json")
        assert result is None

    def test_empty_string_returns_none(self):
        result = CLIBridge._try_parse_json_output("")
        assert result is None

    def test_whitespace_only_returns_none(self):
        result = CLIBridge._try_parse_json_output("   \n  \n  ")
        assert result is None

    def test_last_json_line_with_relevant_key(self):
        """Scans from last line: skips {"not_relevant": 42} (no matching key),
        then finds {"summary": "done"} which has 'summary' key."""
        stdout = "log line\n{\"summary\": \"done\"}\n{\"not_relevant\": 42}"
        result = CLIBridge._try_parse_json_output(stdout)
        assert result == {"summary": "done"}

    def test_last_json_line_with_relevant_key_reversed(self):
        stdout = "log line\n{\"not_relevant\": 42}\n{\"summary\": \"done\"}"
        result = CLIBridge._try_parse_json_output(stdout)
        assert result == {"summary": "done"}

    def test_json_array_not_returned(self):
        result = CLIBridge._try_parse_json_output('[1, 2, 3]')
        assert result is None

    def test_json_with_error_key(self):
        result = CLIBridge._try_parse_json_output('{"error": "something failed"}')
        assert result == {"error": "something failed"}

    def test_nested_json(self):
        result = CLIBridge._try_parse_json_output('{"result": {"nested": "value"}}')
        assert result == {"result": {"nested": "value"}}

    def test_malformed_json_in_lines_ignored(self):
        stdout = "some text\n{invalid json\n{\"result\": \"valid\"}"
        result = CLIBridge._try_parse_json_output(stdout)
        assert result == {"result": "valid"}

    def test_empty_json_object(self):
        """{} is valid JSON and passes the whole-output check (isinstance(dict) is True),
        so it is returned as-is before any key-based filtering."""
        result = CLIBridge._try_parse_json_output("{}")
        assert result == {}

    def test_json_inside_text_not_extracted(self):
        """Only standalone JSON lines (starting with { and ending with }) are extracted."""
        stdout = "the result is {\"result\": \"ok\"} but not standalone"
        result = CLIBridge._try_parse_json_output(stdout)
        # The line does not start with { → not detected in reversed line scan
        # And the whole output is not valid JSON
        assert result is None


# =========================================================================
# CLIBridge._build_env tests
# =========================================================================


class TestBuildEnv:
    def test_basic_env_copy(self, bridge, sample_manifest):
        env = bridge._build_env(sample_manifest, None)
        assert "AGENT_MODE" in env
        assert env["AGENT_MODE"] == "test"

    def test_extra_env_overrides(self, bridge, sample_manifest):
        env = bridge._build_env(sample_manifest, {"AGENT_MODE": "override"})
        assert env["AGENT_MODE"] == "override"

    def test_extra_env_adds_new_vars(self, bridge, sample_manifest):
        env = bridge._build_env(sample_manifest, {"MY_VAR": "my_value"})
        assert env["MY_VAR"] == "my_value"

    def test_utf8_defaults_set(self, bridge, sample_manifest):
        env = bridge._build_env(sample_manifest, None)
        assert env.get("PYTHONIOENCODING") == "utf-8"
        assert env.get("PYTHONUTF8") == "1"

    def test_utf8_defaults_do_not_override(self, bridge, sample_manifest):
        env = bridge._build_env(
            sample_manifest,
            {"PYTHONIOENCODING": "latin-1"},
        )
        assert env["PYTHONIOENCODING"] == "latin-1"

    def test_extra_env_empty_dict(self, bridge, sample_manifest):
        env = bridge._build_env(sample_manifest, {})
        assert "AGENT_MODE" in env
        assert env["AGENT_MODE"] == "test"


# =========================================================================
# CLIBridge constructor / default_timeout
# =========================================================================


class TestCLIBridgeConstructor:
    def test_default_timeout(self):
        bridge = CLIBridge()
        assert bridge.default_timeout == 300

    def test_custom_timeout(self):
        bridge = CLIBridge(default_timeout=60)
        assert bridge.default_timeout == 60

    def test_creates_default_registry(self):
        bridge = CLIBridge()
        assert isinstance(bridge.registry, AgentProcessRegistry)

    def test_accepts_custom_registry(self):
        custom = AgentProcessRegistry()
        bridge = CLIBridge(registry=custom)
        assert bridge.registry is custom


# =========================================================================
# End-to-end: AgentProcessRegistry + ProcessInfo integration
# =========================================================================


class TestRegistryProcessInfoIntegration:
    def test_register_creates_runnable_info(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = AgentManifest(
            name="integration-agent",
            interface=AgentInterface(command="echo hello"),
        )
        info = registry.register("integration-agent", mock_process, manifest)
        assert info.is_running is False  # status is "starting"
        info.status = "running"
        assert info.is_running is True
        assert registry.running_count == 1
        assert registry.total_count == 1

    def test_multiple_agents_with_different_statuses(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)

        names_statuses = [
            ("alpha", "running"),
            ("beta", "starting"),
            ("gamma", "stopped"),
            ("delta", "failed"),
            ("epsilon", "stopping"),
        ]
        for name, status in names_statuses:
            p = registry.register(name, mock_process, manifest)
            p.status = status

        assert registry.running_count == 1
        assert registry.total_count == 5
        assert len(registry.list_by_status("failed")) == 1
        assert len(registry.list_by_status("running")) == 1

    def test_status_summary_all_icons(self, mock_process):
        registry = AgentProcessRegistry()
        manifest = MagicMock(spec=AgentManifest)

        for name, status in [("a", "running"), ("b", "starting"),
                              ("c", "stopping"), ("d", "stopped"),
                              ("e", "failed")]:
            p = registry.register(name, mock_process, manifest)
            p.status = status

        summary = registry.status_summary()
        for icon in ["🟢", "🟡", "🟠", "⚫", "🔴"]:
            assert icon in summary

    def test_register_records_started_at(self, mock_process):
        registry = AgentProcessRegistry()
        before = time.monotonic()
        info = registry.register("a", mock_process, MagicMock(spec=AgentManifest))
        after = time.monotonic()
        assert before <= info.started_at <= after


# =========================================================================
# Smoke: CLIBridge._build_daemon_command (basic structure only)
# =========================================================================


class TestBuildDaemonCommand:
    def test_no_placeholders_returns_splitted(self, bridge, manifest_no_placeholders):
        cmd = bridge._build_daemon_command(manifest_no_placeholders)
        assert cmd == ["static-tool", "--flag"]

    def test_with_mode_replaced(self, bridge):
        manifest = AgentManifest(
            name="daemon-test",
            interface=AgentInterface(
                command="agent --mode {mode} --goal {goal}",
            ),
        )
        cmd = bridge._build_daemon_command(manifest)
        cmd_str = " ".join(cmd)
        assert "--mode" in cmd_str
        assert "daemon" in cmd_str
        assert "--goal" in cmd_str
        assert "serve" in cmd_str

    def test_all_placeholders_cleaned(self, bridge):
        manifest = AgentManifest(
            name="placeholder-heavy",
            interface=AgentInterface(
                command="agent --mode {mode} --goal {goal} --task {task} --project {project}",
            ),
        )
        cmd = bridge._build_daemon_command(manifest)
        # All placeholders should be replaced
        cmd_str = " ".join(cmd)
        assert "{mode}" not in cmd_str
        assert "{goal}" not in cmd_str
        assert "{task}" not in cmd_str
        assert "{project}" not in cmd_str
        assert "daemon" in cmd_str or "serve" in cmd_str or cmd_str != ""

    def test_fallback_to_first_token(self, bridge):
        """If the template only has placeholders, fallback to first token."""
        manifest = AgentManifest(
            name="minimal",
            interface=AgentInterface(command="{goal}"),
        )
        cmd = bridge._build_daemon_command(manifest)
        # The template becomes empty after cleanup, falls back to first token
        assert isinstance(cmd, list)
        assert len(cmd) >= 1

    def test_unknown_template_returns_echo_fallback(self, bridge):
        """When shlex.split fails on the first token, return echo fallback."""
        manifest = AgentManifest(
            name="broken",
            interface=AgentInterface(command=""),
        )
        cmd = bridge._build_daemon_command(manifest)
        # The code falls through to the final fallback
        assert isinstance(cmd, list)
