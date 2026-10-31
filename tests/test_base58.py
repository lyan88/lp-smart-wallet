#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base58


class Base58Test(unittest.TestCase):
    def test_solana_zero_address(self):
        addr = "11111111111111111111111111111111"
        raw = base58.decode(addr)
        self.assertEqual(len(raw), 32)
        self.assertEqual(raw, b"\x00" * 32)
        self.assertEqual(base58.encode(raw), addr)

    def test_roundtrip_random_bytes(self):
        raw = bytes(range(32))
        self.assertEqual(base58.decode(base58.encode(raw)), raw)

    def test_leading_zero_preserved(self):
        raw = b"\x00\x00abc"
        self.assertEqual(base58.decode(base58.encode(raw)), raw)

    def test_invalid_character(self):
        with self.assertRaises(ValueError):
            base58.decode("0OIl")

    def test_is_valid_length(self):
        self.assertTrue(base58.is_valid("11111111111111111111111111111111", 32))
        self.assertFalse(base58.is_valid("abc", 32))


if __name__ == "__main__":
    unittest.main()
