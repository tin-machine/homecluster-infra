#!/usr/bin/env python3
"""Compare fresh, bounded HTTPS feed contents with active kernel sets.

This does not write nft rules or claim that banIP's own download avoided a
backup restore. Only refresh invokes the existing, fixed banIP reload command.
"""
import argparse
from datetime import datetime
import fcntl
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import ssl
import stat
import subprocess
import time
import urllib.parse
import urllib.request

REGISTRY = {
    "dshield": {"url_4": "https://feeds.dshield.org/block.txt", "rule": "feed 13", "chain": "in"},
    "feodo": {"url_4": "https://feodotracker.abuse.ch/downloads/ipblocklist.txt", "rule": "feed 1", "chain": "inout"},
}
STATE = Path("/etc/homecluster-security/banip-proof-state.json")
RUNTIME = Path("/var/run/banIP/banIP.runtime.json")
DEFINITIONS = Path("/etc/banip/banip.feeds")
CUSTOM = Path("/etc/banip/banip.custom.feeds")
MAX_BYTES = 1024 * 1024
MAX_RECORDS = 5000
MAX_AGE = 48 * 3600
HEX = re.compile(r"^[a-f0-9]{64}$")
SCOPE = "independent_https_content_equals_active_nft_set"


class ProofError(ValueError):
    pass


def require(test, reason):
    if not test:
        raise ProofError(reason)


def epoch(value, now):
    return type(value) in (int, float) and math.isfinite(value) and 1_600_000_000 <= value <= now + 300


def read_json(path, maximum, private=False):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_json_file")
    if private:
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600, "nonprivate_json_file")
    require(info.st_size <= maximum, "json_size_limit")
    value = json.loads(path.read_text())
    require(isinstance(value, dict), "invalid_json_object")
    return value


def configuration(path):
    value = read_json(path, 4096, private=True)
    require(set(value) == {"schema", "feeds"} and type(value["schema"]) is int and value["schema"] == 1,
            "invalid_configuration")
    feeds = value["feeds"]
    require(isinstance(feeds, list) and 1 <= len(feeds) <= 2 and
            all(isinstance(x, str) and x in REGISTRY for x in feeds) and len(set(feeds)) == len(feeds),
            "unsupported_feed_configuration")
    return {"schema": 1, "feeds": sorted(feeds)}


