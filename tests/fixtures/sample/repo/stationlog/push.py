"""Send the rows of a daily report to the dashboard."""
import json
import urllib.request

from .config import Settings

BATCH = 50


def batches(rows, size=BATCH):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def push(settings: Settings, rows: list) -> int:
    """Post the rows in batches of BATCH; returns how many batches were sent."""
    if not settings.dashboard_url:
        return 0
    sent = 0
    for batch in batches(rows):
        body = json.dumps({"rows": batch}).encode()
        request = urllib.request.Request(settings.dashboard_url, data=body,
                                         headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=30)
        sent += 1
    return sent
