#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring


class ScoringTest(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(scoring.clamp(-1), 0.0)
        self.assertEqual(scoring.clamp(2), 1.0)
        self.assertEqual(scoring.clamp(0.4), 0.4)

    def test_impermanent_loss(self):
        self.assertAlmostEqual(scoring.impermanent_loss(100, 100), 0.0)
        self.assertAlmostEqual(scoring.impermanent_loss(100, 400), -0.2)
        self.assertEqual(scoring.impermanent_loss(0, 400), 0.0)

    def test_fee_rate_and_yield(self):
        pool = {"vol_h24": 1_000_000, "tvl": 100_000, "fee_pct": 0.3}
        self.assertAlmostEqual(scoring.fee_rate_for(pool), 0.003)
        self.assertAlmostEqual(scoring.fee_yield_daily(pool), 0.03)
        self.assertAlmostEqual(
            scoring.fee_rate_for({"fee_pct": -1}), 0.003
        )

    def test_v_tvl(self):
        self.assertAlmostEqual(scoring.v_tvl({"vol_h24": 500, "tvl": 100}), 5.0)
        self.assertEqual(scoring.v_tvl({"tvl": 0}), 0.0)

    def test_pool_score_bounds(self):
        good = {
            "vol_h24": 5_000_000, "tvl": 500_000, "buyers_h1": 900,
            "chg_h24": 20, "chg_h1": 2,
        }
        bad = {"vol_h24": 0, "tvl": 0, "buyers_h1": 0, "chg_h24": -80}
        self.assertGreater(scoring.pool_score(good), scoring.pool_score(bad))
        self.assertGreaterEqual(scoring.pool_score(bad), 0.0)
        self.assertLessEqual(scoring.pool_score(good), 100.0)

    def test_token_stats(self):
        rows = [
            {
                "buy_usd": 100, "sell_usd": 80, "buy_qty": 100, "sell_qty": 40,
                "avg_buy": 1, "avg_sell": 2, "peak_price": 3, "current_price": 2,
            },
            {
                "buy_usd": 100, "sell_usd": 50, "buy_qty": 100, "sell_qty": 100,
                "avg_buy": 1, "avg_sell": 0.5, "peak_price": 1.1, "current_price": 0.6,
            },
        ]
        stats = scoring.token_stats(rows)
        self.assertEqual(stats["tokens"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertAlmostEqual(stats["win_rate"], 0.5)
        self.assertAlmostEqual(stats["avg_peak_roi"], 1.05)
        self.assertAlmostEqual(stats["realized_pnl"], -10.0)

    def test_trade_score(self):
        self.assertEqual(scoring.trade_score({}), 0.0)
        strong = {
            "tokens": 20, "win_rate": 0.8, "avg_peak_roi": 2.0,
            "realized_pnl": 1000, "volume_usd": 2000,
        }
        weak = {
            "tokens": 20, "win_rate": 0.1, "avg_peak_roi": 0.05,
            "realized_pnl": -500, "volume_usd": 2000,
        }
        self.assertGreater(scoring.trade_score(strong), scoring.trade_score(weak))
        self.assertLessEqual(scoring.trade_score(strong), 100.0)

    def test_lp_score(self):
        self.assertEqual(scoring.lp_score(0, 0, 0, 0, 0), 0.0)
        self.assertAlmostEqual(
            scoring.lp_score(8, 8, 0.05, 0, 80), 94.0, places=1
        )

    def test_lp_score_with_holdings(self):
        self.assertEqual(scoring.lp_score(0, 0, 0, 0, 0, hold_count=0), 0.0)
        held = scoring.lp_score(0, 1, 0.03, 0, 76, hold_count=1)
        self.assertGreater(held, 0.0)
        more = scoring.lp_score(0, 2, 0.03, 0, 76, hold_count=2)
        self.assertGreater(more, held)

    def test_smart_score_and_tier(self):
        self.assertAlmostEqual(scoring.smart_score(90, 50), 78.0)
        self.assertLessEqual(scoring.smart_score(0, 100, has_lp_evidence=False), 45.0)
        self.assertEqual(scoring.tier(90), "S")
        self.assertEqual(scoring.tier(75), "A")
        self.assertEqual(scoring.tier(60), "B")
        self.assertEqual(scoring.tier(45), "C")
        self.assertEqual(scoring.tier(10), "D")


if __name__ == "__main__":
    unittest.main()
