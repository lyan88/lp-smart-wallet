#!/usr/bin/env python3
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import filters
import scoring


def iso(delta_hours=0):
    return (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=delta_hours)
    ).replace(microsecond=0).isoformat()


def sample_pool():
    return filters.apply_pool_metrics(
        {
            "address": "pool1",
            "name": "TOK / SOL",
            "dex": "orca",
            "base_mint": "base",
            "quote_mint": "quote",
            "base_symbol": "TOK",
            "quote_symbol": "SOL",
            "price_usd": 2.0,
            "quote_price_usd": 150.0,
            "tvl": 100_000,
            "vol_h24": 1_000_000,
            "vol_h1": 10_000,
            "chg_h1": 1.0,
            "chg_h24": 5.0,
            "buyers_h1": 100,
            "fee_pct": 0.3,
        }
    )


def sample_trades(wallet="walletA"):
    return [
        {
            "id": "t1", "pool": "pool1", "wallet": wallet, "token": "base",
            "side": "buy", "price_usd": 1.0, "amount_usd": 100.0,
            "token_amount": 100.0, "block_ts": iso(2), "tx_hash": "h1",
        },
        {
            "id": "t2", "pool": "pool1", "wallet": wallet, "token": "base",
            "side": "sell", "price_usd": 2.0, "amount_usd": 80.0,
            "token_amount": 40.0, "block_ts": iso(1), "tx_hash": "h2",
        },
    ]


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.open_db(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_state(self):
        self.assertIsNone(db.state_get(self.conn, "x"))
        db.state_set(self.conn, "x", {"a": 1})
        self.assertEqual(db.state_get(self.conn, "x"), {"a": 1})

    def test_pool_upsert(self):
        pool = sample_pool()
        self.assertEqual(db.upsert_pools(self.conn, [pool]), 1)
        stored = db.get_pool(self.conn, "pool1")
        self.assertTrue(stored["winning"])
        self.assertAlmostEqual(stored["pool_score"], pool["pool_score"])
        self.assertEqual(len(db.winning_pools(self.conn)), 1)

    def test_trades_and_tokens(self):
        db.upsert_pools(self.conn, [sample_pool()])
        self.assertEqual(db.insert_trades(self.conn, sample_trades()), 2)
        self.assertEqual(db.insert_trades(self.conn, sample_trades()), 0)
        db.refresh_wallet_tokens(self.conn)
        tokens = db.wallet_tokens(self.conn, "walletA")
        self.assertEqual(len(tokens), 1)
        row = tokens[0]
        self.assertEqual(row["buys"], 1)
        self.assertEqual(row["sells"], 1)
        self.assertAlmostEqual(row["avg_buy"], 1.0)
        self.assertAlmostEqual(row["avg_sell"], 2.0)
        self.assertAlmostEqual(row["peak_price"], 2.0)

    def test_trade_only_wallet_is_capped(self):
        db.upsert_pools(self.conn, [sample_pool()])
        db.insert_trades(self.conn, sample_trades())
        db.refresh_wallet_tokens(self.conn)
        db.ensure_trade_wallets(self.conn)
        db.refresh_wallet_scores(self.conn)
        row = db.get_wallet(self.conn, "walletA")
        self.assertEqual(row["trades"], 2)
        self.assertGreater(row["trade_score"], 0)
        self.assertLessEqual(row["smart_score"], 45.0)
        self.assertIn(row["tier"], ("C", "D"))
        self.assertLess(row["smart_score"], 70.0)

    def test_lp_event_and_score(self):
        db.upsert_pools(self.conn, [sample_pool()])
        event = {
            "sig": "sig1", "wallet": "walletLP", "pool": "pool1",
            "program": "orca", "dex": "orca", "kind": "add",
            "confidence": "high", "base_delta": -10.0, "quote_delta": -150.0,
            "size_usd": 2000.0, "price_usd": 2.0, "pool_score": 80.0,
            "fee_yield_daily": 0.03, "chg_h24": 5.0, "block_ts": iso(0),
        }
        self.assertEqual(db.insert_lp_events(self.conn, [event]), 1)
        self.assertEqual(db.insert_lp_events(self.conn, [event]), 0)
        db.update_lp_edges(self.conn)
        db.refresh_wallet_scores(self.conn)
        row = db.get_wallet(self.conn, "walletLP")
        self.assertEqual(row["lp_adds"], 1)
        self.assertEqual(row["lp_pools"], 1)
        self.assertGreater(row["lp_score"], 0)
        self.assertGreater(row["smart_score"], 0)
        agg = db.lp_aggregate(self.conn, "walletLP")
        self.assertEqual(agg["adds"], 1)
        self.assertGreater(agg["avg_edge"], 0)
        events = db.wallet_lp_events(self.conn, "walletLP")
        self.assertEqual(events[0]["pool_name"], "TOK / SOL")

    def test_lp_holders_and_aggregate(self):
        db.upsert_pools(self.conn, [sample_pool()])
        result = db.upsert_lp_holders(
            self.conn,
            "pool1",
            "lpMint111111111111111111111111111111111",
            1000.0,
            [
                {"wallet": "holderA", "amount": 100.0, "pct": 0.1, "rank": 1},
                {"wallet": "holderB", "amount": 50.0, "pct": 0.05, "rank": 2},
            ],
            dex="raydium-cpmm",
            tvl=100_000.0,
        )
        self.assertEqual(result["stored"], 2)
        self.assertEqual(len(result["new"]), 2)
        self.assertEqual(len(db.holders_for_pool(self.conn, "pool1")), 2)
        holdings = db.wallet_lp_holdings(self.conn, "holderA")
        self.assertEqual(holdings[0]["pool_name"], "TOK / SOL")
        self.assertAlmostEqual(holdings[0]["value_usd"], 10_000.0)

        agg = db.lp_aggregate(self.conn, "holderA")
        self.assertEqual(agg["hold_count"], 1)
        self.assertEqual(agg["adds"], 0)
        self.assertAlmostEqual(agg["hold_max_pct"], 0.1)
        self.assertAlmostEqual(agg["max_size"], 10_000.0)
        self.assertGreater(agg["hold_edge"], 0)

        db.refresh_wallet_scores(self.conn)
        row = db.get_wallet(self.conn, "holderA")
        self.assertEqual(row["lp_holdings"], 1)
        self.assertEqual(row["lp_pools"], 1)
        self.assertGreater(row["lp_score"], 0)
        self.assertGreater(row["smart_score"], 0)
        self.assertTrue(filters.wallet_has_lp_evidence(row))
        top = db.top_wallets(self.conn, limit=10, eligible_only=True)
        self.assertEqual(top[0]["address"], "holderA")

    def test_holder_snapshot_replaces_stale_and_detects_new(self):
        db.upsert_pools(self.conn, [sample_pool()])
        first = [
            {"wallet": "holderA", "amount": 100.0, "pct": 0.1, "rank": 1},
            {"wallet": "holderB", "amount": 50.0, "pct": 0.05, "rank": 2},
        ]
        db.upsert_lp_holders(
            self.conn, "pool1", "lpMint", 1000.0, first, tvl=100_000.0
        )
        again = db.upsert_lp_holders(
            self.conn, "pool1", "lpMint", 1000.0, first[:1],
            tvl=100_000.0,
        )
        wallets = {
            r["wallet"] for r in db.holders_for_pool(self.conn, "pool1")
        }
        self.assertEqual(wallets, {"holderA"})
        self.assertEqual(again["new"], [])

        changed = db.upsert_lp_holders(
            self.conn, "pool1", "lpMint", 1000.0,
            [{"wallet": "holderC", "amount": 80.0, "pct": 0.08, "rank": 1}],
            tvl=100_000.0,
        )
        self.assertEqual([h["wallet"] for h in changed["new"]], ["holderC"])

    def test_rescore_clears_stale_lp_after_events_deleted(self):
        db.upsert_pools(self.conn, [sample_pool()])
        db.insert_lp_events(
            self.conn,
            [
                {
                    "sig": "sig-stale", "wallet": "staleWallet", "pool": "pool1",
                    "program": "prog", "dex": "raydium", "kind": "add",
                    "confidence": "high", "base_delta": -1.0, "quote_delta": -1.0,
                    "size_usd": 1000.0, "block_ts": iso(1),
                }
            ],
        )
        db.refresh_wallet_scores(self.conn)
        row = db.get_wallet(self.conn, "staleWallet")
        self.assertEqual(row["lp_adds"], 1)
        self.assertGreater(row["lp_score"], 0)

        # Simulasikan migrasi yang menghapus event lama.
        self.conn.execute("DELETE FROM lp_events WHERE wallet='staleWallet'")
        db.refresh_wallet_scores(self.conn)
        row = db.get_wallet(self.conn, "staleWallet")
        self.assertEqual(row["lp_adds"], 0)
        self.assertEqual(row["lp_score"], 0.0)
        self.assertFalse(filters.wallet_has_lp_evidence(row))

    def test_seen_sigs(self):
        sigs = ["a", "b", "c"]
        self.assertEqual(db.filter_unseen_sigs(self.conn, sigs), sigs)
        db.mark_sigs_seen(self.conn, ["a"])
        self.assertEqual(db.filter_unseen_sigs(self.conn, sigs), ["b", "c"])
        db.prune(self.conn)

    def test_alert_queue_and_counts(self):
        self.assertTrue(db.queue_alert(self.conn, "w", "wallet", 80, "hi", "d1"))
        self.assertFalse(db.queue_alert(self.conn, "w", "wallet", 80, "hi", "d1"))
        pending = db.pending_alerts(self.conn)
        self.assertEqual(len(pending), 1)
        db.mark_alert_sent(self.conn, pending[0]["id"])
        self.assertEqual(db.pending_alerts(self.conn), [])
        hour, day = db.alert_counts(self.conn)
        self.assertEqual((hour, day), (1, 1))
        row = db.get_wallet(self.conn, "w")
        self.assertIsNone(row)  # mark_alert_sent tidak membuat wallet

    def test_seed_wallet_enrichment(self):
        db.ensure_wallet(self.conn, "seed1")
        self.conn.execute(
            "UPDATE wallets SET note='seed', lp_checked_at=NULL WHERE address='seed1'"
        )
        self.conn.commit()
        rows = db.wallets_for_enrichment(self.conn, 5)
        self.assertEqual(rows[0]["address"], "seed1")

    def test_summary(self):
        db.upsert_pools(self.conn, [sample_pool()])
        summary = db.summary(self.conn)
        self.assertEqual(summary["pools"], 1)
        self.assertEqual(summary["winning_pools"], 1)


if __name__ == "__main__":
    unittest.main()
