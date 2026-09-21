"""Tell the operator when a station stops answering."""
import sys

ALERT_AFTER = 5


class Alerts:
    """Missed polls per station, kept in memory for as long as the collector runs."""

    def __init__(self, after: int = ALERT_AFTER):
        self.after = after
        self.misses = {}

    def record(self, silent: list, stations: list) -> list:
        """Count missed polls; return the stations that just reached the limit."""
        crossed = []
        for station in stations:
            if station in silent:
                self.misses[station] = self.misses.get(station, 0) + 1
                if self.misses[station] == self.after:
                    crossed.append(station)
            else:
                self.misses[station] = 0
        for station in crossed:
            print(f"stationlog: {station} missed {self.after} polls in a row", file=sys.stderr)
        return crossed
