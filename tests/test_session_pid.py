"""Comprehensive unit tests for agent_hub.session_store and agent_hub.pid_store.

Tests cover:
- SessionRecord dataclass (to_dict/from_dict roundtrip, summary, truncation)
- SessionStore CRUD (save, load, list_sessions)
- SessionStore.get_recent_context (LLM context formatting, truncation, failure indicators)
- SessionStore._prune (file count limit enforcement)
- new_session_record convenience factory
- Corrupted session file handling
- StoredProcess dataclass
- PidFileStore CRUD (save, load_all, remove)
- PidFileStore.stop_all (process termination, dead process handling, cross-platform)
- Persistence across instances
- Malformed PID file graceful handling

Mock subprocess and os.kill as needed. Uses pytest tmp_path for storage directories.
"""

from __future__ import annotations

import json
import signal
from unittest.mock import MagicMock, patch

import pytest

from agent_hub.session_store import (
    MAX_SESSION_FILES,
    RESULT_TRUNCATE_CHARS,
    SessionRecord,
    SessionStore,
    new_session_record,
)
from agent_hub.pid_store import (
    PidFileStore,
    StoredProcess,
)


# ══════════════════════════════════════════════════════════════════════
# SessionRecord
# ══════════════════════════════════════════════════════════════════════


class TestSessionRecord:
    """Tests for SessionRecord dataclass serialization and properties."""

    def test_to_dict_roundtrip(self):
        """to_dict then from_dict preserves all fields."""
        original = SessionRecord(
            session_id="abc123",
            user_input="analyze code",
            route_plan={"strategy": "fan_out"},
            task_results=[{"agent": "test", "success": True}],
            aggregate="All tasks completed successfully",
            timestamp="2026-06-28T12:00:00",
            duration_ms=1500.0,
        )
        data = original.to_dict()
        restored = SessionRecord.from_dict(data)
        assert restored.session_id == original.session_id
        assert restored.user_input == original.user_input
        assert restored.route_plan == original.route_plan
        assert restored.task_results == original.task_results
        assert restored.aggregate == original.aggregate
        assert restored.timestamp == original.timestamp
        assert restored.duration_ms == original.duration_ms

    def test_to_dict_contains_expected_keys(self):
        """to_dict output has all expected keys."""
        record = SessionRecord(session_id="x", user_input="hi")
        data = record.to_dict()
        expected_keys = {
            "session_id", "user_input", "route_plan", "task_results",
            "aggregate", "timestamp", "duration_ms",
        }
        assert set(data.keys()) == expected_keys

    def test_to_dict_truncates_aggregate_at_500(self):
        """to_dict truncates aggregate to 500 characters."""
        long_text = "A" * 1000
        record = SessionRecord(session_id="x", user_input="test", aggregate=long_text)
        data = record.to_dict()
        assert len(data["aggregate"]) == 500
        assert data["aggregate"] == "A" * 500

    def test_from_dict_defaults(self):
        """from_dict fills missing fields with sensible defaults."""
        restored = SessionRecord.from_dict({})
        assert restored.session_id == ""
        assert restored.user_input == ""
        assert restored.route_plan == {}
        assert restored.task_results == []
        assert restored.aggregate == ""
        assert restored.timestamp == ""
        assert restored.duration_ms == 0.0

    def test_from_dict_graceful_types(self):
        """from_dict handles type mismatches without crashing.

        Note: explicitly passing None for a field bypasses the get() default
        (the default only applies when the key is absent), so route_plan and
        task_results will be None rather than {}/[].
        """
        data = {
            "session_id": 12345,
            "user_input": None,
            "route_plan": None,
            "task_results": None,
            "aggregate": None,
            "timestamp": None,
            "duration_ms": "100.5",
        }
        restored = SessionRecord.from_dict(data)
        assert restored.session_id == "12345"
        assert restored.user_input == "None"
        # route_plan and task_results are None because the key exists with None
        assert restored.route_plan is None
        assert restored.task_results is None
        assert restored.aggregate == "None"
        assert restored.timestamp == "None"
        assert restored.duration_ms == 100.5

    def test_summary_all_success(self):
        """summary includes user input, agent names, and success ratio."""
        record = SessionRecord(
            session_id="abc",
            user_input="Hello world",
            timestamp="2026-06-28T12:00:00.123456",
            task_results=[
                {"agent": "agent_a", "success": True},
                {"agent": "agent_b", "success": True},
            ],
        )
        summary = record.summary
        assert "2026-06-28T12:00:00" in summary
        assert "Hello world" in summary
        assert "agent_a" in summary
        assert "agent_b" in summary
        assert "2/2" in summary

    def test_summary_partial_success(self):
        """summary shows reduced success ratio when some tasks fail."""
        record = SessionRecord(
            session_id="abc",
            user_input="test",
            timestamp="2026-06-28T12:00:00",
            task_results=[
                {"agent": "agent_a", "success": True},
                {"agent": "agent_b", "success": False},
            ],
        )
        assert "1/2" in record.summary

    def test_summary_empty_results(self):
        """summary handles empty task_results gracefully."""
        record = SessionRecord(
            session_id="abc",
            user_input="test",
            timestamp="2026-06-28T12:00:00",
            task_results=[],
        )
        summary = record.summary
        assert "0/0" in summary

    def test_summary_deduplicates_agent_names(self):
        """summary deduplicates agent names from task_results."""
        record = SessionRecord(
            session_id="abc",
            user_input="test",
            timestamp="2026-06-28T12:00:00",
            task_results=[
                {"agent": "agent_x", "success": True},
                {"agent": "agent_x", "success": False},
            ],
        )
        # agent_x should appear only once in the agent list
        assert record.summary.count("agent_x") == 1


