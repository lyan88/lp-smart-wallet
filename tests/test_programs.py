#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import programs


class ProgramsTest(unittest.TestCase):
    def test_known_dexes(self):
        self.assertEqual(programs.dex_for("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"), "orca")
        self.assertEqual(
            programs.dex_for("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"),
            "raydium",
        )
        self.assertTrue(
            programs.is_lp_program("cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG")
        )

    def test_system_is_not_lp(self):
        self.assertFalse(programs.is_lp_program("11111111111111111111111111111111"))
        self.assertEqual(programs.name_for("11111111111111111111111111111111"), "System")

    def test_unknown_program(self):
        self.assertIsNone(programs.dex_for("unknown"))
        self.assertEqual(programs.name_for("unknown"), "unknown")

    def test_lp_mint_layouts(self):
        cpmm = programs.lp_mint_layout(
            "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
        )
        self.assertEqual(cpmm["offset"], 136)
        self.assertEqual(cpmm["dex"], "raydium-cpmm")
        v4 = programs.lp_mint_layout(
            "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
        )
        self.assertEqual(v4["offset"], 464)
        self.assertTrue(
            programs.supports_lp_holders(
                "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
            )
        )
        # Orca/CLMM/DLMM memakai posisi NFT, bukan LP token fungible.
        self.assertFalse(
            programs.supports_lp_holders(
                "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
            )
        )


if __name__ == "__main__":
    unittest.main()
