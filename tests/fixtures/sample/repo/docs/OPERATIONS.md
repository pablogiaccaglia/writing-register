# Operations

## Daily push

`scripts/push.sh` runs once a day from cron. It builds yesterday's report and then pushes it.

## Commands

- `python -m stationlog.cli poll` runs the collector.
- `python -m stationlog.cli prune` runs retention.

## Reports

Reports are written under `out/reports/`.

## Retention

Archived readings live in `out/archive/`. `stationlog/retention.py` can delete the ones older than the number of days in the settings, but only when pruning is turned on.

## Stations that stop answering

The collector retries a station that does not answer. After enough missed polls in a row, `stationlog/alerts.py` prints a warning.

## Settings

`stationlog/config.py` reads `stationlog.toml` from the working directory.
