"""
tests/test_backup.py — the unrecoverable state, and getting it off the box.

Everything runs on one VM. Lose it and you lose the record of every video
published, the performance scores accumulated over months, and the history
that stops the channel repeating itself — none of which the YouTube API
can rebuild.
"""
import json
import os
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def state(tmp_path):
    from config.settings import settings
    settings.logs_dir = tmp_path
    (tmp_path / "upload_manifest.jsonl").write_text('{"video_id":"abc"}\n')
    (tmp_path / "performance_state.json").write_text('{"hook":{"stat_first":0.71}}')
    (tmp_path / "content_history.json").write_text("{}")
    (tmp_path / "claude_usage.jsonl").write_text('{"source":"script_gen"}\n')
    (tmp_path / "heartbeat.log").write_text("regenerable noise\n")
    (tmp_path / "quota_tracker.json").write_text("{}")
    (tmp_path / "pipeline_state").mkdir()
    (tmp_path / "pipeline_state" / "weekday_premarket_2026-08-29.json").write_text("{}")
    (tmp_path / "analytics").mkdir()
    (tmp_path / "analytics" / "2026-08-28.json").write_text('{"views": 10}')
    return tmp_path


def _names(archive):
    with tarfile.open(archive) as tar:
        return tar.getnames()


class TestWhatGetsArchived:
    def test_irreplaceable_state_is_included(self, state):
        from scheduler.backup import create_archive
        names = _names(create_archive("T"))
        for required in ("upload_manifest.jsonl", "performance_state.json",
                         "content_history.json", "claude_usage.jsonl"):
            assert required in names, required

    def test_directories_are_archived_whole(self, state):
        from scheduler.backup import create_archive
        names = _names(create_archive("T"))
        assert any(n.startswith("analytics/") for n in names)
        assert any(n.startswith("pipeline_state/") for n in names)

    def test_regenerable_files_are_left_out(self, state):
        """
        Heartbeats and quota counters rebuild themselves on the next run.
        Keeping the archive small is what makes it affordable to keep many.
        """
        from scheduler.backup import create_archive
        names = _names(create_archive("T"))
        assert "heartbeat.log" not in names
        assert "quota_tracker.json" not in names

    def test_credentials_are_never_archived(self, state):
        """
        The deliberate exclusion. OAuth tokens are the one thing here that is
        NOT irreplaceable — re-authorising takes minutes. Copying them off-box
        would trade those minutes for a bundle of channel credentials in object
        storage, where a leak is a channel takeover.
        """
        from scheduler.backup import BACKUP_SET, create_archive
        assert not any("token" in entry or "oauth" in entry or "config" in entry
                       for entry in BACKUP_SET), BACKUP_SET
        (state / "youtube_token.json").write_text('{"refresh_token":"secret"}')
        names = _names(create_archive("T"))
        assert not any("token" in n for n in names), names

    def test_nothing_to_archive_writes_no_file(self, tmp_path):
        from config.settings import settings
        from scheduler.backup import create_archive
        settings.logs_dir = tmp_path
        assert create_archive("T") is None
        assert not list(tmp_path.glob("**/*.tar.gz"))


class TestRestore:
    def test_the_archive_round_trips(self, state, tmp_path):
        """A backup that cannot be unpacked is not a backup."""
        from scheduler.backup import create_archive
        archive = create_archive("T")
        restored = tmp_path / "restored"
        with tarfile.open(archive) as tar:
            tar.extractall(restored)
        scores = json.loads((restored / "performance_state.json").read_text())
        assert scores["hook"]["stat_first"] == 0.71
        assert (restored / "analytics" / "2026-08-28.json").exists()


class TestPruning:
    def test_only_the_newest_are_kept(self, state):
        from scheduler.backup import create_archive, prune, backup_dir
        for i in range(12):
            create_archive(f"2026010{i:02d}")
        assert prune(keep=8) == 4
        assert len(list(backup_dir().glob("*.tar.gz"))) == 8

    def test_pruning_keeps_the_most_recent_stamps(self, state):
        from scheduler.backup import create_archive, prune, backup_dir
        for i in range(5):
            create_archive(f"2026010{i}")
        prune(keep=2)
        remaining = sorted(p.name for p in backup_dir().glob("*.tar.gz"))
        assert "20260103" in remaining[0] and "20260104" in remaining[1]


