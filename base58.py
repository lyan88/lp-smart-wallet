#!/usr/bin/env python3
"""Base58 Bitcoin/Solana tanpa dependensi luar."""

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(ALPHABET)}


def decode(text):
    if not isinstance(text, str) or not text:
        raise ValueError("base58 kosong")
    number = 0
    for char in text:
        try:
            number = number * 58 + _INDEX[char]
        except KeyError:
            raise ValueError("karakter base58 tidak valid: %r" % char)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + raw


def encode(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    number = int.from_bytes(data, "big")
    out = []
    while number:
        number, rem = divmod(number, 58)
        out.append(ALPHABET[rem])
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + "".join(reversed(out))


def is_valid(text, length=None):
    try:
        raw = decode(text)
    except (ValueError, TypeError):
        return False
    if length is not None and len(raw) != length:
        return False
    return True
