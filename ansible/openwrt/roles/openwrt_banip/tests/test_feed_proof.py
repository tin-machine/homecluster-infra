"""No router/network: fresh content, kernel-set and fail-closed proof contracts."""
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("feed_proof", Path(__file__).resolve().parents[1] / "files/banip_feed_proof.py")
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
NOW = 1_780_000_000
PAYLOAD = {"dshield": b"# synthetic ranges\n1.2.3.0 1.2.3.127 25 10 fixture\n1.2.3.128 1.2.3.255 25 10 fixture\n",
           "feodo": b"# synthetic IPs; no traffic generated\n8.8.8.8\n8.8.4.4\n"}


def nft(name, elements):
    return json.dumps({"nftables": [{"metainfo": {}}, {"set": {"family": "inet", "table": "banIP", "name": name + ".v4",
                         "type": "ipv4_addr", "flags": ["interval"], "elem": elements}}]}).encode()


class FeedProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.root.chmod(0o700)
        self.config = {"schema": 1, "feeds": ["dshield", "feodo"]}
        self.config_path = self.write("config.json", self.config)
        self.path = self.root / "proof.json"
        self.runtime = self.write("runtime.json", {"status": "active", "last_run": "mode: reload, date / time: 01/01/2026 00:00:00, duration: 1s",
                                                "active_feeds": ["allowlist.v4", "dshield.v4", "feodo.v4"]})
        self.definitions = self.write("definitions.json", proof.REGISTRY)
        for attr, value in [("RUNTIME", self.runtime), ("DEFINITIONS", self.definitions), ("CUSTOM", self.root / "custom")]:
            p = patch.object(proof, attr, value); p.start(); self.addCleanup(p.stop)
        self.prior = proof.state(self.config, self.path, now=NOW)

    def write(self, name, value):
        p = self.root / name; p.write_text(json.dumps(value)); p.chmod(0o600); return p

    def runner(self, args, **kwargs):
        if args[:3] == ["uci", "-q", "get"]:
            if args[-1].endswith("ban_feed"):
                return b"dshield feodo"
            return b"1" if args[-1].endswith("ban_enabled") else b""
        if args[-1] == "dshield.v4":
            return nft("dshield", [{"prefix": {"addr": "1.2.3.0", "len": 24}}])
        if args[-1] == "feodo.v4":
            return nft("feodo", ["8.8.8.8", "8.8.4.4"])
        self.fail("unexpected command")

    def snapshot(self):
        return proof.snapshot(self.config, runner=self.runner)

    def verify(self, prior=None, **kwargs):
        return proof.verify(self.config, prior or self.prior, reader=lambda _: self.snapshot(),
                            downloader=kwargs.get("downloader", lambda name: PAYLOAD[name]), now=NOW)

    def test_fresh_https_content_matches_collapsed_kernel_sets(self):
        current = self.snapshot()
        record = self.verify()
        self.assertEqual(record["feeds"]["dshield"]["last_verified"]["source_records"], 2)
        self.assertEqual(record["feeds"]["dshield"]["last_verified"]["count"], 1)
        self.assertEqual(proof.summary(self.config, record, current, now=NOW)["status"], "verified")
        self.assertEqual(record["feeds"]["dshield"]["last_verified"]["scope"], proof.SCOPE)

    def test_kernel_range_and_counter_wrappers_are_content_equivalent(self):
        a = proof.nft_set("dshield", nft("dshield", [{"range": ["1.2.3.0", "1.2.3.255"]}]))
        b = proof.nft_set("dshield", nft("dshield", [{"elem": {"val": {"prefix": {"addr": "1.2.3.0", "len": 24}},
                                                                         "counter": {"packets": 9, "bytes": 20}}}]))
        self.assertEqual(a["canonical_sha256"], b["canonical_sha256"])
        self.assertEqual(a["canonical_sha256"], proof.parse_feed("dshield", PAYLOAD["dshield"])["canonical_sha256"])

    def test_bad_provider_data_never_verifies(self):
        bad = [b"", b"# empty\n", b"<html>error</html>", b"1.2.3.0 1.2.3.254 24", b"1.2.3.1 1.2.3.255 24",
               b"1.2.3.0 1.2.3.255 0", b"10.1.0.0 10.1.0.255 24", b"127.0.0.0 127.0.0.255 24",
               b"192.0.2.0 192.0.2.255 24", b"224.0.0.0 224.0.0.255 24", b"::1 ::2 128", b"\xff"]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                proof.parse_feed("dshield", data)
        for data in [b"8.8.8.8 extra", b"8.8.8.8/32", b"0.0.0.0", b"::1", b"255.255.255.255"]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                proof.parse_feed("feodo", data)
        with self.assertRaisesRegex(ValueError, "size_limit"):
            proof.parse_feed("feodo", b"x" * (proof.MAX_BYTES + 1))

    def test_unsupported_configuration_never_executes_provider_rule_text(self):
        for fields in ({"feeds": []}, {"feeds": ["unknown"]}, {"feeds": ["feodo", "feodo"]}, {"schema": True}):
            self.write("config.json", dict(self.config, **fields))
            with self.assertRaises(ValueError):
                proof.configuration(self.config_path)
        changed = copy.deepcopy(proof.REGISTRY); changed["dshield"]["rule"] = "feed 1; command"
        self.write("definitions.json", changed)
        with self.assertRaisesRegex(ValueError, "feed_definition_changed"):
            self.snapshot()

    def test_actual_set_or_active_runtime_change_prevents_success(self):
        before = self.snapshot(); after = copy.deepcopy(before); after["generation"] += "changed"
        snapshots = iter([before, after])
        result = proof.verify(self.config, self.prior, reader=lambda _: next(snapshots), downloader=lambda name: PAYLOAD[name], now=NOW)
        self.assertEqual(proof.summary(self.config, result, after, now=NOW)["status"], "unknown")
        self.assertNotIn("last_verified", result["feeds"]["dshield"])
        after = copy.deepcopy(before); after["sets"]["dshield"]["canonical_sha256"] = "0" * 64
        result = proof.verify(self.config, self.prior, reader=lambda _: after, downloader=lambda name: PAYLOAD[name], now=NOW)
        self.assertEqual(result["feeds"]["dshield"]["last_check"]["reason"], "active_set_content_mismatch")
        self.assertEqual(proof.summary(self.config, result, after, now=NOW)["status"], "error")

    def test_download_failure_preserves_prior_proof_but_does_not_hide_new_error(self):
        previous = self.verify()
        def unavailable(_):
            raise OSError("fixture TLS failure; no request sent")
        result = self.verify(previous, downloader=unavailable)
        self.assertEqual(result["feeds"]["dshield"]["last_verified"], previous["feeds"]["dshield"]["last_verified"])
        self.assertEqual(proof.summary(self.config, result, self.snapshot(), now=NOW + 60)["status"], "error")
        with patch.object(proof.time, "time", return_value=NOW + 1):
            result = proof.failed_check(self.config, previous, "refresh_or_verification_failed")
        self.assertEqual(proof.summary(self.config, result, self.snapshot(), now=NOW + 2)["status"], "error")

    def test_cached_old_or_changed_set_proof_is_not_fresh(self):
        good = self.verify(); current = self.snapshot()
        self.assertEqual(proof.summary(self.config, good, current, now=NOW + proof.MAX_AGE + 1)["status"], "stale")
        current["sets"]["feodo"]["canonical_sha256"] = "0" * 64
        self.assertEqual(proof.summary(self.config, good, current, now=NOW)["status"], "unknown")
        self.assertEqual(proof.summary(self.config, good, None, now=NOW)["status"], "unknown")
        absent = proof.summary(self.config, self.prior, self.snapshot(), now=NOW)
        self.assertIsNone(absent["age_seconds"])
        self.assertTrue(all(f["last_verified_count"] is None and f["verified_at"] is None for f in absent["feeds"]))

    def test_inactive_malformed_or_custom_runtime_cannot_inherit_proof(self):
        normal = json.loads(self.runtime.read_text())
        for change in [{"status": "processing"}, {"last_run": "not a generation"}, {"active_feeds": ["dshield.v4", "feodo.v6"]},
                       {"last_run": "mode: reload, date / time: 32/01/2026 00:00:00,"}]:
            self.write("runtime.json", dict(normal, **change))
            with self.assertRaises(ValueError):
                self.snapshot()
        self.write("runtime.json", normal)
        self.write("custom", {})
        with self.assertRaisesRegex(ValueError, "custom_feed_definition_unsupported"):
            self.snapshot()

    def test_atomic_state_private_files_and_partial_write_guard(self):
        good = self.verify(); proof.save(self.path, good)
        self.assertEqual(proof.state(self.config, self.path, now=NOW), good)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.path.with_name(self.path.name + ".part").write_text("partial")
        with self.assertRaisesRegex(ValueError, "partial_proof"):
            proof.state(self.config, self.path, now=NOW)
        with self.assertRaises(FileExistsError):
            proof.save(self.path, good)
        self.path.with_name(self.path.name + ".part").unlink()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "nonprivate"):
            proof.state(self.config, self.path, now=NOW)

    def test_corrupt_or_future_state_does_not_return_verified(self):
        good = self.verify()
        for value in (True, float("nan"), NOW + 3600, 1):
            changed = copy.deepcopy(good); changed["feeds"]["feodo"]["last_verified"]["at"] = value
            self.write("proof.json", changed)
            with self.assertRaisesRegex(ValueError, "invalid_proof_state"):
                proof.state(self.config, self.path, now=NOW)
        changed = copy.deepcopy(good); changed["definition_sha256"] = "0" * 64; self.write("proof.json", changed)
        with self.assertRaisesRegex(ValueError, "invalid_proof_state"):
            proof.state(self.config, self.path, now=NOW)

    def test_nft_identity_unknown_expressions_and_default_prefix_are_rejected(self):
        for element in ({"concat": []}, {"prefix": {"addr": "0.0.0.0", "len": 0}},
                        {"prefix": {"addr": "1.2.3.0", "len": True}},
                        {"elem": {"val": "8.8.8.8", "counter": {"packets": True}}}):
            with self.subTest(element=element), self.assertRaises(ValueError):
                proof.nft_set("dshield", nft("dshield", [element]))
        with self.assertRaisesRegex(ValueError, "nft_set_identity"):
            proof.nft_set("feodo", nft("dshield", ["8.8.8.8"]))

    def test_unsafe_redirects_are_rejected_before_following(self):
        handler = proof.HTTPSRedirects()
        for url in ("http://feeds.dshield.org/block.txt", "https://example.test/feed", "https://user@feeds.dshield.org/feed",
                    "https://feeds.dshield.org:8443/feed"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "unsafe_feed_redirect"):
                handler.redirect_request(None, None, 302, "redirect", {}, url)

    def test_bounded_child_output_timeout_and_nonzero_exit(self):
        self.assertEqual(proof.run([sys.executable, "-c", "print('ok')"], maximum=8), b"ok\n")
        for code, maximum, timeout, reason in [("print('x'*10000)", 8, 2, "command_output_limit"),
                                               ("import time;time.sleep(1)", 8, .03, "command_timeout"),
                                               ("raise SystemExit(3)", 8, 2, "command_failed")]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                proof.run([sys.executable, "-c", code], maximum=maximum, timeout=timeout)
        self.assertEqual(proof.run([sys.executable, "-c", "raise SystemExit(1)"], allow_missing=True), b"")
        with self.assertRaisesRegex(ValueError, "command_failed"):
            proof.run([sys.executable, "-c", "raise SystemExit(127)"], allow_missing=True)

    def test_changed_uci_source_paths_cannot_inherit_stock_proof(self):
        def changed(args, **kwargs):
            if args[-1] == "banip.global.ban_feedfile":
                return b"/unexpected/provider.json"
            return self.runner(args, **kwargs)
        with self.assertRaisesRegex(ValueError, "unsupported_feed_definition_path"):
            proof.snapshot(self.config, runner=changed)

    def fetch_response(self, *, status=200, length=None, chunks=None):
        class Response:
            headers = {} if length is None else {"Content-Length": length}
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read1(self, maximum): return next(self.chunks)
        response = Response(); response.status = status; response.chunks = iter(chunks or [PAYLOAD["feodo"], b""])
        class Opener:
            def open(self, request, timeout):
                assert request.full_url == proof.REGISTRY["feodo"]["url_4"] and timeout == 8
                return response
        return Opener()

    def test_fetch_requires_200_and_enforces_header_stream_and_wall_limits(self):
        with patch.object(proof.urllib.request, "build_opener", return_value=self.fetch_response()):
            self.assertEqual(proof.fetch("feodo"), PAYLOAD["feodo"])
        for response, reason in [(self.fetch_response(status=304), "feed_http_status"),
                                 (self.fetch_response(status=500), "feed_http_status"),
                                 (self.fetch_response(length=str(proof.MAX_BYTES + 1)), "feed_size_limit"),
                                 (self.fetch_response(length="-1"), "feed_size_limit"),
                                 (self.fetch_response(chunks=[b"x" * (proof.MAX_BYTES + 1), b""]), "feed_size_limit")]:
            with patch.object(proof.urllib.request, "build_opener", return_value=response), self.assertRaisesRegex(ValueError, reason):
                proof.fetch("feodo")
        with patch.object(proof.urllib.request, "build_opener", return_value=self.fetch_response()), \
                patch.object(proof.time, "monotonic", side_effect=[0, 0, 21]), self.assertRaisesRegex(ValueError, "feed_download_timeout"):
            proof.fetch("feodo")

    def test_state_symlink_hardlink_and_unknown_parent_are_rejected(self):
        good = self.verify(); target = self.write("other.json", good)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "unsafe_json_file"):
            proof.state(self.config, self.path, now=NOW)
        self.path.unlink(); os.link(target, self.path)
        with self.assertRaisesRegex(ValueError, "unsafe_json_file"):
            proof.state(self.config, self.path, now=NOW)
        self.path.unlink()
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "unsafe_state_directory"):
            proof.state(self.config, self.path, now=NOW)
        self.root.chmod(0o700)

    def test_status_action_is_read_only_and_never_downloads(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        output = io.StringIO()
        with patch.object(proof, "STATE", self.path), patch.object(proof, "resource_guard"), \
                patch.object(proof, "snapshot", side_effect=proof.ProofError("banip_not_active")), \
                patch.object(proof, "fetch", side_effect=AssertionError("status must not fetch")), \
                patch.object(sys, "argv", ["proof", "status", "--config", str(self.config_path)]), \
                patch("sys.stdout", output):
            self.assertEqual(proof.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "unknown")
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_refresh_calls_only_fixed_reload_and_saves_verified_receipt(self):
        old = self.snapshot(); new = dict(old, generation=old["generation"] + " next")
        calls = []
        with patch.object(proof, "STATE", self.path), patch.object(proof, "resource_guard"), \
                patch.object(proof, "snapshot", side_effect=[old, new, new, new, new]), \
                patch.object(proof, "fetch", side_effect=lambda name: PAYLOAD[name]), \
                patch.object(proof, "run", side_effect=lambda args, **kw: calls.append(args) or b""), \
                patch.object(sys, "argv", ["proof", "refresh", "--config", str(self.config_path)]), patch("sys.stdout", io.StringIO()):
            self.assertEqual(proof.main(), 0)
        self.assertEqual(calls, [["/etc/init.d/banip", "reload"]])
        saved = proof.state(self.config, self.path)
        self.assertTrue(all(x["last_check"]["status"] == "verified" for x in saved["feeds"].values()))

    def test_refresh_failure_cannot_hide_behind_previous_success(self):
        good = self.verify(); proof.save(self.path, good)
        current = self.snapshot()
        with patch.object(proof, "STATE", self.path), patch.object(proof, "resource_guard"), \
                patch.object(proof, "snapshot", return_value=current), \
                patch.object(proof, "run", side_effect=proof.ProofError("command_failed")), \
                patch.object(proof.time, "time", return_value=NOW + 1), \
                patch.object(sys, "argv", ["proof", "refresh", "--config", str(self.config_path)]), patch("sys.stdout", io.StringIO()):
            self.assertEqual(proof.main(), 1)
        saved = proof.state(self.config, self.path, now=NOW + 1)
        self.assertEqual(saved["feeds"]["feodo"]["last_verified"], good["feeds"]["feodo"]["last_verified"])
        self.assertTrue(all(x["last_check"]["status"] == "error" for x in saved["feeds"].values()))

    def test_concurrent_verifier_never_reloads_or_fetches(self):
        lock = self.path.with_name(self.path.name + ".lock")
        lock.touch(mode=0o600)
        with lock.open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(proof, "STATE", self.path), patch.object(proof, "resource_guard"), \
                    patch.object(proof, "fetch", side_effect=AssertionError("must not fetch")), \
                    patch.object(proof, "run", side_effect=AssertionError("must not reload")), \
                    patch.object(sys, "argv", ["proof", "refresh", "--config", str(self.config_path)]):
                with self.assertRaises(BlockingIOError):
                    proof.main()


if __name__ == "__main__":
    unittest.main()
