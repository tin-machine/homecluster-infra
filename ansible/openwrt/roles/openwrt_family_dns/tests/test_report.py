import contextlib
import datetime as dt
import io
import json
import pathlib
import runpy
import unittest
from unittest.mock import Mock, patch

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "files/family-dns-report"
module = runpy.run_path(str(SCRIPT))
DAY = dt.date(2026, 1, 2)


def row(name, stamp, **extra):
    return {
        "question": {"name": name, "class": "IN", "type": "A"},
        "time": stamp, "client": "192.0.2.1", "reason": "NotFilteredNotFound",
        **extra,
    }


class QueryLogReport(unittest.TestCase):
    def test_authenticated_pages_and_local_day(self):
        start = dt.datetime.combine(DAY, dt.time.min, module["JST"])
        stamp = "2026-01-01T15:00:00.000000123Z"
        first = row("foo.example.co.jp", stamp)
        older = row("before.example.com", "2026-01-01T14:59:59Z")
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(json.dumps(page).encode()) for page in [
                {"data": [first], "oldest": stamp}, {"data": [older], "oldest": ""},
            ]
        ]
        records = module["records"]
        with patch.dict(records.__globals__, build_opener=Mock(return_value=opener)):
            rows = list(records(start, start + dt.timedelta(days=1), "Basic fixture"))
        self.assertEqual([r for _, r in rows], [first])
        req = opener.open.call_args_list[1].args[0]
        self.assertIn("older_than=2026-01-01T15%3A00%3A00.000000123Z", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Basic fixture")

    def test_grouping_public_suffix_service_and_anonymous_clients(self):
        stamp = dt.datetime.combine(DAY, dt.time.min, module["JST"])
        entries = [
            row("a.example.co.jp", stamp), row("b.example.co.jp", stamp),
            row("youtube.com.example.org", stamp),
            row("www.youtube.com", stamp, client_info={"name": "<device>"}),
            row("www.youtube.com", stamp, reason="FilteredBlockedService",
                service_name="youtube", client_info={"name": "<device>"}),
        ]
        report = module["report"]
        result = io.StringIO()
        with patch.dict(report.__globals__, records=lambda *_: [(stamp, e) for e in entries]):
            with contextlib.redirect_stdout(result):
                report(DAY, ("user", "", "test-credential"))
        html = result.getvalue()
        self.assertIn("保存済み 5 件", html)
        self.assertNotIn("192.0.2.1", html)
        self.assertNotIn("<device>", html)
        self.assertIn("<td>example.co.jp</td><td>a.example.co.jp</td><td>2</td>", html)
        self.assertIn("<td>未分類</td><td>example.org</td>", html)
        self.assertIn("<td>&lt;device&gt;</td><td>YouTube</td><td>youtube.com</td>"
                      "<td>www.youtube.com</td><td>2</td><td>1</td>", html)


if __name__ == "__main__":
    unittest.main()