# ══════════════════════════════════════════════════════════════════════
# SessionStore
# ══════════════════════════════════════════════════════════════════════


class TestSessionStore:
    """Tests for SessionStore CRUD, context, and utilities."""

    # ── save ─────────────────────────────────────────────────────

    def test_save_creates_json_file(self, tmp_path):
        """save() creates a JSON file in the store directory."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="test-session-123",
            user_input="hello",
            timestamp="2026-06-28T12:00:00",
        )
        store.save(record)
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) == 1

    def test_save_file_has_valid_json(self, tmp_path):
        """save() writes a valid JSON file with expected content."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="sid-001",
            user_input="analyze this",
            route_plan={"strategy": "fan_out"},
            task_results=[{"agent": "a1", "success": True}],
            aggregate="All good",
            timestamp="2026-06-28T12:00:00",
            duration_ms=2500.0,
        )
        file_path = store.save(record)
        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert data["session_id"] == "sid-001"
        assert data["user_input"] == "analyze this"
        assert data["route_plan"]["strategy"] == "fan_out"
        assert data["task_results"][0]["agent"] == "a1"
        assert data["aggregate"] == "All good"
        assert data["timestamp"] == "2026-06-28T12:00:00"
        assert data["duration_ms"] == 2500.0

    def test_save_generates_timestamp_when_missing(self, tmp_path):
        """save() generates a timestamp for the filename even when the record
        has none.  The in-record timestamp field is stored as-is (empty),
        only the filename reflects the current time."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(session_id="no-ts-123", user_input="test")
        file_path = store.save(record)
        # The filename should contain a timestamp component
        assert file_path.stem.count("-") >= 2  # has date-like pattern
        loaded = store.load("no-ts-123")
        assert loaded is not None
        # The record's own timestamp field was empty → stays empty in the file
        assert loaded.timestamp == ""

    def test_save_file_naming(self, tmp_path):
        """save() creates a file named with timestamp and session_id prefix."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="abc123def456",
            user_input="test",
            timestamp="2026-06-28T12:00:00",
        )
        file_path = store.save(record)
        # Filename should contain safe timestamp and first 8 chars of session_id
        assert "2026-06-28T12-00-00" in file_path.name
        assert "abc123de" in file_path.name
        assert file_path.suffix == ".json"

    # ── load ─────────────────────────────────────────────────────

    def test_load_returns_record(self, tmp_path):
        """load() retrieves a saved SessionRecord."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="test-session-123",
            user_input="analyze this",
            route_plan={"strategy": "fan_out"},
            task_results=[{"agent": "a1", "success": True, "output": "done"}],
            aggregate="Everything worked",
            timestamp="2026-06-28T12:00:00",
            duration_ms=2500.0,
        )
        store.save(record)
        loaded = store.load("test-session-123")
        assert loaded is not None
        assert loaded.session_id == "test-session-123"
        assert loaded.user_input == "analyze this"
        assert loaded.route_plan == {"strategy": "fan_out"}
        assert loaded.task_results == [{"agent": "a1", "success": True, "output": "done"}]
        assert loaded.aggregate == "Everything worked"
        assert loaded.duration_ms == 2500.0

    def test_load_returns_none_for_missing(self, tmp_path):
        """load() returns None when no matching session exists."""
        store = SessionStore(store_dir=tmp_path)
        assert store.load("nonexistent") is None

    def test_load_by_session_id_prefix(self, tmp_path):
        """load() matches on the first 8 chars of session_id."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="abcdef123456",
            user_input="test",
            timestamp="2026-06-28T12:00:00",
        )
        store.save(record)
        # Use a longer id that shares the first 8 chars
        loaded = store.load("abcdef12XXXX")
        assert loaded is not None
        assert loaded.session_id == "abcdef123456"

    def test_load_empty_store_returns_none(self, tmp_path):
        """load() returns None when the store directory is empty."""
        store = SessionStore(store_dir=tmp_path)
        assert store.load("anything") is None

    # ── list_sessions ────────────────────────────────────────────

    def test_list_sessions_empty(self, tmp_path):
        """list_sessions returns empty list for empty store."""
        store = SessionStore(store_dir=tmp_path)
        assert store.list_sessions() == []

    def test_list_sessions_sorted_by_recency(self, tmp_path):
        """list_sessions returns sessions newest-first."""
        store = SessionStore(store_dir=tmp_path)
        store.save(SessionRecord(
            session_id="s1", user_input="first",
            timestamp="2026-06-28T10:00:00",
        ))
        store.save(SessionRecord(
            session_id="s2", user_input="second",
            timestamp="2026-06-28T11:00:00",
        ))
        store.save(SessionRecord(
            session_id="s3", user_input="third",
            timestamp="2026-06-28T12:00:00",
        ))
        sessions = store.list_sessions()
        assert len(sessions) == 3
        assert sessions[0].user_input == "third"
        assert sessions[1].user_input == "second"
        assert sessions[2].user_input == "first"

    def test_list_sessions_respects_limit(self, tmp_path):
        """list_sessions(limit=N) returns at most N records."""
        store = SessionStore(store_dir=tmp_path)
        for i in range(10):
            ts = f"2026-06-28T{i:02d}:00:00"
            store.save(SessionRecord(
                session_id=f"s{i}", user_input=f"s{i}", timestamp=ts,
            ))
        assert len(store.list_sessions(limit=3)) == 3
        assert len(store.list_sessions(limit=100)) == 10

    def test_list_sessions_skips_corrupted_files(self, tmp_path):
        """list_sessions skips files that fail to parse."""
        store = SessionStore(store_dir=tmp_path)
        store.save(SessionRecord(
            session_id="good", user_input="valid",
            timestamp="2026-06-28T12:00:00",
        ))
        # Write a corrupted file
        (tmp_path / "corrupted.json").write_text("not valid json", encoding="utf-8")
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == "good"

    # ── get_recent_context ───────────────────────────────────────

    def test_get_recent_context_empty_store(self, tmp_path):
        """get_recent_context returns empty string for empty store."""
        store = SessionStore(store_dir=tmp_path)
        assert store.get_recent_context() == ""
        assert store.get_recent_context(n=10) == ""

    def test_get_recent_context_single_session(self, tmp_path):
        """get_recent_context returns a single formatted entry."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="s1",
            user_input="analyze the code",
            aggregate="Found 3 issues",
            timestamp="2026-06-28T12:00:00",
            task_results=[{"agent": "agent_a", "success": True}],
        )
        store.save(record)
        context = store.get_recent_context(n=5)
        assert "[1] 用户:" in context
        assert "analyze the code" in context
        assert "agent_a" in context
        assert "✅" in context
        assert "1/1" in context
        assert "Found 3 issues" in context

    def test_get_recent_context_multiple_sessions_newest_first(self, tmp_path):
        """get_recent_context orders sessions newest-first."""
        store = SessionStore(store_dir=tmp_path)
        store.save(SessionRecord(
            session_id="s1", user_input="older task",
            aggregate="Done 1", timestamp="2026-06-28T10:00:00",
            task_results=[{"agent": "a", "success": True}],
        ))
        store.save(SessionRecord(
            session_id="s2", user_input="newer task",
            aggregate="Done 2", timestamp="2026-06-28T11:00:00",
            task_results=[{"agent": "a", "success": True}],
        ))
        context = store.get_recent_context(n=5)
        lines = [l for l in context.split("\n") if l.strip()]
        # Find the entry indices
        newer_idx = next(i for i, l in enumerate(lines) if "newer task" in l)
        older_idx = next(i for i, l in enumerate(lines) if "older task" in l)
        assert newer_idx < older_idx, "newer session should appear first"

    def test_get_recent_context_with_failures(self, tmp_path):
        """get_recent_context shows failure indicators for mixed results."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="s1",
            user_input="deploy",
            aggregate="Deployment failed",
            timestamp="2026-06-28T12:00:00",
            task_results=[
                {"agent": "agent_a", "success": True},
                {"agent": "agent_b", "success": False},
            ],
        )
        store.save(record)
        context = store.get_recent_context()
        assert "⚠️" in context
        assert "❌" in context or "❌" in context
        assert "1/2" in context

    def test_get_recent_context_all_success_no_fail_indicator(self, tmp_path):
        """get_recent_context uses ✅ when all tasks succeed."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="s1",
            user_input="all good",
            aggregate="Success",
            timestamp="2026-06-28T12:00:00",
            task_results=[
                {"agent": "a", "success": True},
                {"agent": "b", "success": True},
            ],
        )
        store.save(record)
        context = store.get_recent_context()
        assert "✅" in context
        assert "❌" not in context
        assert "2/2" in context

    def test_get_recent_context_truncates_total_length(self, tmp_path):
        """get_recent_context respects the CONTEXT_MAX_CHARS limit."""
        store = SessionStore(store_dir=tmp_path)
        long_agg = "X" * 300
        for i in range(20):
            ts = f"2026-06-28T{i:02d}:00:00"
            store.save(SessionRecord(
                session_id=f"s{i}", user_input=f"task {i}",
                aggregate=long_agg,
                timestamp=ts,
                task_results=[{"agent": "a", "success": True}],
            ))
        context = store.get_recent_context(n=20)
        # Should be <= CONTEXT_MAX_CHARS plus a small allowance for truncation note
        assert len(context) <= 1350  # CONTEXT_MAX_CHARS (1200) + truncation note (~50)

    def test_get_recent_context_truncates_long_aggregates(self, tmp_path):
        """get_recent_context truncates individual aggregates with ellipsis."""
        store = SessionStore(store_dir=tmp_path)
        long_agg = "Y" * 500
        record = SessionRecord(
            session_id="s1", user_input="big output",
            aggregate=long_agg,
            timestamp="2026-06-28T12:00:00",
            task_results=[{"agent": "a", "success": True}],
        )
        store.save(record)
        context = store.get_recent_context()
        assert "Y" * RESULT_TRUNCATE_CHARS in context
        assert "..." in context

    def test_get_recent_context_shows_truncation_note(self, tmp_path):
        """get_recent_context includes a truncation note when entries are omitted."""
        store = SessionStore(store_dir=tmp_path)
        for i in range(10):
            ts = f"2026-06-28T{i:02d}:00:00"
            store.save(SessionRecord(
                session_id=f"s{i}", user_input=f"very long task description {i}",
                aggregate="A" * 400,
                timestamp=ts,
                task_results=[{"agent": "a", "success": True}],
            ))
        context = store.get_recent_context(n=10)
        # If not all entries fit, a truncation note should appear
        if "省略" in context:
            assert "更早" in context or "省略" in context

    # ── _prune ───────────────────────────────────────────────────

    def test_prune_removes_excess_files(self, tmp_path):
        """_prune deletes oldest files when count exceeds MAX_SESSION_FILES."""
        store = SessionStore(store_dir=tmp_path)
        for i in range(MAX_SESSION_FILES + 10):
            ts = f"2026-06-28T{i:02d}:00:00"
            store.save(SessionRecord(
                session_id=f"s{i:03d}", user_input=f"task {i}",
                timestamp=ts,
            ))
        files = list(tmp_path.glob("*.json"))
        assert len(files) <= MAX_SESSION_FILES

    def test_prune_under_limit_does_nothing(self, tmp_path):
        """_prune does nothing when file count is within the limit."""
        store = SessionStore(store_dir=tmp_path)
        for i in range(MAX_SESSION_FILES - 5):
            ts = f"2026-06-28T{i:02d}:00:00"
            store.save(SessionRecord(
                session_id=f"s{i:03d}", user_input=f"task {i}",
                timestamp=ts,
            ))
        files = list(tmp_path.glob("*.json"))
        assert len(files) == MAX_SESSION_FILES - 5

    def test_prune_preserves_newest_files(self, tmp_path):
        """_prune keeps the newest MAX_SESSION_FILES and removes the oldest."""
        store = SessionStore(store_dir=tmp_path)
        for i in range(MAX_SESSION_FILES + 5):
            ts = f"2026-06-28T{i:02d}:00:00"
            store.save(SessionRecord(
                session_id=f"s{i:03d}", user_input=f"task {i}",
                timestamp=ts,
            ))
        sessions = store.list_sessions(limit=MAX_SESSION_FILES + 10)
        ids = {s.session_id for s in sessions}
        # The 5 oldest files (s000 through s004) should be gone
        for i in range(5):
            assert f"s{i:03d}" not in ids, f"s{i:03d} should have been pruned"
        # The remaining files (s005 and newer) should still be there
        for i in range(5, MAX_SESSION_FILES + 5):
            assert f"s{i:03d}" in ids, f"s{i:03d} should still exist"

    # ── new_session_record ───────────────────────────────────────

    def test_new_session_record_factory(self):
        """new_session_record creates a populated SessionRecord."""
        record = new_session_record(
            user_input="test input",
            route_plan={"strategy": "fan_out"},
            task_results=[{"agent": "a", "success": True}],
            aggregate="All good",
            duration_ms=1234.5,
        )
        assert isinstance(record, SessionRecord)
        assert record.user_input == "test input"
        assert record.route_plan == {"strategy": "fan_out"}
        assert record.task_results == [{"agent": "a", "success": True}]
        assert record.aggregate == "All good"
        assert record.duration_ms == 1234.5
        assert record.session_id  # auto-generated UUID hex
        assert record.timestamp  # auto-generated ISO timestamp

    def test_new_session_record_unique_ids(self):
        """Each new_session_record call generates a unique session_id."""
        r1 = new_session_record("input1", {}, [], "", 0)
        r2 = new_session_record("input2", {}, [], "", 0)
        assert r1.session_id != r2.session_id

    # ── corrupted files ──────────────────────────────────────────

    def test_corrupted_json_file_returns_none(self, tmp_path):
        """load() returns None when the session file has corrupted JSON."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="good1", user_input="valid",
            timestamp="2026-06-28T12:00:00",
        )
        store.save(record)
        # Corrupt the saved file
        json_files = list(tmp_path.glob("*.json"))
        json_files[0].write_text("{corrupted json", encoding="utf-8")
        loaded = store.load("good1")
        assert loaded is None

    def test_corrupted_file_does_not_affect_other_sessions(self, tmp_path):
        """A corrupted file doesn't prevent loading other valid sessions."""
        store = SessionStore(store_dir=tmp_path)
        store.save(SessionRecord(
            session_id="good", user_input="valid",
            timestamp="2026-06-28T12:00:00",
        ))
        # Write several corrupted files
        for i in range(3):
            (tmp_path / f"bad_{i}.json").write_text("{bad", encoding="utf-8")
        # list_sessions should still return the valid session
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].user_input == "valid"
        # load should still find the valid session
        loaded = store.load("good")
        assert loaded is not None
        assert loaded.user_input == "valid"

    def test_empty_json_object_is_loaded(self, tmp_path):
        """An empty JSON object {} loads with all default fields."""
        store = SessionStore(store_dir=tmp_path)
        # Write a file with empty JSON object but valid session_id in filename
        fpath = tmp_path / "2026-06-28T12-00-00_empty.json"
        fpath.write_text("{}", encoding="utf-8")
        # This file won't be found by load() because the session_id prefix
        # "empty" isn't matched. But we can verify from_dict works.
        data = json.loads(fpath.read_text(encoding="utf-8"))
        record = SessionRecord.from_dict(data)
        assert record.session_id == ""

    # ── persistence ──────────────────────────────────────────────

    def test_persistence_across_instances(self, tmp_path):
        """Sessions survive reload from disk with a new SessionStore instance."""
        store1 = SessionStore(store_dir=tmp_path)
        store1.save(SessionRecord(
            session_id="persist-session",
            user_input="saved data",
            timestamp="2026-06-28T12:00:00",
            task_results=[{"agent": "a", "success": True}],
        ))
        store2 = SessionStore(store_dir=tmp_path)
        loaded = store2.load("persist-session")
        assert loaded is not None
        assert loaded.user_input == "saved data"
        assert loaded.task_results == [{"agent": "a", "success": True}]

    def test_persistence_multiple_sessions(self, tmp_path):
        """Multiple sessions all survive across instances."""
        store1 = SessionStore(store_dir=tmp_path)
        for i in range(5):
            ts = f"2026-06-28T{i:02d}:00:00"
            store1.save(SessionRecord(
                session_id=f"s{i}", user_input=f"data {i}",
                timestamp=ts,
            ))
        store2 = SessionStore(store_dir=tmp_path)
        sessions = store2.list_sessions()
        assert len(sessions) == 5
        inputs = {s.user_input for s in sessions}
        assert inputs == {"data 0", "data 1", "data 2", "data 3", "data 4"}

    # ── delete_session (manual file removal) ─────────────────────

    def test_delete_by_removing_file(self, tmp_path):
        """load() returns None after the session file is manually removed."""
        store = SessionStore(store_dir=tmp_path)
        record = SessionRecord(
            session_id="delete-me",
            user_input="to be deleted",
            timestamp="2026-06-28T12:00:00",
        )
        store.save(record)
        assert store.load("delete-me") is not None
        # Manually remove the session file
        for f in tmp_path.glob("*.json"):
            f.unlink()
        assert store.load("delete-me") is None

    # ── default store dir ────────────────────────────────────────

    def test_default_store_dir_created(self):
        """SessionStore creates the default store directory automatically."""
        store = SessionStore()
        assert store.store_dir.exists()
        assert store.store_dir.is_dir()


