"""Build the daily report from the readings the collector wrote."""
from collections import defaultdict
from pathlib import Path

VALID_MIN, VALID_MAX = -60.0, 60.0
REPORT_DIR = Path("out/reports")


def _valid(lines):
    """Readings as (station, value), without the ones a sensor fault produced."""
    for line in lines:
        station, _, raw = line.partition("\t")
        try:
            value = float(raw)
        except ValueError:
            continue
        if not VALID_MIN <= value <= VALID_MAX:
            continue
        yield station, value


def build(lines) -> dict:
    """Minimum, maximum and mean temperature per station."""
    by_station = defaultdict(list)
    for station, value in _valid(lines):
        by_station[station].append(value)
    return {s: {"min": min(v), "max": max(v), "mean": round(sum(v) / len(v), 1)}
            for s, v in sorted(by_station.items())}


def write(report: dict, day: str) -> Path:
    """Write one day's report as a text file with a line per station."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{day}.txt"
    path.write_text("".join(f"{s}\t{r['min']}\t{r['max']}\t{r['mean']}\n" for s, r in report.items()))
    return path
