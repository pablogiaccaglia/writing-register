# stationlog

stationlog collects readings from a network of weather stations, builds a daily report from them and sends the report to a dashboard.

## Running it

The settings live in `stationlog.toml`, which `stationlog/config.py` reads. The dashboard address can also come from the STATIONLOG_DASHBOARD_URL environment variable, which wins over the file. Start the collector with `python -m stationlog.cli poll`. It waits 600 seconds between two passes over the stations unless the settings say otherwise.

Once a day, `scripts/push.sh` builds the report for the day before and sends it. The script stops at the first command that fails, so a report that could not be built is never sent.

## Where the data goes

Readings are appended to `out/readings.txt`, one line per station and poll. The folder is not tracked by git. Daily reports are removed after a year.

See [the architecture](docs/ARCHITECTURE.md) and [the operations guide](docs/OPERATIONS.md).
