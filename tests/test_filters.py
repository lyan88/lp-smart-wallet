#!/usr/bin/env python3
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import filters


def good_pool(**over):
    pool = {
        "address": "pool",
        "name": "TOK / SOL",
        "dex": "orca",
        "vol_h24": 1_000_000,
        "tvl": 200_000,
        "chg_h1": 1.0,
        "chg_h24": 10.0,
        "buyers_h1": 100,
        "fee_pct": 0.3,
    }
    pool.update(over)
    return pool


class FilterTest(unittest.TestCase):
    def test_winning_pool(self):
        self.assertTrue(filters.is_winning_pool(good_pool()))
        self.assertEqual(filters.pool_rejection_reasons(good_pool()), [])

    def test_reject_low_volume(self):
        reasons = filters.pool_rejection_reasons(good_pool(vol_h24=1000))
        self.assertTrue(any("volume" in r for r in reasons))

    def test_reject_dump(self):
        reasons = filters.pool_rejection_reasons(good_pool(chg_h1=-50))
        self.assertTrue(any("dump" in r for r in reasons))

    def test_reject_low_buyers(self):
        reasons = filters.pool_rejection_reasons(good_pool(buyers_h1=1))
        self.assertTrue(any("buyer" in r for r in reasons))

    def test_apply_metrics(self):
        pool = filters.apply_pool_metrics(good_pool())
        self.assertTrue(pool["winning"])
        self.assertIn("pool_score", pool)
        self.assertIn("fee_yield_daily", pool)

    def test_short_helpers(self):
        self.assertEqual(filters.short_addr("abcdefghijklmnop", 3, 3), "abc...nop")
        self.assertEqual(filters.short_money(1_500_000), "$1.50M")
        self.assertEqual(filters.short_money(2_500), "$2.5K")
        self.assertEqual(filters.short_money(-1000), "-$1.0K")
        self.assertEqual(filters.short_pct(1.25), "+1.2%")

    def test_alert_ready(self):
        now = datetime.datetime(2026, 10, 30, tzinfo=datetime.timezone.utc)
        row = {
            "address": "wallet",
            "lp_adds": 2,
            "lp_size_usd": 1000,
            "smart_score": 80,
            "last_alert_at": None,
        }
        self.assertTrue(filters.alert_ready(row, now=now))
        row["last_alert_at"] = (now - datetime.timedelta(hours=2)).isoformat()
        self.assertFalse(filters.alert_ready(row, now=now))
        row["last_alert_at"] = (now - datetime.timedelta(days=2)).isoformat()
        self.assertTrue(filters.alert_ready(row, now=now))
        row["smart_score"] = 10
        self.assertFalse(filters.alert_ready(row, now=now))

    def test_holder_counts_as_lp_evidence(self):
        row = {
            "address": "wallet",
            "lp_adds": 0,
            "lp_holdings": 1,
            "lp_size_usd": 1000,
            "smart_score": 60,
            "last_alert_at": None,
        }
        self.assertTrue(filters.wallet_has_lp_evidence(row))
        self.assertTrue(filters.wallet_eligible(row))
        self.assertFalse(
            filters.wallet_has_lp_evidence(dict(row, lp_holdings=0))
        )

    def test_age_days(self):
        now = datetime.datetime(2026, 10, 30, tzinfo=datetime.timezone.utc)
        self.assertAlmostEqual(
            filters.age_days((now - datetime.timedelta(hours=12)).isoformat(), now),
            0.5,
            places=3,
        )
        self.assertGreater(filters.age_days("", now), 999)


if __name__ == "__main__":
    unittest.main()
