# Architecture

stationlog is a handful of small modules under `stationlog/`, one per job.

## Collecting

`stationlog/collector.py` polls every station in the settings and appends what each one answers to the readings file.

## Summarising

`stationlog/summary.py` reads the readings file and builds the daily report: the minimum, maximum and mean temperature per station.

## Pushing

`stationlog/push.py` posts the report rows to the dashboard.

## Alerts

`stationlog/alerts.py` counts the polls each station missed.
