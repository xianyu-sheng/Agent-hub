"""Tests for agent_hub.cron -- cron expression parsing, scheduling, persistence."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import agent_hub.cron as cron_mod

# The source file is missing `import os` at module level but uses os.path.*,
# os.path.exists, os.remove in request_stop() and _cron_loop().  We inject
# it here so those methods are callable in tests.  This is a testing
# accommodation for a known bug in the source.
cron_mod.os = os

from agent_hub.cron import (
    MAX_HISTORY_ENTRIES,
    CronJob,
    CronRunRecord,
    CronScheduler,
    _match_field,
    cron_matches,
    parse_cron,
)


# ── Fixtures ──────────────────────────────────────────────────────────

FIXED_NOW = datetime(2026, 6, 29, 9, 0, 0, tzinfo=timezone.utc)  # Monday 9:00 UTC


@pytest.fixture
def mock_now():
    """Freeze agent_hub.cron.datetime.now at a fixed UTC time."""
    with patch.object(cron_mod, "datetime") as mock:
        mock.now.return_value = FIXED_NOW
        mock.fromisoformat = datetime.fromisoformat
        yield mock


@pytest.fixture
def scheduler(tmp_path):
    """CronScheduler with jobs/history paths inside tmp_path."""
    return CronScheduler(
        jobs_path=tmp_path / "cron_jobs.json",
        history_path=tmp_path / "cron_history.json",
    )


# ── _match_field ─────────────────────────────────────────────────────


class TestMatchField:
    """_match_field() unit tests."""

    def test_wildcard_matches_any_int(self):
        assert _match_field("*", 0) is True
        assert _match_field("*", 30) is True
        assert _match_field("*", 59) is True

    def test_step_matches(self):
        """*/5 matches 0, 5, 10 ... 55."""
        assert all(
            _match_field("*/5", v) for v in range(0, 60, 5)
        )

    def test_step_non_matches(self):
        """*/5 does NOT match 1, 3, 7."""
        assert _match_field("*/5", 1) is False
        assert _match_field("*/5", 3) is False
        assert _match_field("*/5", 7) is False

    def test_step_zero_returns_false(self):
        """When step is 0 (invalid), return False."""
        assert _match_field("*/0", 0) is False

    def test_list_matches(self):
        assert _match_field("0,30", 0) is True
        assert _match_field("0,30", 30) is True

    def test_list_non_matches(self):
        assert _match_field("0,30", 15) is False

    def test_range_matches(self):
        assert _match_field("1-5", 1) is True
        assert _match_field("1-5", 3) is True
        assert _match_field("1-5", 5) is True

    def test_range_non_matches(self):
        assert _match_field("1-5", 0) is False
        assert _match_field("1-5", 6) is False

    def test_exact_value_matches(self):
        assert _match_field("9", 9) is True

    def test_exact_value_non_matches(self):
        assert _match_field("9", 8) is False

    def test_invalid_int_returns_false(self):
        assert _match_field("abc", 5) is False

    def test_nested_comma_list(self):
        assert _match_field("0,15,30,45", 0) is True
        assert _match_field("0,15,30,45", 15) is True
        assert _match_field("0,15,30,45", 30) is True
        assert _match_field("0,15,30,45", 45) is True
        assert _match_field("0,15,30,45", 7) is False


# ── cron_matches ─────────────────────────────────────────────────────


class TestCronMatches:
    """cron_matches() unit tests."""

    def test_weekday_nine_am_matches(self):
        """0 9 * * 1-5 matches Mon-Fri 9:00am."""
        dt = datetime(2026, 6, 29, 9, 0, 0, tzinfo=timezone.utc)  # Monday
        assert cron_matches("0 9 * * 1-5", dt) is True

    def test_weekday_nine_am_no_match_saturday(self):
        """0 9 * * 1-5 does NOT match Saturday 9am."""
        dt = datetime(2026, 7, 4, 9, 0, 0, tzinfo=timezone.utc)  # Saturday
        assert cron_matches("0 9 * * 1-5", dt) is False

    def test_every_fifteen_minutes(self):
        """*/15 * * * * matches :00, :15, :30, :45."""
        base = datetime(2026, 6, 29, 9, 0, 0, tzinfo=timezone.utc)
        for minute in (0, 15, 30, 45):
            assert cron_matches("*/15 * * * *", base.replace(minute=minute)) is True

    def test_every_fifteen_minutes_no_match(self):
        """*/15 * * * * does NOT match :07, :22, :37, :52."""
        base = datetime(2026, 6, 29, 9, 0, 0, tzinfo=timezone.utc)
        for minute in (7, 22, 37, 52):
            assert cron_matches("*/15 * * * *", base.replace(minute=minute)) is False

    def test_jan_first_midnight(self):
        """0 0 1 1 * matches Jan 1 midnight."""
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert cron_matches("0 0 1 1 *", dt) is True

    def test_custom_dt_parameter_overrides_current_time(self):
        """Custom dt parameter overrides current time."""
        dt = datetime(2026, 12, 25, 10, 30, 0, tzinfo=timezone.utc)
        assert cron_matches("30 10 25 12 *", dt) is True
        assert cron_matches("0 9 * * *", dt) is False

    def test_four_field_raises_value_error(self):
        with pytest.raises(ValueError, match="5 字段"):
            cron_matches("*/15 * * *")

    def test_six_field_raises_value_error(self):
        with pytest.raises(ValueError, match="5 字段"):
            cron_matches("0 0 1 1 * 2026")

    def test_sunday_dow_zero_matches(self):
        """dow=0 matches Sunday (the cron convention)."""
        dt = datetime(2026, 6, 28, 10, 0, 0, tzinfo=timezone.utc)  # Sunday
        assert cron_matches("* * * * 0", dt) is True
        assert cron_matches("* * * * 7", dt) is True

    def test_sunday_dow_zero_no_match_weekday(self):
        """dow=0 does NOT match Monday."""
        dt = datetime(2026, 6, 29, 10, 0, 0, tzinfo=timezone.utc)  # Monday
        assert cron_matches("* * * * 0", dt) is False


# ── parse_cron ───────────────────────────────────────────────────────


class TestParseCron:
    """parse_cron() unit tests."""

    def test_every_five_minutes(self):
        result = parse_cron("*/5 * * * *")
        assert result == {
            "minute": "*/5",
            "hour": "*",
            "dom": "*",
            "month": "*",
            "dow": "*",
        }

    def test_weekday_nine_am(self):
        result = parse_cron("0 9 * * 1-5")
        assert result == {
            "minute": "0",
            "hour": "9",
            "dom": "*",
            "month": "*",
            "dow": "1-5",
        }

    def test_one_field_raises_value_error(self):
        with pytest.raises(ValueError, match="5 字段"):
            parse_cron("invalid")


# ── CronJob ──────────────────────────────────────────────────────────


class TestCronJob:
    """CronJob dataclass tests."""

    def test_to_dict_includes_all_set_fields(self):
        job = CronJob(
            name="workday",
            cron="0 9 * * 1-5",
            agent="my_agent",
            task="do_work",
            params={"key": "val"},
            enabled=False,
            last_run="2026-06-28T09:00:00+00:00",
            created_at="2026-06-01T00:00:00+00:00",
        )
        d = job.to_dict()
        assert d["name"] == "workday"
        assert d["cron"] == "0 9 * * 1-5"
        assert d["agent"] == "my_agent"
        assert d["task"] == "do_work"
        assert d["params"] == {"key": "val"}
        assert d["enabled"] is False
        assert d["last_run"] == "2026-06-28T09:00:00+00:00"
        assert d["created_at"] == "2026-06-01T00:00:00+00:00"

    def test_to_dict_omits_defaults(self):
        """enabled=True, empty params, empty last_run/created_at are omitted."""
        job = CronJob(name="minimal", cron="* * * * *", agent="a", task="t")
        d = job.to_dict()
        assert d == {
            "name": "minimal",
            "cron": "* * * * *",
            "agent": "a",
            "task": "t",
        }

    def test_from_dict_roundtrip(self):
        data = {
            "name": "myjob",
            "cron": "30 6 * * 1-5",
            "agent": "worker",
            "task": "backup",
            "params": {"target": "/data"},
            "enabled": False,
            "last_run": "2026-06-27T06:30:00+00:00",
            "created_at": "2026-06-01T00:00:00+00:00",
        }
        job = CronJob.from_dict(data)
        assert job.name == "myjob"
        assert job.cron == "30 6 * * 1-5"
        assert job.agent == "worker"
        assert job.task == "backup"
        assert job.params == {"target": "/data"}
        assert job.enabled is False
        assert job.last_run == "2026-06-27T06:30:00+00:00"
        assert job.created_at == "2026-06-01T00:00:00+00:00"
        # Roundtrip through to_dict
        assert job.to_dict() == data

    def test_from_dict_minimal_fields(self):
        """Default values for enabled/last_run/created_at."""
        job = CronJob.from_dict({
            "name": "minimal",
            "cron": "0 0 * * *",
            "agent": "a",
            "task": "t",
        })
        assert job.name == "minimal"
        assert job.enabled is True
        assert job.last_run == ""
        assert job.created_at == ""

    def test_name_cron_agent_required(self):
        """from_dict maps missing fields to empty strings (validation elsewhere)."""
        job = CronJob.from_dict({})
        assert job.name == ""
        assert job.cron == ""
        assert job.agent == ""
        assert job.task == ""


# ── CronRunRecord ────────────────────────────────────────────────────


class TestCronRunRecord:
    """CronRunRecord dataclass tests."""

    def test_construction_with_all_fields(self):
        record = CronRunRecord(
            job_name="test_job",
            agent="my_agent",
            task="do_stuff",
            success=True,
            output="completed",
            error="",
            duration_ms=150.5,
            timestamp="2026-06-29T09:00:00+00:00",
        )
        assert record.job_name == "test_job"
        assert record.agent == "my_agent"
        assert record.task == "do_stuff"
        assert record.success is True
        assert record.output == "completed"
        assert record.duration_ms == 150.5
        assert record.timestamp == "2026-06-29T09:00:00+00:00"

    def test_default_values(self):
        """success=False, output="", error="", duration_ms=0."""
        record = CronRunRecord(
            job_name="j", agent="a", task="t", success=False,
        )
        assert record.output == ""
        assert record.error == ""
        assert record.duration_ms == 0.0
        assert record.timestamp == ""


# ── CronScheduler ────────────────────────────────────────────────────


class TestCronScheduler:
    """CronScheduler tests with tmp_path for file I/O."""

    # ── CRUD ──────────────────────────────────────────────────

    def test_add_job_stores_and_persists(self, scheduler):
        job = CronJob(name="daily", cron="0 9 * * 1-5", agent="a", task="t")
        scheduler.add_job(job)
        assert scheduler.get_job("daily") is job

        # Verify it was persisted to the JSON file
        raw = json.loads(scheduler._jobs_path.read_text(encoding="utf-8"))
        assert raw["jobs"][0]["name"] == "daily"
        assert raw["jobs"][0]["cron"] == "0 9 * * 1-5"

    def test_add_job_empty_name_raises(self, scheduler):
        with pytest.raises(ValueError, match="name"):
            scheduler.add_job(
                CronJob(name="", cron="* * * * *", agent="a", task="t")
            )

    def test_add_job_validates_cron_expression(self, scheduler):
        """Expression with wrong field count raises ValueError."""
        with pytest.raises(ValueError, match="5 字段"):
            scheduler.add_job(
                CronJob(name="bad", cron="bad expr", agent="a", task="t")
            )

    def test_get_job_returns_job_by_name(self, scheduler):
        job = CronJob(name="findme", cron="0 0 * * *", agent="a", task="t")
        scheduler.add_job(job)
        assert scheduler.get_job("findme") is job

    def test_get_job_returns_none_for_missing(self, scheduler):
        assert scheduler.get_job("nonexistent") is None

    def test_list_jobs_returns_all(self, scheduler):
        scheduler.add_job(CronJob(name="j1", cron="0 9 * * *", agent="a", task="t"))
        scheduler.add_job(CronJob(name="j2", cron="30 18 * * *", agent="a", task="t"))
        jobs = scheduler.list_jobs()
        assert len(jobs) == 2
        assert {j.name for j in jobs} == {"j1", "j2"}

    def test_remove_job_deletes_and_returns(self, scheduler):
        job = CronJob(name="delme", cron="0 0 * * *", agent="a", task="t")
        scheduler.add_job(job)
        removed = scheduler.remove_job("delme")
        assert removed is job
        assert scheduler.get_job("delme") is None

    def test_remove_job_returns_none_for_missing(self, scheduler):
        assert scheduler.remove_job("ghost") is None

    def test_duplicate_name_overwrites(self, scheduler):
        j1 = CronJob(name="dup", cron="0 9 * * *", agent="a", task="t1")
        j2 = CronJob(name="dup", cron="30 18 * * *", agent="b", task="t2")
        scheduler.add_job(j1)
        scheduler.add_job(j2)
        job = scheduler.get_job("dup")
        assert job.agent == "b"
        assert job.task == "t2"

    # ── Loading / Persistence ─────────────────────────────────

    def test_load_restores_jobs_from_json(self, tmp_path):
        jobs_file = tmp_path / "cron_jobs.json"
        jobs_file.parent.mkdir(parents=True, exist_ok=True)
        jobs_file.write_text(
            json.dumps({
                "jobs": [
                    {
                        "name": "restored",
                        "cron": "0 9 * * *",
                        "agent": "a",
                        "task": "t",
                        "enabled": True,
                    },
                ],
            }),
            encoding="utf-8",
        )
        hist_file = tmp_path / "cron_history.json"
        hist_file.write_text("[]", encoding="utf-8")
        s = CronScheduler(jobs_path=jobs_file, history_path=hist_file)
        assert s.get_job("restored") is not None
        assert s.get_job("restored").name == "restored"

    def test_load_handles_corrupted_json_gracefully(self, tmp_path):
        jobs_file = tmp_path / "cron_jobs.json"
        jobs_file.write_text("not valid json {{{", encoding="utf-8")
        hist_file = tmp_path / "cron_history.json"
        hist_file.write_text("[]", encoding="utf-8")
        s = CronScheduler(jobs_path=jobs_file, history_path=hist_file)
        assert s.list_jobs() == []

    def test_load_handles_non_list_json(self, tmp_path):
        """A JSON object without a 'jobs' key is treated as empty."""
        jobs_file = tmp_path / "cron_jobs.json"
        jobs_file.write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")
        hist_file = tmp_path / "cron_history.json"
        hist_file.write_text("[]", encoding="utf-8")
        s = CronScheduler(jobs_path=jobs_file, history_path=hist_file)
        assert s.list_jobs() == []

    def test_save_jobs_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "cron_jobs.json"
        s = CronScheduler(
            jobs_path=nested,
            history_path=tmp_path / "cron_history.json",
        )
        s.add_job(CronJob(name="deep_test", cron="* * * * *", agent="a", task="t"))
        assert nested.exists()

    # ── History ───────────────────────────────────────────────

    def test_history_method_returns_recent_first(self, scheduler):
        for i in range(5):
            scheduler._history.append(
                CronRunRecord(
                    job_name=f"job{i}", agent="a", task="t", success=True,
                )
            )
        h = scheduler.history(limit=3)
        assert len(h) == 3
        # Most recent first (reversed order)
        assert [r.job_name for r in h] == ["job4", "job3", "job2"]

    def test_history_max_entries(self, scheduler):
        """_save_history only keeps the last MAX_HISTORY_ENTRIES."""
        total = MAX_HISTORY_ENTRIES + 50
        for i in range(total):
            scheduler._history.append(
                CronRunRecord(
                    job_name=f"job{i}",
                    agent="a",
                    task="t",
                    success=True,
                    timestamp=f"2026-01-01T00:{i:02d}:00+00:00",
                )
            )
        scheduler._save_history()
        saved = json.loads(scheduler._history_path.read_text(encoding="utf-8"))
        assert len(saved) == MAX_HISTORY_ENTRIES
        # The 50 oldest entries should be dropped; first entry is "job50"
        assert saved[0]["job_name"] == "job50"

    # ── get_due_jobs ──────────────────────────────────────────

    def test_get_due_jobs_returns_enabled_matching_job(self, scheduler, mock_now):
        """A job whose cron matches the current time is returned."""
        job = CronJob(name="due_test", cron="0 9 * * *", agent="a", task="t")
        scheduler.add_job(job)
        assert scheduler.get_due_jobs() == [job]

    def test_get_due_jobs_skips_disabled_jobs(self, scheduler, mock_now):
        job = CronJob(
            name="disabled",
            cron="0 9 * * *",
            agent="a",
            task="t",
            enabled=False,
        )
        scheduler.add_job(job)
        assert scheduler.get_due_jobs() == []

    def test_get_due_jobs_skips_jobs_ran_within_tolerance(self, scheduler, mock_now):
        """Skips jobs whose last_run is within MATCH_TOLERANCE_SEC (65s)."""
        # last_run 30 seconds ago -- inside the window
        recent = datetime.fromtimestamp(
            FIXED_NOW.timestamp() - 30, tz=timezone.utc
        ).isoformat()
        job = CronJob(
            name="recent",
            cron="0 9 * * *",
            agent="a",
            task="t",
            last_run=recent,
        )
        scheduler.add_job(job)
        assert scheduler.get_due_jobs() == []

    def test_get_due_jobs_allows_jobs_ran_long_ago(self, scheduler, mock_now):
        """A job whose last_run is beyond the tolerance IS due."""
        # last_run 120 seconds ago -- outside the window
        old = datetime.fromtimestamp(
            FIXED_NOW.timestamp() - 120, tz=timezone.utc
        ).isoformat()
        job = CronJob(
            name="old",
            cron="0 9 * * *",
            agent="a",
            task="t",
            last_run=old,
        )
        scheduler.add_job(job)
        assert scheduler.get_due_jobs() == [job]

    def test_get_due_jobs_skips_non_matching_cron(self, scheduler, mock_now):
        """Job whose cron doesn't match the current time is excluded."""
        job = CronJob(name="nocron", cron="30 23 * * *", agent="a", task="t")
        scheduler.add_job(job)
        assert scheduler.get_due_jobs() == []

    def test_get_due_jobs_bad_last_run_treated_as_due(self, scheduler, mock_now):
        """When last_run cannot be parsed, the job is treated as due."""
        job = CronJob(
            name="bads",
            cron="0 9 * * *",
            agent="a",
            task="t",
            last_run="not-a-datetime",
        )
        scheduler.add_job(job)
        assert scheduler.get_due_jobs() == [job]

    # ── request_stop ──────────────────────────────────────────

    def test_request_stop_creates_sentinel_file(self, scheduler):
        stop_file = os.path.join(scheduler.data_dir, ".cron_stop")
        assert not os.path.exists(stop_file)
        scheduler.request_stop()
        assert os.path.exists(stop_file)

    def test_request_stop_multiple_calls_idempotent(self, scheduler):
        """Repeated calls do not raise."""
        scheduler.request_stop()
        scheduler.request_stop()  # should not raise
        stop_file = os.path.join(scheduler.data_dir, ".cron_stop")
        assert os.path.exists(stop_file)

    # ── data_dir property ─────────────────────────────────────

    def test_data_dir_property(self, scheduler):
        assert scheduler.data_dir == str(scheduler._jobs_path.parent)


# ── Integration: file roundtrip ──────────────────────────────────────


class TestCronSchedulerFileRoundtrip:
    """End-to-end file persistence roundtrip."""

    def test_add_remove_reload(self, tmp_path):
        jp = tmp_path / "cron_jobs.json"
        hp = tmp_path / "cron_history.json"

        s1 = CronScheduler(jobs_path=jp, history_path=hp)
        s1.add_job(CronJob(name="persist", cron="0 0 * * *", agent="a", task="t"))
        assert len(s1.list_jobs()) == 1

        # New scheduler instance loads from the same files
        s2 = CronScheduler(jobs_path=jp, history_path=hp)
        assert len(s2.list_jobs()) == 1
        assert s2.get_job("persist").cron == "0 0 * * *"

        # Remove from s2, verify s3 sees empty
        s2.remove_job("persist")
        s3 = CronScheduler(jobs_path=jp, history_path=hp)
        assert s3.list_jobs() == []
