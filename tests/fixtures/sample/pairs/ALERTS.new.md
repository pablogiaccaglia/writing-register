# Alerts

The alerts module, `stationlog/alerts.py`, watches for stations that stop answering. It emails the operator as well.

It counts missed polls per station. The counts are saved to disk between runs.

It prints a warning when a station has missed too many. The limit is three missed polls.

A station that answers again starts from zero. Its count lives in memory only.
