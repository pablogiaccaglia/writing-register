# Architecture

stationlog is a handful of small modules under `stationlog/`, one per job. Each job is started by a subcommand of the command line module.

## Collecting

`stationlog/collector.py` polls every station in the settings and appends what each one answers to the readings file. A station that does not answer is asked again, up to three times by default, with a longer wait after each failed attempt.

## Summarising

`stationlog/summary.py` reads the readings file and builds the daily report: the minimum, maximum and mean temperature per station. Every reading in the file counts toward the report. The mean is rounded to one decimal place. Each report is written to its own file, named after the day. The summariser then sends the report rows to the dashboard.

## Pushing

`stationlog/push.py` posts the report rows to the dashboard. It sends the rows in batches of 30. Nothing is sent when no dashboard address is set. Each batch is one JSON request with a timeout of 30 seconds.

## Alerts

`stationlog/alerts.py` counts the polls each station missed. When a station misses 5 polls in a row, the module prints one line to standard error. A poll the station answers resets its count to zero.