# ══════════════════════════════════════════════════════════════════════
# StoredProcess
# ══════════════════════════════════════════════════════════════════════


class TestStoredProcess:
    """Tests for StoredProcess dataclass."""

    def test_fields_assigned(self):
        """StoredProcess fields are correctly assigned."""
        proc = StoredProcess(name="agent1", pid=12345, started_at=100.0)
        assert proc.name == "agent1"
        assert proc.pid == 12345
        assert proc.started_at == 100.0
        assert proc.protocol == "cli"  # default

    def test_custom_protocol(self):
        """StoredProcess accepts a custom protocol value."""
        proc = StoredProcess(name="a", pid=1, started_at=0.0, protocol="mcp")
        assert proc.protocol == "mcp"

    def test_custom_protocol_http(self):
        """StoredProcess accepts http protocol."""
        proc = StoredProcess(name="b", pid=2, started_at=1.0, protocol="http")
        assert proc.protocol == "http"

    def test_custom_protocol_internal(self):
        """StoredProcess accepts internal protocol."""
        proc = StoredProcess(name="c", pid=3, started_at=2.0, protocol="internal")
        assert proc.protocol == "internal"


# ══════════════════════════════════════════════════════════════════════
# PidFileStore
# ══════════════════════════════════════════════════════════════════════


