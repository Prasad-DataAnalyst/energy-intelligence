"""
scheduler/backup.py — get the unrecoverable state off this instance.

Everything runs on one VM with no copy of anything anywhere else. Lose it
and you lose the record of every video ever published, the performance
scores accumulated over months, and the history that stops the channel
repeating itself. None of that can be rebuilt from the YouTube API.

Deliberately does NOT back up config/*.json.

The OAuth tokens and client secrets live there, and they are the one thing
here that is *not* irreplaceable — re-authorising takes a few minutes.
Copying them off-box trades those few minutes for a bundle of channel
credentials sitting in object storage, where a leak is a channel takeover.
That is a bad trade, so the archive holds data only and a rebuilt instance
re-authorises.
"""
import logging
import os
import shlex
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
KEEP_ARCHIVES = 8          # ~two months of weekly runs

# What actually cannot be rebuilt. Anything regenerable on the next run —
# heartbeats, quota counters, the monitor log — is left out so the archive
# stays small enough to be worth keeping many of.
BACKUP_SET = [
    "upload_manifest.jsonl",      # every video published; the dead-man's source of truth
    "performance_state.json",     # EMA scores earned over months of publishing
    "content_history.json",       # what has been covered, so topics do not repeat
    "hook_history.json",          # hook rotation, same reason
    "style_state.json",
    "playlist_ids.json",          # YouTube playlist IDs, tedious to re-map by hand
    "claude_usage.jsonl",         # spend history; the burn trend needs a past to compare to
    "failed_queue.json",          # work still owed
    "shorts_music_history.json",
    "pipeline_daily.jsonl",       # per-day run reports
    "analytics",                  # directory: daily stats pulled from YouTube
    "pipeline_state",             # directory: per-slot checkpoints
]

# Shell command run on the finished archive, with {archive} substituted, e.g.
#   BACKUP_REMOTE_CMD="gsutil cp {archive} gs://my-bucket/driftwire326/"
#   BACKUP_REMOTE_CMD="rclone copy {archive} remote:driftwire326"
# Without it the archive only ever exists on the instance it is protecting,
# which is not a backup — the health report says so.
REMOTE_CMD_ENV = "BACKUP_REMOTE_CMD"


def backup_dir() -> Path:
    return settings.logs_dir / BACKUP_DIR_NAME


def create_archive(stamp: "str | None" = None) -> "Path | None":
    """Tar up the irreplaceable state. Returns the archive, or None if empty."""
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = backup_dir()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"driftwire326-state-{stamp}.tar.gz"

    included = 0
    with tarfile.open(archive, "w:gz") as tar:
        for name in BACKUP_SET:
            source = settings.logs_dir / name
            if not source.exists():
                continue
            try:
                tar.add(source, arcname=name)
                included += 1
            except Exception as exc:
                logger.warning("Could not archive %s: %s", name, exc)

    if not included:
        archive.unlink(missing_ok=True)
        logger.warning("Nothing to back up — no state files found")
        return None
    logger.info("Backup archive: %s (%d items, %.1f KB)",
                archive.name, included, archive.stat().st_size / 1024)
    return archive


def prune(keep: int = KEEP_ARCHIVES) -> int:
    """Drop the oldest archives. Returns how many were removed."""
    archives = sorted(backup_dir().glob("driftwire326-state-*.tar.gz"))
    removed = 0
    for old in (archives[:-keep] if keep > 0 else archives):
        try:
            old.unlink()
            removed += 1
        except Exception as exc:
            logger.warning("Could not remove %s: %s", old.name, exc)
    return removed


def push_remote(archive: Path) -> bool:
    """
    Copy the archive off the instance using the operator's own command.

    Not run through a shell: the template is split with shlex and executed
    directly, so a path containing a space cannot become two arguments.
    """
    template = (os.getenv(REMOTE_CMD_ENV) or "").strip()
    if not template:
        logger.warning(
            "%s not set — the archive is still on the machine it protects",
            REMOTE_CMD_ENV)
        return False
    try:
        command = [part.replace("{archive}", str(archive))
                   for part in shlex.split(template)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error("Remote backup failed (%s): %s",
                         result.returncode, result.stderr[-300:])
            return False
        logger.info("Backup copied off-instance via %s", command[0])
        return True
    except Exception as exc:
        logger.error("Remote backup command failed: %s", exc)
        return False


def run_backup() -> "Path | None":
    """Weekly job: archive, ship, prune. Never raises."""
    try:
        archive = create_archive()
        if archive is None:
            return None
        push_remote(archive)
        pruned = prune()
        if pruned:
            logger.info("Pruned %d old archive(s)", pruned)
        return archive
    except Exception as exc:
        logger.error("Backup run failed: %s", exc)
        return None
