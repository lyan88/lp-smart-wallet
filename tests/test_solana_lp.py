#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base58
import solana_lp

ORCA = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
POOL = "Pool1111111111111111111111111111111111111111"
WALLET = "Wallet11111111111111111111111111111111111111"
BASE = "BaseMint111111111111111111111111111111111111"
QUOTE = "QuoteMint11111111111111111111111111111111111"
DEPOSIT = base58.encode(bytes.fromhex("01f77fe837cc1386") + b"\x00" * 8)
WITHDRAW = base58.encode(bytes.fromhex("07829828f85796c9") + b"\x00" * 8)
BUY = base58.encode(bytes.fromhex("c62e1552b4d9e870") + b"\x00" * 8)
SELL = base58.encode(bytes.fromhex("33e685a4017f83ad") + b"\x00" * 8)


def make_tx(base_pre, base_post, quote_pre, quote_post, program=ORCA,
            include_pool=True, owner=WALLET, data="3Bxs4h24hBtQy9rw"):
    accounts = [
        {"pubkey": WALLET, "signer": True, "writable": True},
        {"pubkey": POOL, "signer": False, "writable": True},
    ]
    ix_accounts = [WALLET, POOL] if include_pool else [WALLET]
    return {
        "blockTime": 1761790000,
        "transaction": {
            "signatures": ["sig-1"],
            "message": {
                "accountKeys": accounts,
                "instructions": [
                    {
                        "programId": program,
                        "accounts": ix_accounts,
                        "data": data,
                    }
                ],
            },
        },
        "meta": {
            "feePayer": WALLET,
            "preTokenBalances": [
                {"owner": owner, "mint": BASE, "uiTokenAmount": {"uiAmount": base_pre}},
                {"owner": owner, "mint": QUOTE, "uiTokenAmount": {"uiAmount": quote_pre}},
            ],
            "postTokenBalances": [
                {"owner": owner, "mint": BASE, "uiTokenAmount": {"uiAmount": base_post}},
                {"owner": owner, "mint": QUOTE, "uiTokenAmount": {"uiAmount": quote_post}},
            ],
            "innerInstructions": [],
        },
    }


def pools():
    return {
        POOL: {
            "address": POOL,
            "base_mint": BASE,
            "quote_mint": QUOTE,
            "base_symbol": "TOK",
            "quote_symbol": "USDC",
            "price_usd": 2.0,
            "quote_price_usd": 1.0,
        }
    }


class ClassifyTest(unittest.TestCase):
    def test_add_both_sides(self):
        tx = make_tx(100, 50, 1000, 900)
        events = solana_lp.classify_tx(tx, pools())
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["kind"], "add")
        self.assertEqual(ev["confidence"], "high")
        self.assertEqual(ev["wallet"], WALLET)
        self.assertEqual(ev["pool"], POOL)
        self.assertAlmostEqual(ev["size_usd"], 200.0)

    def test_remove_both_sides(self):
        tx = make_tx(50, 100, 900, 1000)
        events = solana_lp.classify_tx(tx, pools())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "remove")
        self.assertEqual(events[0]["confidence"], "high")

    def test_swap_is_ignored(self):
        tx = make_tx(100, 90, 1000, 1100)
        self.assertEqual(solana_lp.classify_tx(tx, pools()), [])

    def test_one_sided_without_discriminator_ignored(self):
        tx = make_tx(100, 50, 1000, 1000)
        events = solana_lp.classify_tx(tx, pools())
        self.assertEqual(events, [])

    def test_one_sided_add_with_known_discriminator(self):
        tx = make_tx(100, 50, 1000, 1000, program=PUMPSWAP, data=DEPOSIT)
        events = solana_lp.classify_tx(tx, pools())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "add")
        self.assertEqual(events[0]["confidence"], "high")

    def test_one_sided_remove_with_known_discriminator(self):
        tx = make_tx(50, 100, 1000, 1000, program=PUMPSWAP, data=WITHDRAW)
        events = solana_lp.classify_tx(tx, pools())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "remove")
        self.assertEqual(events[0]["confidence"], "high")

    def test_pumpswap_buy_is_not_lp(self):
        tx = make_tx(100, 200, 1000, 1000, program=PUMPSWAP, data=BUY)
        self.assertEqual(solana_lp.classify_tx(tx, pools()), [])

    def test_pumpswap_sell_is_not_lp(self):
        tx = make_tx(200, 100, 1000, 1000, program=PUMPSWAP, data=SELL)
        self.assertEqual(solana_lp.classify_tx(tx, pools()), [])

    def test_unknown_pool_low_confidence(self):
        tx = make_tx(100, 50, 1000, 900, include_pool=False)
        events = solana_lp.classify_tx(tx, {})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "add")
        self.assertEqual(events[0]["confidence"], "low")
        self.assertEqual(events[0]["pool"], "")

    def test_non_lp_program_ignored(self):
        tx = make_tx(100, 50, 1000, 900, program="11111111111111111111111111111111")
        self.assertEqual(solana_lp.classify_tx(tx, pools()), [])

    def test_only_signers_filter(self):
        tx = make_tx(100, 50, 1000, 900)
        other = "Other111111111111111111111111111111111111"
        self.assertEqual(solana_lp.classify_tx(tx, pools(), only_signers=[other]), [])

    def test_signers_and_legacy_keys(self):
        tx = {
            "transaction": {"message": {"accountKeys": ["LegacyAddr"]}},
            "meta": {"feePayer": "FeePayer"},
        }
        self.assertEqual(solana_lp.signers(tx), ["LegacyAddr"])

    def test_classify_deltas_rules(self):
        self.assertEqual(solana_lp.classify_deltas(-1, -2)[0], "add")
        self.assertEqual(solana_lp.classify_deltas(1, 2)[0], "remove")
        self.assertEqual(solana_lp.classify_deltas(-1, 2)[0], "swap")
        self.assertEqual(solana_lp.classify_deltas(-1, 0)[0], "add")
        self.assertEqual(solana_lp.classify_deltas(0, 0)[0], "none")

    def test_block_iso(self):
        tx = {"blockTime": 0}
        self.assertTrue(solana_lp.block_iso(tx).startswith("1970-01-01"))


if __name__ == "__main__":
    unittest.main()
