# Alerts

The alerts module, `stationlog/alerts.py`, watches for stations that stop answering.

It counts missed polls per station.

It prints a warning when a station has missed too many.

A station that answers again starts from zero.
