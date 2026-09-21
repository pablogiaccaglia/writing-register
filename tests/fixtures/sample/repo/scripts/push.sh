#!/bin/sh
# Build yesterday's report and send it to the dashboard. Run once a day from cron.
set -e -u
day=$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F)
python -m stationlog.cli report "$day"
python -m stationlog.cli push "$day"
