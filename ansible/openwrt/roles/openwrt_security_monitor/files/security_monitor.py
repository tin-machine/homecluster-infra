#!/usr/bin/env python3
"""Emit a bounded security heartbeat; missing/invalid evidence stays unknown."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import signal
import subprocess
import threading
import time


RUNTIME = Path("/var/run/banIP/banIP.runtime.json")
LAST_RUN = re.compile(r"^mode: ([a-z]+), date / time: (\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}),")


def read_json(path, maximum):
    with path.open("rb") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("oversized_json")
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("invalid_json_object")
    return data


def config(path):
    data = read_json(path, 4096)
    sensor = data.get("sensor")
    interval = data.get("interval_seconds")
    if not isinstance(sensor, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", sensor):
        raise ValueError("invalid_sensor")
    if type(interval) is not int or not 60 <= interval <= 300:
        raise ValueError("invalid_interval")
    return data


def heartbeat(sensor, *, runtime=RUNTIME, now=None):
    now = time.time() if now is None else now
    record = {
        "schema": 1, "security_source": "openwrt", "sensor": sensor,
        "log_type": "heartbeat",
        "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "banip_status": "unknown", "banip_last_run_utc": None,
        "banip_run_age_seconds": None, "banip_run_mode": None,
        "banip_feed_verification": "unknown",
    }
    try:
        data = read_json(runtime, 32768)
        status = data.get("status")
        if status not in {"active", "processing", "stopped", "error", "disabled"}:
            raise ValueError("invalid_runtime_status")
        record["banip_status"] = status
        version = data.get("backend_ver", "unknown")
        if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", version):
            record["banip_version"] = version
        feeds = data.get("active_feeds")
        if isinstance(feeds, list) and len(feeds) <= 128 and all(
            isinstance(f, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", f) for f in feeds
        ):
            record["banip_feed_count"] = len(feeds)
        else:
            raise ValueError("invalid_runtime_feeds")
        last = data.get("last_run")
        match = LAST_RUN.match(last) if isinstance(last, str) else None
        if match:
            # banIP writes router-local time, not UTC.
            stamp = datetime.strptime(match[2], "%d/%m/%Y %H:%M:%S").astimezone(timezone.utc)
            age = now - stamp.timestamp()
            if age < -300:
                record["banip_error"] = "runtime_clock_skew"
            else:
                record["banip_last_run_utc"] = stamp.isoformat()
                record["banip_run_age_seconds"] = max(0, int(age))
                record["banip_run_mode"] = match[1]
        else:
            record["banip_error"] = "last_run_unknown"
    except FileNotFoundError:
        record["banip_error"] = "runtime_missing"
    except OSError:
        record["banip_error"] = "runtime_unreadable"
    except (ValueError, TypeError):
        record["banip_status"] = "unknown"
        record["banip_error"] = "runtime_invalid"
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        settings = config(args.config)
    except (OSError, ValueError):
        raise SystemExit("invalid security monitor configuration") from None
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        message = json.dumps(heartbeat(settings["sensor"]), separators=(",", ":"))
        if args.once:
            print(message)
            return
        # Never put captured traffic, credentials or arbitrary log bodies here.
        try:
            subprocess.run(["logger", "-p", "daemon.info", "-t", "homecluster-security", "--", message],
                           check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise SystemExit("security heartbeat delivery failed") from None
        stop.wait(settings["interval_seconds"])


if __name__ == "__main__":
    main()
