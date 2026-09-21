# stationlog

stationlog collects readings from a network of weather stations, builds a daily report from them and sends the report to a dashboard.

## Running it

The settings live in `stationlog.toml`, which `stationlog/config.py` reads. Start the collector with `python -m stationlog.cli poll`.

Once a day, `scripts/push.sh` builds the report for the day before and sends it.

## Where the data goes

Readings are appended to `out/readings.txt`. The folder is not tracked by git.

See [the architecture](docs/ARCHITECTURE.md) and [the operations guide](docs/OPERATIONS.md).