def definition_hash(config):
    data = {name: REGISTRY[name] for name in config["feeds"]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(args, maximum=MAX_BYTES, timeout=5, allow_missing=False):
    """Bound output and wall time, without a shell or killing unrelated processes."""
    child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    output, deadline = bytearray(), time.monotonic() + timeout
    selector.register(child.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            require(time.monotonic() < deadline, "command_timeout")
            for key, _ in selector.select(.1):
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(block)
                    require(len(output) <= maximum, "command_output_limit")
        code = child.wait(timeout=max(.1, deadline - time.monotonic()))
        require(code == 0 or (allow_missing and code == 1), "command_failed")
        return b"" if code == 1 else bytes(output)
    finally:
        selector.close()
        child.stdout.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def canonical(networks):
    require(networks and len(networks) <= MAX_RECORDS, "feed_record_limit")
    require(all(x.version == 4 and x.prefixlen > 0 for x in networks), "invalid_address_family")
    normalized = list(ipaddress.collapse_addresses(networks))
    encoded = "\n".join(str(x) for x in normalized).encode()
    return {"canonical_sha256": hashlib.sha256(encoded).hexdigest(), "count": len(normalized)}


def parse_feed(name, data):
    require(name in REGISTRY and isinstance(data, bytes) and 0 < len(data) <= MAX_BYTES, "feed_size_limit")
    try:
        lines = data.decode("ascii").splitlines()
        networks = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if name == "dshield":
                # Supported provider table: start, end, prefix length, metadata.
                require(len(parts) >= 3 and parts[2].isdigit(), "feed_format_invalid")
                start, end = ipaddress.IPv4Address(parts[0]), ipaddress.IPv4Address(parts[1])
                prefix = int(parts[2])
                require(16 <= prefix <= 32, "unsupported_prefix_size")
                network = ipaddress.IPv4Network(str(start) + "/" + str(prefix), strict=True)
                require(network.broadcast_address == end, "feed_range_mismatch")
            else:
                require(len(parts) == 1, "feed_format_invalid")
                address = ipaddress.IPv4Address(parts[0])
                network = ipaddress.IPv4Network(str(address) + "/32")
            require(network.is_global and not network.is_multicast, "nonpublic_feed_prefix")
            networks.append(network)
            require(len(networks) <= MAX_RECORDS, "feed_record_limit")
        return dict(canonical(networks), source_records=len(networks), source_sha256=hashlib.sha256(data).hexdigest())
    except ProofError:
        raise
    except (UnicodeError, ValueError, TypeError) as error:
        raise ProofError("feed_format_invalid") from error


def nft_set(name, data):
    require(len(data) <= MAX_BYTES, "nft_size_limit")
    value = json.loads(data)
    sets = [x["set"] for x in value["nftables"] if "set" in x]
    require(len(sets) == 1, "nft_set_identity")
    current = sets[0]
    require(current["family"] == "inet" and current["table"] == "banIP" and current["name"] == name + ".v4" and
            current["type"] == "ipv4_addr" and set(current["flags"]) == {"interval"}, "nft_set_identity")
    elements = current.get("elem", [])
    require(isinstance(elements, list) and 1 <= len(elements) <= MAX_RECORDS, "nft_element_limit")
    networks = []
    for element in elements:
        if isinstance(element, dict) and set(element) == {"elem"}:
            wrapper = element["elem"]
            require(isinstance(wrapper, dict) and "val" in wrapper and set(wrapper) <= {"val", "counter"}, "nft_element_invalid")
            counter = wrapper.get("counter", {})
            require(isinstance(counter, dict) and set(counter) <= {"packets", "bytes"} and
                    all(type(x) is int and x >= 0 for x in counter.values()), "nft_element_invalid")
            element = wrapper["val"]
        if isinstance(element, str):
            networks.append(ipaddress.IPv4Network(element, strict=False))
        elif isinstance(element, dict) and set(element) == {"prefix"}:
            prefix = element["prefix"]
            require(set(prefix) == {"addr", "len"} and type(prefix["len"]) is int, "nft_element_invalid")
            networks.append(ipaddress.IPv4Network(str(prefix["addr"]) + "/" + str(prefix["len"]), strict=True))
        elif isinstance(element, dict) and set(element) == {"range"}:
            pair = element["range"]
            require(isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair), "nft_element_invalid")
            networks.extend(ipaddress.summarize_address_range(*[ipaddress.IPv4Address(x) for x in pair]))
        else:
            raise ProofError("nft_element_invalid")
        require(len(networks) <= MAX_RECORDS, "nft_element_limit")
    return dict(canonical(networks), set_elements=len(elements))


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        require(target.scheme == "https" and target.hostname in {"feeds.dshield.org", "feodotracker.abuse.ch"} and
                not target.username and not target.password and target.port in (None, 443), "unsafe_feed_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(name):
    opener = urllib.request.build_opener(HTTPSRedirects(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(REGISTRY[name]["url_4"], headers={"User-Agent": "homecluster-feed-proof/1"})
    deadline = time.monotonic() + 20
    with opener.open(request, timeout=8) as response:
        require(response.status == 200, "feed_http_status")
        size = response.headers.get("Content-Length")
        require(size is None or (size.isdigit() and 0 < int(size) <= MAX_BYTES), "feed_size_limit")
        data = bytearray()
        while True:
            require(time.monotonic() < deadline, "feed_download_timeout")
            block = response.read1(65536)
            if not block:
                break
            data.extend(block)
            require(len(data) <= MAX_BYTES, "feed_size_limit")
        return bytes(data)


def snapshot(config, runner=run):
    runtime = read_json(RUNTIME, 32768)
    require(runtime.get("status") == "active" and isinstance(runtime.get("last_run"), str) and runtime["last_run"],
            "banip_not_active")
    match = re.match(r"^mode: (?:reload|start|restart|boot), date / time: (\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}),", runtime["last_run"])
    require(match is not None and len(runtime["last_run"]) <= 512, "invalid_runtime_generation")
    try:
        datetime.strptime(match[1], "%d/%m/%Y %H:%M:%S")
    except ValueError as error:
        raise ProofError("invalid_runtime_generation") from error
    actual = runner(["uci", "-q", "get", "banip.global.ban_feed"], maximum=4096).decode().split()
    require(sorted(actual) == config["feeds"] and runner(["uci", "-q", "get", "banip.global.ban_enabled"], maximum=64).strip() == b"1",
            "configured_feeds_changed")
    for key, expected in (("ban_feedfile", DEFINITIONS), ("ban_customfeedfile", CUSTOM)):
        configured = runner(["uci", "-q", "get", "banip.global." + key], maximum=4096, allow_missing=True).strip()
        require(configured in (b"", str(expected).encode()), "unsupported_feed_definition_path")
    definitions = read_json(DEFINITIONS, 512 * 1024)
    require(not CUSTOM.is_symlink() and (not CUSTOM.exists() or CUSTOM.stat().st_size == 0), "custom_feed_definition_unsupported")
    active = runtime.get("active_feeds")
    require(isinstance(active, list) and len(active) <= 128 and
            all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", x) for x in active) and
            len(set(active)) == len(active), "invalid_active_feeds")
    sets = {}
    for name in config["feeds"]:
        definition = definitions[name]
        require(all(definition.get(k) == v for k, v in REGISTRY[name].items()) and not definition.get("url_6"),
                "feed_definition_changed")
        require(name + ".v4" in active and name + ".v6" not in active, "active_family_changed")
        sets[name] = nft_set(name, runner(["nft", "-j", "list", "set", "inet", "banIP", name + ".v4"]))
    # A concurrent reload or set change cannot inherit this verification.
    end = read_json(RUNTIME, 32768)
    require(end.get("status") == "active" and end.get("last_run") == runtime["last_run"] and
            end.get("active_feeds") == active, "runtime_changed_during_snapshot")
    return {"generation": runtime["last_run"], "sets": sets}


def state(config, path=None, now=None):
    path = STATE if path is None else path
    now = time.time() if now is None else now
    require(epoch(now, now), "clock_untrusted")
    parent = path.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
            "unsafe_state_directory")
    part = path.with_name(path.name + ".part")
    require(not part.exists() and not part.is_symlink(), "partial_proof_requires_reconciliation")
    if not path.exists():
        require(not path.is_symlink(), "unsafe_state_file")
        return {"schema": 1, "definition_sha256": definition_hash(config), "feeds": {}}
    value = read_json(path, 16384, private=True)
    require(type(value.get("schema")) is int and value["schema"] == 1 and value.get("definition_sha256") == definition_hash(config)
            and isinstance(value.get("feeds"), dict) and set(value["feeds"]) <= set(config["feeds"]), "invalid_proof_state")
    for record in value["feeds"].values():
        require(isinstance(record, dict) and set(record) <= {"last_check", "last_verified"}, "invalid_proof_state")
        check = record["last_check"]
        require(check["status"] in ("verified", "error", "unknown") and epoch(check["at"], now) and
                isinstance(check["reason"], str) and re.fullmatch(r"[a-z_]{1,64}", check["reason"]), "invalid_proof_state")
        if "last_verified" in record:
            good = record["last_verified"]
            require(epoch(good["at"], now) and good["scope"] == SCOPE and
                    all(isinstance(good[x], str) and HEX.fullmatch(good[x]) for x in ("source_sha256", "canonical_sha256")) and
                    type(good["count"]) is int and 1 <= good["count"] <= MAX_RECORDS, "invalid_proof_state")
    return value


def verify(config, prior, *, reader=None, downloader=None, now=None):
    reader = snapshot if reader is None else reader
    downloader = fetch if downloader is None else downloader
    fixed_time = now is not None
    now = time.time() if now is None else now
    require(epoch(now, now), "clock_untrusted")
    out = json.loads(json.dumps(prior))
    results, before, after = {}, None, None
    try:
        before = reader(config)
        for name in config["feeds"]:
            try:
                results[name] = parse_feed(name, downloader(name))
            except (ValueError, OSError) as error:
                results[name] = {"error": str(error) if isinstance(error, ProofError) else "feed_download_failed"}
        after = reader(config)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, ProofError) else "snapshot_unavailable"
        results = {name: {"error": reason} for name in config["feeds"]}
    if not fixed_time:
        finished = time.time()
        require(epoch(finished, finished) and -1 <= finished - now <= 90, "clock_shift_during_verification")
        now = finished
    for name in config["feeds"]:
        record = out["feeds"].setdefault(name, {})
        result = results[name]
        if "error" in result:
            check = {"status": "error", "reason": result["error"]}
        elif before != after:
            check = {"status": "unknown", "reason": "concurrent_set_or_runtime_change"}
        elif result["canonical_sha256"] != after["sets"][name]["canonical_sha256"]:
            check = {"status": "error", "reason": "active_set_content_mismatch"}
        else:
            check = {"status": "verified", "reason": "verified"}
            record["last_verified"] = dict(result, at=now, scope=SCOPE)
        record["last_check"] = dict(check, at=now)
    return out


def failed_check(config, prior, reason):
    now = time.time()
    require(epoch(now, now), "clock_untrusted")
    out = json.loads(json.dumps(prior))
    for name in config["feeds"]:
        out["feeds"].setdefault(name, {})["last_check"] = {"status": "error", "reason": reason, "at": now}
    return out


def resource_guard():
    lines = [x.split() for x in Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemAvailable:")]
    require(len(lines) == 1 and lines[0][2] == "kB" and int(lines[0][1]) >= 64 * 1024, "memory_reserve_low")
    require(os.getloadavg()[0] <= 2 * (os.cpu_count() or 1), "router_load_high")


def summary(config, proof, current=None, now=None):
    now = time.time() if now is None else now
    feeds = []
    for name in config["feeds"]:
        record = proof["feeds"].get(name, {})
        good, check = record.get("last_verified"), record.get("last_check", {})
        age = max(0, int(now - good["at"])) if good else None
        if current is None:
            status, reason = "unknown", "current_snapshot_unavailable"
        elif check.get("status") != "verified":
            status, reason = check.get("status", "unknown"), check.get("reason", "proof_missing")
        elif not good or good["canonical_sha256"] != current["sets"][name]["canonical_sha256"]:
            status, reason = "unknown", "current_set_changed"
        elif age > MAX_AGE:
            status, reason = "stale", "proof_expired"
        else:
            status, reason = "verified", "verified"
        feeds.append(dict(feed=name + ".v4", status=status, reason=reason, verified_at=good["at"] if good else None,
                          age_seconds=age, last_verified_count=good["count"] if good else None))
    status = next((x for x in ("error", "unknown", "stale") if any(f["status"] == x for f in feeds)), "verified")
    ages = [f["age_seconds"] for f in feeds if f["age_seconds"] is not None]
    return dict(status=status, age_seconds=max(ages) if len(ages) == len(feeds) else None, feeds=feeds)


def save(path, value):
    parent = path.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
            "unsafe_state_directory")
    part = path.with_name(path.name + ".part")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":")); stream.flush(); os.fsync(stream.fileno())
    os.replace(part, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("verify", "refresh", "status"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = configuration(args.config)
    resource_guard()
    prior = state(config)
    if args.action == "status":
        try:
            current = snapshot(config)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            current = None
        print(json.dumps(summary(config, prior, current), separators=(",", ":")))
        return 0
    lock = os.open(STATE.with_name(STATE.name + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid() and
                stat.S_IMODE(info.st_mode) == 0o600, "unsafe_proof_lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = state(config)
        try:
            if args.action == "refresh":
                before = snapshot(config)
                run(["/etc/init.d/banip", "reload"], maximum=16384, timeout=15)
                deadline = time.monotonic() + 120
                while True:
                    require(time.monotonic() < deadline, "banip_reload_deadline")
                    try:
                        current = snapshot(config)
                        if current["generation"] != before["generation"]:
                            break
                    except (ValueError, OSError, KeyError, TypeError):
                        pass
                    time.sleep(1)
            value = verify(config, prior)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            value = failed_check(config, prior, "refresh_or_verification_failed")
        save(STATE, value)
        try:
            current = snapshot(config)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            current = None
        result = summary(config, value, current)
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result["status"] == "verified" else 1
    finally:
        os.close(lock)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, ProofError) else "proof_unavailable"
        print(json.dumps({"status": "unknown", "reason": reason}))
        raise SystemExit(2)
