#!/usr/bin/env python3
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import render


def iso(hours=1):
    return (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=hours)
    ).replace(microsecond=0).isoformat()


def wallet():
    return {
        "address": "7xKX6bH8mN2pQ4rS9tU1vW3yZ5aB7cD9eF2gH4jK6mN8",
        "tier": "A",
        "smart_score": 78.4,
        "lp_adds": 3,
        "lp_pools": 2,
        "lp_size_usd": 12500,
        "lp_edge": 0.042,
        "lp_last_ts": iso(2),
        "tokens": 14,
        "win_rate": 0.62,
        "avg_peak_roi": 0.85,
        "trades": 22,
        "volume_usd": 3200,
        "last_alert_at": None,
    }


def events():
    return [
        {
            "kind": "add", "pool": "pool1", "pool_name": "HYPE / SOL",
            "size_usd": 1200, "net_edge": 0.031, "fee_yield_daily": 0.018,
            "block_ts": iso(2), "sig": "sig1",
        },
        {
            "kind": "remove", "pool": "pool1", "pool_name": "HYPE / SOL",
            "size_usd": 500, "net_edge": 0.01, "fee_yield_daily": 0.018,
            "block_ts": iso(5), "sig": "sig2",
        },
    ]


class RenderTest(unittest.TestCase):
    def test_wallet_alert_id(self):
        text = render.wallet_alert(wallet(), events(), lang="id")
        self.assertIn("SMART LP WALLET", text)
        self.assertIn(wallet()["address"], text)
        self.assertIn("HYPE / SOL", text)
        self.assertIn("solscan.io/account/", text)
        self.assertIn("+4.2%", text)

    def test_wallet_alert_en_and_both(self):
        en = render.wallet_alert(wallet(), events(), lang="en")
        self.assertIn("LP evidence", en)
        both = render.wallet_alert(wallet(), events(), lang="both")
        self.assertEqual(both.count("SMART LP WALLET"), 2)

    def test_html_escaping(self):
        row = wallet()
        ev = events()
        ev[0]["pool_name"] = "<script>alert(1)</script>"
        text = render.wallet_alert(row, ev, lang="en")
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_lp_event_alert(self):
        text = render.lp_event_alert(wallet(), events()[0], lang="id")
        self.assertIn("LP BARU", text)
        self.assertIn("HYPE / SOL", text)
        en = render.lp_event_alert(wallet(), events()[0], lang="en")
        self.assertIn("NEW LP", en)

    def test_lp_holder_alert(self):
        holding = {
            "pool": "pool1",
            "pool_name": "FLUKE / SOL",
            "pct": 0.1234,
            "value_usd": 5000.0,
            "rank": 2,
            "edge": 0.02,
            "fee_yield_daily": 0.03,
            "fetched_at": iso(1),
        }
        text = render.lp_holder_alert(wallet(), holding, lang="id")
        self.assertIn("TOP LP TERDETEKSI", text)
        self.assertIn("FLUKE / SOL", text)
        self.assertIn("12.34%", text)
        self.assertIn("$5.0K", text)
        self.assertIn("#2", text)
        en = render.lp_holder_alert(wallet(), holding, lang="en")
        self.assertIn("TOP LP DETECTED", en)

    def test_wallet_alert_mentions_holdings(self):
        row = wallet()
        row["lp_holdings"] = 2
        text = render.wallet_alert(row, events(), lang="en")
        self.assertIn("LP holds", text)

    def test_daily_report(self):
        stats = {
            "pools": 40, "winning_pools": 8, "wallets_with_lp": 12,
            "lp_events": 30, "alerts_sent": 5,
        }
        pools = [
            {
                "name": "HYPE / SOL", "dex": "orca", "vol_h24": 1_000_000,
                "tvl": 200_000, "fee_yield_daily": 0.015, "pool_score": 82,
            }
        ]
        text = render.daily_report([wallet()], pools, stats, lang="id")
        self.assertIn("Laporan Top LP Smart Wallet", text)
        self.assertIn("HYPE / SOL", text)
        self.assertIn("5 alert", text)

    def test_no_wallets_report(self):
        text = render.daily_report([], [], {}, lang="en")
        self.assertIn("No wallet qualified", text)

    def test_test_message(self):
        self.assertIn("connection test", render.test_message("en"))
        self.assertIn("Uji koneksi", render.test_message("id"))


if __name__ == "__main__":
    unittest.main()
