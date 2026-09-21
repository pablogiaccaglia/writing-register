# Operations

## Daily push

`scripts/push.sh` runs once a day from cron. It builds yesterday's report and then pushes it. The report step reads the whole readings file each time.

## Commands

- `python -m stationlog.cli poll` runs the collector, which keeps polling until it is stopped.
- `python -m stationlog.cli prune` runs retention, which deletes nothing unless pruning is turned on.

## Reports

Reports are written under `out/reports/`, one text file per day with a line per station.

## Retention

Archived readings live in `out/archive/`. `stationlog/retention.py` can delete the ones older than the number of days in the settings, but only when pruning is turned on. Every run of the prune command deletes the archived readings older than 400 days. Pruning is turned on by setting STATIONLOG_PRUNE to 1 in the environment. A file's age is measured from the time it was last modified.

## Stations that stop answering

The collector never retries a station that does not answer. After enough missed polls in a row, `stationlog/alerts.py` prints a warning.

## Settings

`stationlog/config.py` reads `stationlog.toml` from the working directory. When the file is missing, stationlog starts with no stations at all. The number of days to keep readings defaults to 400.
