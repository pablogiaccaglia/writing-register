"""Delete archived readings older than the configured number of days."""
import time
from pathlib import Path

from .config import Settings

ARCHIVE = Path("out/archive")


def old_files(directory: Path, keep_days: int, now=None) -> list:
    """The archived files last modified more than keep_days ago."""
    cutoff = (now or time.time()) - keep_days * 86400
    return sorted(p for p in directory.glob("*.txt") if p.stat().st_mtime < cutoff)


def prune(settings: Settings) -> list:
    """Remove archived readings past keep_days, only when pruning is turned on."""
    doomed = old_files(ARCHIVE, settings.keep_days)
    if not settings.prune:
        return []
    for path in doomed:
        path.unlink()
    return doomed