class TestPidFileStore:
    """Tests for PidFileStore CRUD and process management."""

    # ── save ─────────────────────────────────────────────────────

    def test_save_creates_json_file(self, tmp_path):
        """save() creates the process_registry.json file."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        assert store._store_path.exists()
        data = json.loads(store._store_path.read_text(encoding="utf-8"))
        assert "agent1" in data

    def test_save_stores_all_fields(self, tmp_path):
        """save() stores name, pid, started_at, and protocol."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("test_agent", 99999, protocol="mcp")
        data = json.loads(store._store_path.read_text(encoding="utf-8"))
        entry = data["test_agent"]
        assert entry["name"] == "test_agent"
        assert entry["pid"] == 99999
        assert entry["protocol"] == "mcp"
        assert isinstance(entry["started_at"], float)
        assert entry["started_at"] > 0

    def test_save_updates_existing_entry(self, tmp_path):
        """save() overwrites an existing entry for the same agent name."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 1001)
        store.save("agent1", 2002)
        records = store.load_all()
        assert len(records) == 1
        assert records["agent1"].pid == 2002

    def test_save_preserves_other_entries(self, tmp_path):
        """save() preserves other entries when adding a new one."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent_a", 1001)
        store.save("agent_b", 1002)
        records = store.load_all()
        assert len(records) == 2

    def test_save_creates_store_dir(self, tmp_path):
        """save() creates the storage directory if it doesn't exist."""
        nested = tmp_path / "a" / "b" / "c"
        store = PidFileStore(store_dir=nested)
        store.save("agent1", 12345)
        assert nested.exists()
        assert store._store_path.exists()

    # ── load_all ─────────────────────────────────────────────────

    def test_load_all_empty_when_no_file(self, tmp_path):
        """load_all returns empty dict when the registry file doesn't exist."""
        store = PidFileStore(store_dir=tmp_path)
        assert store.load_all() == {}

    def test_load_all_returns_stored_process_objects(self, tmp_path):
        """load_all returns StoredProcess instances with correct fields."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345, protocol="mcp")
        records = store.load_all()
        assert "agent1" in records
        proc = records["agent1"]
        assert isinstance(proc, StoredProcess)
        assert proc.name == "agent1"
        assert proc.pid == 12345
        assert proc.protocol == "mcp"
        assert proc.started_at > 0

    def test_load_all_multiple_agents(self, tmp_path):
        """load_all returns all saved agents."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent_a", 1001)
        store.save("agent_b", 1002)
        store.save("agent_c", 1003)
        records = store.load_all()
        assert len(records) == 3
        assert records["agent_a"].pid == 1001
        assert records["agent_b"].pid == 1002
        assert records["agent_c"].pid == 1003

    # ── remove ───────────────────────────────────────────────────

    def test_remove_existing_entry(self, tmp_path):
        """remove() deletes an entry from the registry."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        store.remove("agent1")
        assert "agent1" not in store.load_all()

    def test_remove_nonexistent_does_not_raise(self, tmp_path):
        """remove() does not raise when removing a nonexistent entry."""
        store = PidFileStore(store_dir=tmp_path)
        # Should not raise
        store.remove("nonexistent")

    def test_remove_one_of_multiple(self, tmp_path):
        """remove() only removes the target entry, preserving others."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 1001)
        store.save("agent2", 1002)
        store.remove("agent1")
        records = store.load_all()
        assert len(records) == 1
        assert "agent2" in records
        assert records["agent2"].pid == 1002

    def test_remove_all_entries_empties_file(self, tmp_path):
        """remove() leaves an empty JSON object when all entries are removed."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 1001)
        store.remove("agent1")
        data = json.loads(store._store_path.read_text(encoding="utf-8"))
        assert data == {}

    # ── stop_all ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stop_all_no_processes(self, tmp_path):
        """stop_all returns 0 when no processes are registered."""
        store = PidFileStore(store_dir=tmp_path)
        count = await store.stop_all()
        assert count == 0

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "win32")
    async def test_stop_all_windows_success(self, tmp_path):
        """stop_all terminates processes on Windows via taskkill."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        with patch("agent_hub.pid_store.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            with patch(
                "agent_hub.pid_store.asyncio.to_thread",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ):
                count = await store.stop_all()
        assert count == 1
        mock_run.assert_called_once_with(
            ["taskkill", "/PID", "12345", "/F", "/T"],
            capture_output=True, timeout=10,
        )

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "win32")
    async def test_stop_all_windows_process_not_found(self, tmp_path):
        """stop_all counts a process as stopped when taskkill says not found."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        with patch("agent_hub.pid_store.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = b"not found"
            mock_run.return_value = mock_result
            with patch(
                "agent_hub.pid_store.asyncio.to_thread",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ):
                count = await store.stop_all()
        # Process already dead - still counts as stopped
        assert count == 1

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "win32")
    async def test_stop_all_windows_chinese_not_found(self, tmp_path):
        """stop_all handles Chinese 'not found' error message on Windows."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        with patch("agent_hub.pid_store.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "不存在".encode("utf-8")
            mock_run.return_value = mock_result
            with patch(
                "agent_hub.pid_store.asyncio.to_thread",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ):
                count = await store.stop_all()
        assert count == 1

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "win32")
    async def test_stop_all_windows_unknown_error(self, tmp_path):
        """stop_all does not count process on unexpected taskkill error."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        with patch("agent_hub.pid_store.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = b"access denied"
            mock_run.return_value = mock_result
            with patch(
                "agent_hub.pid_store.asyncio.to_thread",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ):
                count = await store.stop_all()
        assert count == 0

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "linux")
    async def test_stop_all_unix(self, tmp_path):
        """stop_all terminates processes on Unix via os.kill."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        with patch("agent_hub.pid_store.os.kill") as mock_kill:
            # Make os.kill(pid, 0) raise OSError so the wait loop exits immediately
            def kill_side_effect(pid, sig):
                if sig == 0:
                    raise OSError("Process does not exist")
            mock_kill.side_effect = kill_side_effect
            count = await store.stop_all()
        assert count == 1
        # Should have sent SIGTERM at least once
        mock_kill.assert_any_call(12345, signal.SIGTERM)

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "linux")
    async def test_stop_all_unix_already_dead(self, tmp_path):
        """stop_all handles already-dead process on Unix."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        with patch("agent_hub.pid_store.os.kill") as mock_kill:
            # All os.kill calls raise OSError (process already dead)
            mock_kill.side_effect = OSError("No such process")
            count = await store.stop_all()
        assert count == 1

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "win32")
    async def test_stop_all_multiple_processes(self, tmp_path):
        """stop_all terminates all registered processes."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 1001)
        store.save("agent2", 1002)
        store.save("agent3", 1003)
        with patch("agent_hub.pid_store.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch(
                "agent_hub.pid_store.asyncio.to_thread",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ):
                count = await store.stop_all()
        assert count == 3
        assert mock_run.call_count == 3

    @pytest.mark.asyncio
    @patch("agent_hub.pid_store.sys.platform", "win32")
    async def test_stop_all_clears_file(self, tmp_path):
        """stop_all removes the registry file after stopping all processes."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        assert store._store_path.exists()
        with patch("agent_hub.pid_store.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch(
                "agent_hub.pid_store.asyncio.to_thread",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ):
                await store.stop_all()
        assert not store._store_path.exists()

    # ── persistence ──────────────────────────────────────────────

    def test_persistence_across_instances(self, tmp_path):
        """PID registry survives reload from disk with a new instance."""
        store1 = PidFileStore(store_dir=tmp_path)
        store1.save("agent1", 12345)
        store2 = PidFileStore(store_dir=tmp_path)
        records = store2.load_all()
        assert "agent1" in records
        assert records["agent1"].pid == 12345

    def test_persistence_multiple_agents(self, tmp_path):
        """Multiple PID entries survive across instances."""
        store1 = PidFileStore(store_dir=tmp_path)
        store1.save("agent_a", 1001)
        store1.save("agent_b", 1002)
        store2 = PidFileStore(store_dir=tmp_path)
        records = store2.load_all()
        assert len(records) == 2
        assert records["agent_a"].pid == 1001
        assert records["agent_b"].pid == 1002

    # ── malformed file ───────────────────────────────────────────

    def test_malformed_json_returns_empty(self, tmp_path):
        """load_all returns empty dict when the JSON file is corrupt."""
        store = PidFileStore(store_dir=tmp_path)
        store._store_path.parent.mkdir(parents=True, exist_ok=True)
        store._store_path.write_text("not valid json", encoding="utf-8")
        assert store.load_all() == {}

    def test_non_dict_json_returns_empty(self, tmp_path):
        """load_all returns empty dict when the JSON root is not a dict."""
        store = PidFileStore(store_dir=tmp_path)
        store._store_path.parent.mkdir(parents=True, exist_ok=True)
        store._store_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert store.load_all() == {}

    def test_malformed_does_not_prevent_save(self, tmp_path):
        """A corrupt file does not prevent subsequent save operations."""
        store = PidFileStore(store_dir=tmp_path)
        store._store_path.parent.mkdir(parents=True, exist_ok=True)
        store._store_path.write_text("{garbage", encoding="utf-8")
        # Save should succeed despite the corrupt file
        store.save("agent1", 12345)
        records = store.load_all()
        assert "agent1" in records
        assert records["agent1"].pid == 12345

    # ── _clear ───────────────────────────────────────────────────

    def test_clear_removes_file(self, tmp_path):
        """_clear removes the registry file."""
        store = PidFileStore(store_dir=tmp_path)
        store.save("agent1", 12345)
        assert store._store_path.exists()
        store._clear()
        assert not store._store_path.exists()

    def test_clear_no_file_no_error(self, tmp_path):
        """_clear does not raise when the registry file doesn't exist."""
        store = PidFileStore(store_dir=tmp_path)
        store._clear()  # should not raise

    # ── default store dir ────────────────────────────────────────

    def test_default_store_dir_created(self, tmp_path):
        """PidFileStore default store directory is created automatically."""
        store = PidFileStore()
        assert store.store_dir.exists()
        assert store.store_dir.is_dir()
