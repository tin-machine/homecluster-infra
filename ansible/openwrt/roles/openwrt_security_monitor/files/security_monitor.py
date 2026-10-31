#!/usr/bin/env python3
"""Emit a bounded security heartbeat; missing/invalid evidence stays unknown."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import threading
import time


RUNTIME = Path("/var/run/banIP/banIP.runtime.json")
LAST_RUN = re.compile(r"^mode: ([a-z]+), date / time: (\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}),")
FEED_PROOF_COMMAND = ("/usr/libexec/homecluster-banip-feed-proof.py", "status", "--config",
                      "/etc/homecluster-security/banip-proof.json")
FEED_PROOF_TIMEOUT = 12


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
    if type(data.get("feed_verification_enabled", False)) is not bool:
        raise ValueError("invalid_feed_verification_flag")
    return data


def read_feed_proof():
    # The fixed status action never reloads banIP. Its own session contains
    # only this read-only helper and any UCI/nft readers it starts.
    child = subprocess.Popen(FEED_PROOF_COMMAND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    output, deadline = bytearray(), time.monotonic() + FEED_PROOF_TIMEOUT
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise ValueError("feed_proof_timeout")
            for key, _ in selector.select(.05):
                block = os.read(key.fd, 4096)
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(block)
                    if len(output) > 2048:
                        raise ValueError("oversized_feed_proof")
        if child.wait(timeout=max(.1, deadline - time.monotonic())) != 0:
            raise ValueError("feed_proof_unavailable")
        return json.loads(output)
    finally:
        selector.close()
        child.stdout.close()
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def checked_feed_proof(value, now):
    if not isinstance(value, dict) or value.get("status") not in {"verified", "error", "unknown", "stale"}:
        raise ValueError("invalid_feed_proof")
    rows = value.get("feeds")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
        raise ValueError("invalid_feed_proof")
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("feed") not in {"dshield.v4", "feodo.v4"} or row.get("status") not in {"verified", "error", "unknown", "stale"}:
            raise ValueError("invalid_feed_proof")
        stamp, age, count = row.get("verified_at"), row.get("age_seconds"), row.get("last_verified_count")
        if stamp is None:
            if age is not None or count is not None or row["status"] in {"verified", "stale"}:
                raise ValueError("invalid_feed_proof")
        else:
            if (type(stamp) not in (int, float) or not math.isfinite(stamp) or not 1_600_000_000 <= stamp <= now + 300 or
                    type(age) is not int or age < 0 or abs(age - max(0, int(now - stamp))) > 30 or
                    type(count) is not int or not 1 <= count <= 5000 or
                    (row["status"] == "verified" and age > 48 * 3600)):
                raise ValueError("invalid_feed_proof")
        result.append(dict(feed=row["feed"], status=row["status"], verified_at=stamp, age_seconds=age,
                           last_verified_count=count))
    if len({x["feed"] for x in result}) != len(result):
        raise ValueError("invalid_feed_proof")
    combined = next((status for status in ("error", "unknown", "stale") if any(x["status"] == status for x in result)), "verified")
    ages = [x["age_seconds"] for x in result if x["age_seconds"] is not None]
    maximum = max(ages) if len(ages) == len(result) else None
    if value["status"] != combined or value.get("age_seconds") != maximum:
        raise ValueError("invalid_feed_proof")
    return {"banip_feed_verification": combined, "banip_feed_max_age_seconds": maximum, "banip_feed_results": result}


def heartbeat(sensor, *, runtime=RUNTIME, now=None, feed_verification=False, proof_reader=None):
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
    if feed_verification and record["banip_status"] == "active" and "banip_error" not in record:
        try:
            if type(feed_verification) is not bool:
                raise ValueError("invalid_feed_verification_flag")
            record.update(checked_feed_proof((proof_reader or read_feed_proof)(), now))
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            record.update(banip_feed_verification="unknown", banip_feed_error="proof_unavailable",
                          banip_feed_max_age_seconds=None, banip_feed_results=None)
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
        message = json.dumps(heartbeat(settings["sensor"], feed_verification=settings.get("feed_verification_enabled", False)),
                             separators=(",", ":"))
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
