"""Command line: stationlog poll | report DAY | push DAY | prune."""
import sys

from . import alerts, collector, push, retention, summary
from .config import load


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    settings = load()
    command = args[0] if args else "poll"
    if command == "poll":
        watcher = alerts.Alerts()
        collector.run(settings, lambda silent: watcher.record(silent, settings.stations))
    elif command == "report":
        lines = collector.READINGS.read_text().splitlines()
        summary.write(summary.build(lines), args[1])
    elif command == "push":
        report = summary.REPORT_DIR / f"{args[1]}.txt"
        rows = [dict(zip(("station", "min", "max", "mean"), line.split("\t")))
                for line in report.read_text().splitlines()]
        push.push(settings, rows)
    elif command == "prune":
        retention.prune(settings)
    else:
        print(f"unknown command {command}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
