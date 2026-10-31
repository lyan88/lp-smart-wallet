#!/usr/bin/env python3
import base64
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base58
import sources
import telegram

CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
POOL = "PoolAddress111111111111111111111111111111"
LP_MINT_BYTES = bytes([9]) * 32
LP_MINT = base58.encode(LP_MINT_BYTES)


def pool_account(offset=136, owner=CPMM, size=700):
    raw = bytearray(size)
    raw[offset : offset + 32] = LP_MINT_BYTES
    return {
        "value": {
            "owner": owner,
            "data": [base64.b64encode(bytes(raw)).decode("ascii"), "base64"],
        }
    }


RAW_POOL = {
    "id": "solana_pool1",
    "type": "pool",
    "attributes": {
        "address": "pool1",
        "name": "TOK / SOL",
        "base_token_price_usd": "2.5",
        "quote_token_price_usd": "150",
        "fdv_usd": "1000000",
        "market_cap_usd": "900000",
        "reserve_in_usd": "50000",
        "pool_fee_percentage": "0.3",
        "pool_created_at": "2026-10-30T00:00:00Z",
        "volume_usd": {"m5": "10", "h1": "100", "h6": "1000", "h24": "10000"},
        "price_change_percentage": {"m5": "1", "h1": "2", "h6": "3", "h24": "4"},
        "transactions": {"h1": {"buys": 30, "sells": 10, "buyers": 20, "sellers": 8}},
    },
    "relationships": {
        "base_token": {"data": {"id": "solana_baseMint"}},
        "quote_token": {"data": {"id": "solana_quoteMint"}},
        "dex": {"data": {"id": "orca"}},
    },
}


class SourcesTest(unittest.TestCase):
    def test_normalize_pool(self):
        pool = sources.normalize_pool(RAW_POOL)
        self.assertEqual(pool["address"], "pool1")
        self.assertEqual(pool["base_mint"], "baseMint")
        self.assertEqual(pool["quote_mint"], "quoteMint")
        self.assertEqual(pool["dex"], "orca")
        self.assertEqual(pool["base_symbol"], "TOK")
        self.assertEqual(pool["quote_symbol"], "SOL")
        self.assertAlmostEqual(pool["price_usd"], 2.5)
        self.assertAlmostEqual(pool["quote_price_usd"], 150.0)
        self.assertAlmostEqual(pool["tvl"], 50000)
        self.assertAlmostEqual(pool["fee_pct"], 0.3)
        self.assertEqual(pool["buyers_h1"], 20)

    def test_parse_ts(self):
        dt = sources.parse_ts("2026-10-30T00:00:00Z")
        self.assertEqual(dt.year, 2026)
        self.assertIsNone(sources.parse_ts("not-a-date"))

    def test_chunks(self):
        text = "\n\n".join("baris %d" % i for i in range(2000))
        chunks = telegram._chunks(text, limit=1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertIn("baris 0", chunks[0])


class HolderSourceTest(unittest.TestCase):
    def setUp(self):
        sources.clear_holders_cache()

    def tearDown(self):
        sources.clear_holders_cache()

    def _run(self, account, supply=1000.0, largest=None, accounts=None,
             calls=None):
        largest = largest or [
            {"address": "acctA", "uiAmount": 400.0},
            {"address": "acctB", "uiAmount": 100.0},
        ]
        accounts = accounts or [
            {"data": {"parsed": {"info": {
                "owner": "walletA",
                "tokenAmount": {"uiAmount": 400.0, "decimals": 9},
            }}}},
            {"data": {"parsed": {"info": {
                "owner": "walletB",
                "tokenAmount": {"uiAmount": 100.0, "decimals": 9},
            }}}},
        ]

        def fake(method, params, **kwargs):
            if calls is not None:
                calls.append(method)
            if method == "getAccountInfo":
                return account
            if method == "getTokenSupply":
                return {"value": {"uiAmount": supply, "decimals": 9}}
            if method == "getTokenLargestAccounts":
                return {"value": largest}
            if method == "getMultipleAccounts":
                return {"value": accounts}
            raise AssertionError("unexpected RPC: %s" % method)

        with mock.patch.object(sources.net, "rpc_call", side_effect=fake):
            return sources.fetch_lp_holders(POOL)

    def test_cpmm_holders(self):
        result = self._run(pool_account())
        self.assertEqual(result["lp_mint"], LP_MINT)
        self.assertEqual(result["dex"], "raydium-cpmm")
        self.assertAlmostEqual(result["supply"], 1000.0)
        self.assertEqual(len(result["holders"]), 2)
        top = result["holders"][0]
        self.assertEqual(top["wallet"], "walletA")
        self.assertAlmostEqual(top["pct"], 0.4)
        self.assertEqual(top["rank"], 1)

    def test_unsupported_dex_stops_after_account_read(self):
        calls = []
        result = self._run(
            pool_account(owner="unknown-program"), calls=calls
        )
        self.assertIsNone(result)
        self.assertEqual(calls, ["getAccountInfo"])

    def test_zero_supply_returns_empty(self):
        result = self._run(pool_account(), supply=0.0)
        self.assertEqual(result["holders"], [])
        self.assertEqual(result["supply"], 0.0)

    def test_v4_excludes_lp_vault(self):
        v4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
        raw = bytearray(752)
        raw[464:496] = LP_MINT_BYTES
        vault_bytes = bytes([5]) * 32
        raw[656:688] = vault_bytes
        account = {
            "value": {
                "owner": v4,
                "data": [base64.b64encode(bytes(raw)).decode("ascii"), "base64"],
            }
        }
        largest = [
            {"address": base58.encode(vault_bytes), "uiAmount": 400.0},
            {"address": "acctB", "uiAmount": 100.0},
        ]
        accounts = [
            {"data": {"parsed": {"info": {
                "owner": "vaultOwner",
                "tokenAmount": {"uiAmount": 400.0, "decimals": 9},
            }}}},
            {"data": {"parsed": {"info": {
                "owner": "walletB",
                "tokenAmount": {"uiAmount": 100.0, "decimals": 9},
            }}}},
        ]
        result = self._run(account, largest=largest, accounts=accounts)
        self.assertEqual(result["dex"], "raydium")
        self.assertEqual([h["wallet"] for h in result["holders"]], ["walletB"])

    def test_aggregates_accounts_and_filters_pool(self):
        largest = [
            {"address": "a1", "uiAmount": 300.0},
            {"address": "a2", "uiAmount": 200.0},
            {"address": "a3", "uiAmount": 50.0},
        ]
        accounts = [
            {"data": {"parsed": {"info": {
                "owner": "walletA",
                "tokenAmount": {"uiAmount": 300.0, "decimals": 9},
            }}}},
            {"data": {"parsed": {"info": {
                "owner": "walletA",
                "tokenAmount": {"uiAmount": 200.0, "decimals": 9},
            }}}},
            {"data": {"parsed": {"info": {
                "owner": POOL,
                "tokenAmount": {"uiAmount": 50.0, "decimals": 9},
            }}}},
        ]
        result = self._run(pool_account(), largest=largest, accounts=accounts)
        self.assertEqual(len(result["holders"]), 1)
        self.assertEqual(result["holders"][0]["wallet"], "walletA")
        self.assertAlmostEqual(result["holders"][0]["amount"], 500.0)
        self.assertAlmostEqual(result["holders"][0]["pct"], 0.5)


if __name__ == "__main__":
    unittest.main()