class TestRemoteCopy:
    def test_no_command_configured_reports_failure(self, state, monkeypatch):
        """An archive beside the thing it protects is not off-box."""
        from scheduler.backup import create_archive, push_remote, REMOTE_CMD_ENV
        monkeypatch.delenv(REMOTE_CMD_ENV, raising=False)
        assert push_remote(create_archive("T")) is False

    def test_the_archive_path_is_substituted(self, state, monkeypatch, tmp_path):
        from scheduler.backup import create_archive, push_remote, REMOTE_CMD_ENV
        archive = create_archive("T")
        landed = tmp_path / "shipped.tar.gz"
        monkeypatch.setenv(REMOTE_CMD_ENV, f"cp {{archive}} {landed}")
        assert push_remote(archive) is True
        assert landed.exists()

    def test_a_failing_command_is_reported_not_raised(self, state, monkeypatch):
        from scheduler.backup import create_archive, push_remote, REMOTE_CMD_ENV
        monkeypatch.setenv(REMOTE_CMD_ENV, "false {archive}")
        assert push_remote(create_archive("T")) is False

    def test_a_missing_binary_is_survivable(self, state, monkeypatch):
        from scheduler.backup import create_archive, push_remote, REMOTE_CMD_ENV
        monkeypatch.setenv(REMOTE_CMD_ENV, "definitely-not-a-real-binary {archive}")
        assert push_remote(create_archive("T")) is False


class TestBackupHealthCheck:
    def test_no_archive_yet_warns(self, tmp_path):
        from config.settings import settings
        from monitor import health_report
        settings.logs_dir = tmp_path
        status, _, detail, _ = health_report._check_backups()
        assert status == health_report._WARN
        assert "no archive yet" in detail

    def test_a_local_only_archive_warns(self, state, monkeypatch):
        from scheduler.backup import create_archive, REMOTE_CMD_ENV
        from monitor import health_report
        monkeypatch.delenv(REMOTE_CMD_ENV, raising=False)
        create_archive("T")
        status, _, detail, _ = health_report._check_backups()
        assert status == health_report._WARN
        assert "only exists on the instance" in detail

    def test_a_shipped_recent_archive_is_ok(self, state, monkeypatch):
        from scheduler.backup import create_archive, REMOTE_CMD_ENV
        from monitor import health_report
        monkeypatch.setenv(REMOTE_CMD_ENV, "gsutil cp {archive} gs://b/")
        create_archive("T")
        assert health_report._check_backups()[0] == health_report._OK

    def test_a_stale_archive_fails(self, state, monkeypatch):
        """The weekly job has stopped running and nothing else would say so."""
        import time
        from scheduler.backup import create_archive, REMOTE_CMD_ENV
        from monitor import health_report
        monkeypatch.setenv(REMOTE_CMD_ENV, "gsutil cp {archive} gs://b/")
        archive = create_archive("T")
        old = time.time() - 30 * 86400
        os.utime(archive, (old, old))
        status, _, detail, _ = health_report._check_backups()
        assert status == health_report._FAIL
        assert "has not run" in detail


class TestRunBackupNeverRaises:
    def test_a_broken_logs_dir_is_survivable(self, tmp_path):
        from config.settings import settings
        from scheduler.backup import run_backup
        blocked = tmp_path / "logs"
        blocked.write_text("a file where a directory should be")
        settings.logs_dir = blocked
        assert run_backup() is None      # must not raise

    def test_a_successful_run_returns_the_archive(self, state, monkeypatch):
        from scheduler.backup import run_backup, REMOTE_CMD_ENV
        monkeypatch.delenv(REMOTE_CMD_ENV, raising=False)
        archive = run_backup()
        assert archive is not None and archive.exists()


class TestBackupJobIsRegistered:
    def test_the_weekly_job_exists(self):
        import inspect
        from scheduler import master_scheduler
        source = inspect.getsource(master_scheduler.start_scheduler)
        assert 'id="state_backup"' in source
        assert "run_state_backup" in source
