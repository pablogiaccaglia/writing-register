"""Load the stationlog settings from a TOML file and the environment."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("stationlog.toml")


@dataclass
class Settings:
    stations: list = field(default_factory=list)
    poll_seconds: int = 600
    retries: int = 3
    keep_days: int = 400
    dashboard_url: str = ""
    prune: bool = False


def load(path: Path = DEFAULT_PATH) -> Settings:
    """The settings in `path`, with the defaults above for anything it leaves out."""
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    settings = Settings(stations=list(data.get("stations", [])))
    settings.poll_seconds = int(data.get("poll_seconds", settings.poll_seconds))
    settings.retries = int(data.get("retries", settings.retries))
    settings.keep_days = int(data.get("keep_days", settings.keep_days))
    settings.dashboard_url = os.environ.get("STATIONLOG_DASHBOARD_URL", data.get("dashboard_url", ""))
    settings.prune = os.environ.get("STATIONLOG_PRUNE") == "1"
    return settings
