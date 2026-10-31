#!/usr/bin/env python3
"""
Registry program DEX Solana yang mendukung likuiditas.

Deteksi aksi LP TIDAK bergantung pada nama instruksi internal (yang bisa
berubah antar versi program). Registry ini hanya dipakai untuk memastikan
sebuah transaksi benar-benar menyentuh program DEX, lalu perubahan saldo
token signer yang menentukan apakah itu add/remove liquidity.

Semua alamat di bawah adalah program ID mainnet-beta.
"""

DEX_PROGRAMS = {
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": {
        "dex": "orca",
        "name": "Orca Whirlpool",
    },
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": {
        "dex": "raydium",
        "name": "Raydium AMM v4",
    },
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": {
        "dex": "raydium-clmm",
        "name": "Raydium CLMM",
    },
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": {
        "dex": "raydium-cpmm",
        "name": "Raydium CPMM",
    },
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": {
        "dex": "meteora-dlmm",
        "name": "Meteora DLMM",
    },
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": {
        "dex": "meteora",
        "name": "Meteora Dynamic AMM",
    },
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": {
        "dex": "meteora-damm-v2",
        "name": "Meteora DAMM v2",
    },
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": {
        "dex": "pumpswap",
        "name": "PumpSwap",
    },
}

# Program yang sering muncul di transaksi LP tetapi bukan DEX/posisi.
NON_LP_PROGRAMS = {
    "11111111111111111111111111111111": "System",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL Token",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": "Token-2022",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "Associated Token Account",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",
    "ComputeBudget111111111111111111111111111111": "Compute Budget",
}


def dex_for(program_id):
    item = DEX_PROGRAMS.get(program_id)
    if not item:
        return None
    return item["dex"]


def name_for(program_id):
    item = DEX_PROGRAMS.get(program_id)
    return item["name"] if item else (NON_LP_PROGRAMS.get(program_id) or program_id)


def is_lp_program(program_id):
    return program_id in DEX_PROGRAMS


# Discriminator 8 byte (hex) untuk instruksi tambah/kurang likuiditas.
# Hanya program yang sudah diverifikasi lewat transaksi nyata yang
# dimasukkan. Kalau tidak ada di sini, deteksi tetap memakai perubahan
# saldo dua sisi (lihat solana_lp.py).
LP_IX_DISCRIMINATORS = {
    # PumpSwap: deposit/withdraw terverifikasi dari tx mainnet.
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": {
        "01f77fe837cc1386": "add",
        "07829828f85796c9": "remove",
    },
    # Orca Whirlpool
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": {
        "2e9cf3760dcdfbb2": "add",
        "851d59df45eeb00a": "add",
        "a026d06f685b2c01": "remove",
        "3a7fbc3e4f52c460": "remove",
    },
    # Raydium CLMM
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": {
        "2e9cf3760dcdfbb2": "add",
        "851d59df45eeb00a": "add",
        "a026d06f685b2c01": "remove",
        "3a7fbc3e4f52c460": "remove",
    },
    # Raydium CPMM
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": {
        "b59d59438fb63448": "add",
        "5055d14818ceb16c": "remove",
    },
    # Meteora DLMM
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": {
        "0703967f94283dc8": "add",
        "2905eeaf64e106cd": "add",
        "1c8cee63e7a21595": "add",
        "5055d14818ceb16c": "remove",
    },
    # Meteora Dynamic AMM
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": {
        "b59d59438fb63448": "add",
        "5055d14818ceb16c": "remove",
    },
}

# Program lama yang memakai nomor instruksi 1 byte (Raydium AMM v4).
LEGACY_LP_IX = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": {
        3: "add",     # Deposit
        4: "remove",  # Withdraw
    },
}

# Offset pubkey `lp_mint` di dalam data akun pool. Hanya program yang LP
# token-nya benar-benar diterbitkan sebagai SPL mint yang dimasukkan.
# Diverifikasi dari akun mainnet:
#   - Raydium CPMM : data 637 byte, lp_mint @136, token mints @168/@200
#   - Raydium AMM v4: data 752 byte, lp_mint @464, token mints @400/@432
# Yang tidak ada di sini (Orca, Raydium CLMM, Meteora DLMM, PumpSwap) memakai
# posisi NFT atau akuntansi internal, jadi holder tidak bisa dienumerasi dari
# SPL mint. PumpSwap memang punya mint di @107 tetapi supply SPL-nya 0.
LP_MINT_OFFSETS = {
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": {
        "offset": 136,
        "dex": "raydium-cpmm",
        "name": "Raydium CPMM",
    },
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": {
        "offset": 464,
        "dex": "raydium",
        "name": "Raydium AMM v4",
        # Token account milik pool yang menyimpan LP token (bukan wallet LP).
        "lp_vault_offset": 656,
    },
}


def lp_mint_layout(program_id):
    """Info offset lp_mint untuk sebuah program pool, atau None."""
    return LP_MINT_OFFSETS.get(program_id)


def supports_lp_holders(program_id):
    return program_id in LP_MINT_OFFSETS


def lp_kind_from_data(program_id, raw):
    """Tentukan add/remove dari byte instruksi, atau None."""
    if not raw:
        return None
    by_disc = LP_IX_DISCRIMINATORS.get(program_id)
    if by_disc and len(raw) >= 8:
        kind = by_disc.get(raw[:8].hex())
        if kind:
            return kind
    by_byte = LEGACY_LP_IX.get(program_id)
    if by_byte and len(raw) >= 1:
        return by_byte.get(raw[0])
    return None


def is_router(program_id):
    return "JUP" in program_id or "jup" in program_id.lower()
