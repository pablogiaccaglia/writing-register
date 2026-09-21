"""Poll every station and append its readings to the readings file."""
import time
import urllib.request
from pathlib import Path

from .config import Settings

READINGS = Path("out/readings.txt")


def fetch(station: str, retries: int):
    """The station's latest reading, or None when it does not answer."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"http://{station}/latest", timeout=10) as reply:
                return reply.read().decode()
        except OSError:
            time.sleep(2 ** attempt)
    return None


def poll(settings: Settings) -> list:
    """One pass over every station; returns the stations that did not answer."""
    silent = []
    READINGS.parent.mkdir(parents=True, exist_ok=True)
    with READINGS.open("a") as out:
        for station in settings.stations:
            line = fetch(station, settings.retries)
            if line is None:
                silent.append(station)
                continue
            out.write(f"{station}\t{line.strip()}\n")
    return silent


def run(settings: Settings, on_pass=None) -> None:
    """Poll forever, waiting poll_seconds between passes."""
    while True:
        silent = poll(settings)
        if on_pass is not None:
            on_pass(silent)
        time.sleep(settings.poll_seconds)
