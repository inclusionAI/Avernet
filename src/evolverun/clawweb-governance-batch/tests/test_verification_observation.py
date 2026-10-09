"""Legacy observation path and NAS/HTTP contract regressions (no external writes)."""
from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import test_batch as fixtures
from test_batch import FakeAnalyst, FakeCenter, FakeNAS, NOW
from clawweb_batch.adapters.nas import NASReader
from clawweb_batch.core import validate_request
from clawweb_batch.pipeline import run


class EmptyHistory:
    def daily_counts(self, start, end):
        return [{"dt": (NOW - timedelta(days=day)).strftime("%Y%m%d"),
                 "is_cron": lane, "raw_sampled_cnt": 10}
                for day in range(1, 15) for lane in (0, 1)]

    def ranking(self, start, end):
        return []

    def sessions(self, *args):
        return []


class LegacyCenter(FakeCenter):
    def detail(self, item_id):
        value = super().detail(item_id)
        value.update(userGuidance="human explanation", rootCauseSummary="scheduled job fails")
        return value


class ReadableNAS(FakeNAS):
    def __init__(self):
        self.enumerated = []
        self.scanned = []

    def sources(self, owner, bot):
        self.enumerated.append((owner, bot))
        return {"sources": ["runtime/agent"], "complete": True, "warnings": []}

    def scope_root(self, *args):
        return Path("runtime")

    def scan_since(self, *args):
        self.scanned.append(args)
        return {"tasks": [], "complete": True, "warnings": []}


class ObservationTests(unittest.TestCase):
    def test_legacy_idle_bot_uses_real_read_path_and_plans_zero_session_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = fixtures.RuntimeTests().config(Path(tmp))
            nas, center = ReadableNAS(), LegacyCenter()
            result = run(cfg, EmptyHistory(), center, FakeAnalyst(), nas, now=NOW, apply=False)
            self.assertEqual(result["errors"], [])
            self.assertEqual(nas.enumerated, [("u", "b")])
            self.assertEqual(len(nas.scanned), 1)
            plan = result["verification"][0]
            self.assertEqual(plan["payload"]["outcome"], "DISAPPEARED")
            self.assertEqual(plan["payload"]["newSessionCount"], 0)
            self.assertIs(plan["payload"]["allowZeroSession"], True)
            self.assertEqual(center.writes, [])
            # Zero-session confirmation is explicit and cannot become a generic override.
            invalid = copy.deepcopy(plan)
            del invalid["payload"]["allowZeroSession"]
            with self.assertRaises(ValueError):
                validate_request(invalid)
            invalid = copy.deepcopy(plan)
            invalid["payload"]["status"] = "RESOLVED"
            with self.assertRaises(ValueError):
                validate_request(invalid)

    def test_missing_partition_does_not_close_idle_legacy_item(self):
        class MissingHistory(EmptyHistory):
            def daily_counts(self, start, end):
                return [r for r in super().daily_counts(start, end) if r["dt"] != "20260927"]
        with tempfile.TemporaryDirectory() as tmp:
            result = run(fixtures.RuntimeTests().config(Path(tmp)), MissingHistory(), LegacyCenter(),
                         FakeAnalyst(), ReadableNAS(), now=NOW, apply=False)
            self.assertEqual(result["verification"][0]["payload"]["outcome"], "INSUFFICIENT_DATA")

    def test_nas_read_failure_does_not_close_idle_legacy_item(self):
        class BrokenNAS(ReadableNAS):
            def scan_since(self, *args):
                return {"tasks": [], "complete": False, "warnings": ["unreadable session"]}
        with tempfile.TemporaryDirectory() as tmp:
            result = run(fixtures.RuntimeTests().config(Path(tmp)), EmptyHistory(), LegacyCenter(),
                         FakeAnalyst(), BrokenNAS(), now=NOW, apply=False)
            self.assertEqual(result["verification"][0]["payload"]["outcome"], "INSUFFICIENT_DATA")

    def test_nas_sources_exact_owner_bot_and_missing_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            matching = base / "prod_staff_u_openclaw_b" / ".openclaw/agents/main/sessions"
            matching.mkdir(parents=True)
            (base / "prod_staff_other_openclaw_b/.openclaw/agents/main/sessions").mkdir(parents=True)
            (base / "prod_staff_u_openclaw_b2/.openclaw/agents/main/sessions").mkdir(parents=True)
            reader = NASReader((base,))
            value = reader.sources("u", "b")
            self.assertTrue(value["complete"])
            self.assertEqual(value["sources"], [str(matching.parent)])
            self.assertFalse(reader.sources("u", "missing")["complete"])
            self.assertFalse(NASReader((base, base / "missing")).sources("u", "b")["complete"])
            with self.assertRaises(ValueError):
                reader.sources("*", "b")
            # A symlinked session path cannot establish current evidence coverage.
            matching.rmdir()
            matching.symlink_to(base, target_is_directory=True)
            result = reader.scan_since(str(matching.parent), "u", "b", NOW.isoformat(), 10000)
            self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
