#!/usr/bin/env python3
"""
Deteksi aksi likuiditas (LP add/remove) dari transaksi Solana.

Dua bukti dipakai, dari yang paling kuat:

  1. Discriminator instruksi program DEX yang sudah diverifikasi
     (mis. PumpSwap deposit/withdraw). Ini tetap bekerja walau hanya satu
     sisi saldo yang terlihat karena akun WSOL ditutup di akhir transaksi.
  2. Perubahan saldo dua token pool milik signer yang searah:
     keduanya berkurang = ADD, keduanya bertambah = REMOVE.

Transaksi satu sisi TANPA discriminator yang dikenal sengaja diabaikan.
Audit mainnet menunjukkan buy/sell PumpSwap juga hanya menampilkan satu
sisi (WSOL ditutup), sehingga menebak dari satu sisi menghasilkan false
positive. Lebih baik kehilangan sebagian sinyal daripada salah label.
"""

import datetime

import base58
from programs import DEX_PROGRAMS, is_lp_program, lp_kind_from_data

EPS = 1e-12


def _account_keys(tx):
    message = ((tx or {}).get("transaction") or {}).get("message") or {}
    keys = message.get("accountKeys") or []
    out = []
    for item in keys:
        if isinstance(item, dict):
            out.append(item.get("pubkey") or "")
        elif isinstance(item, str):
            out.append(item)
        else:
            out.append("")
    return out


def signers(tx):
    message = ((tx or {}).get("transaction") or {}).get("message") or {}
    keys = message.get("accountKeys") or []
    out = []
    for item in keys:
        if isinstance(item, dict) and item.get("signer") and item.get("pubkey"):
            out.append(item["pubkey"])
        elif isinstance(item, str):
            # Bentuk lama tanpa metadata: fee payer adalah signer pertama.
            if not out:
                out.append(item)
    return out


def _instructions(tx):
    message = ((tx or {}).get("transaction") or {}).get("message") or {}
    groups = [message.get("instructions") or []]
    for inner in ((tx or {}).get("meta") or {}).get("innerInstructions") or []:
        groups.append(inner.get("instructions") or [])
    for group in groups:
        for ix in group:
            if isinstance(ix, dict):
                yield ix


def program_ids(tx):
    keys = _account_keys(tx)
    found = set()
    for ix in _instructions(tx):
        pid = ix.get("programId")
        if not pid and ix.get("programIdIndex") is not None:
            try:
                pid = keys[int(ix["programIdIndex"])]
            except (ValueError, IndexError, TypeError):
                pid = None
        if pid:
            found.add(pid)
    return found


def instruction_accounts(tx):
    """Kumpulan pubkey yang muncul sebagai akun instruksi (top + inner)."""
    keys = _account_keys(tx)
    accounts = set()
    for ix in _instructions(tx):
        raw = ix.get("accounts")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    accounts.add(item)
                elif isinstance(item, int):
                    try:
                        accounts.add(keys[item])
                    except IndexError:
                        pass
        parsed = ix.get("parsed")
        if isinstance(parsed, dict):
            info = parsed.get("info") or {}
            for value in info.values():
                if isinstance(value, str) and 32 <= len(value) <= 44:
                    accounts.add(value)
    return accounts


def program_accounts(tx):
    """Peta programId -> himpunan akun yang disebut pada instruksinya."""
    keys = _account_keys(tx)
    out = {}
    for ix in _instructions(tx):
        pid = ix.get("programId")
        if not pid and ix.get("programIdIndex") is not None:
            try:
                pid = keys[int(ix["programIdIndex"])]
            except (ValueError, IndexError, TypeError):
                pid = None
        if not pid:
            continue
        bucket = out.setdefault(pid, set())
        for item in ix.get("accounts") or []:
            if isinstance(item, str):
                bucket.add(item)
            elif isinstance(item, int):
                try:
                    bucket.add(keys[item])
                except IndexError:
                    pass
    return out


def token_balances(tx):
    """dict[(owner, mint)] -> saldo UI sebelum/sesudah."""
    pre = {}
    post = {}
    meta = (tx or {}).get("meta") or {}
    for bucket, target in ((meta.get("preTokenBalances") or [], pre),
                           (meta.get("postTokenBalances") or [], post)):
        for row in bucket:
            owner = row.get("owner")
            mint = row.get("mint")
            if not owner or not mint:
                continue
            amount = ((row.get("uiTokenAmount") or {}).get("uiAmount"))
            if amount is None:
                try:
                    amount = float(
                        (row.get("uiTokenAmount") or {}).get("uiAmountString") or 0
                    )
                except (TypeError, ValueError):
                    amount = 0.0
            target[(owner, mint)] = float(amount)
    return pre, post


def balance_delta(tx, owner, mint):
    pre, post = token_balances(tx)
    before = pre.get((owner, mint), 0.0)
    after = post.get((owner, mint), before)
    return after - before


def block_iso(tx):
    ts = (tx or {}).get("blockTime")
    if ts is None:
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            int(ts), datetime.timezone.utc
        ).replace(microsecond=0).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


def classify_deltas(base_delta, quote_delta, tol_ratio=0.005):
    """
    Klasifikasi murni dari delta dua token pool.

    Mengembalikan (kind, confidence) dengan kind salah satu:
    add, remove, swap, none.
    """
    db = float(base_delta or 0.0)
    dq = float(quote_delta or 0.0)
    biggest = max(abs(db), abs(dq))
    if biggest <= EPS:
        return "none", "low"
    tol = max(EPS, biggest * tol_ratio)
    b = 0 if abs(db) <= tol else (1 if db > 0 else -1)
    q = 0 if abs(dq) <= tol else (1 if dq > 0 else -1)
    if b < 0 and q < 0:
        return "add", "high"
    if b > 0 and q > 0:
        return "remove", "high"
    if (b < 0 and q == 0) or (q < 0 and b == 0):
        return "add", "medium"
    if (b > 0 and q == 0) or (q > 0 and b == 0):
        return "remove", "medium"
    if b != 0 and q != 0 and b != q:
        return "swap", "none"
    return "none", "low"


def _size_usd(pool, base_delta, quote_delta):
    if not pool:
        return 0.0
    price = float(pool.get("price_usd") or 0.0)
    quote_price = float(pool.get("quote_price_usd") or 0.0)
    if quote_price <= 0:
        sym = (pool.get("quote_symbol") or "").upper()
        if sym in ("USDC", "USDT", "USDG", "PYUSD"):
            quote_price = 1.0
    total = abs(base_delta) * price + abs(quote_delta) * quote_price
    return round(total, 2)


def _ix_program_id(ix, keys):
    pid = ix.get("programId")
    if not pid and ix.get("programIdIndex") is not None:
        try:
            pid = keys[int(ix["programIdIndex"])]
        except (ValueError, IndexError, TypeError):
            pid = None
    return pid


def _ix_accounts(ix, keys):
    out = set()
    for item in ix.get("accounts") or []:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, int):
            try:
                out.add(keys[item])
            except IndexError:
                pass
    return out


def instruction_rows(tx):
    """(program_id, accounts, data_bytes) untuk instruksi top + inner."""
    keys = _account_keys(tx)
    for ix in _instructions(tx):
        pid = _ix_program_id(ix, keys)
        if not pid:
            continue
        data = ix.get("data")
        raw = b""
        if isinstance(data, str):
            try:
                raw = base58.decode(data)
            except ValueError:
                raw = b""
        yield pid, _ix_accounts(ix, keys), raw


def classify_tx(tx, pools, only_signers=None):
    """
    Kembalikan daftar event LP di satu transaksi.

    pools: dict alamat_pool -> info pool (butuh base_mint, quote_mint,
           price_usd, quote_price_usd, base_symbol, quote_symbol).
    only_signers: batasi signer yang dinilai (mis. wallet yang sedang
           diperiksa). None berarti semua signer.
    """
    if not tx:
        return []
    progs = program_ids(tx) & set(DEX_PROGRAMS)
    if not progs:
        return []
    rows = list(instruction_rows(tx))
    pool_hits = [
        addr
        for addr in (pools or {})
        if any(addr in accs and pid in DEX_PROGRAMS for pid, accs, _ in rows)
    ]
    tx_signers = signers(tx)
    if only_signers is not None:
        allowed = set(only_signers)
        tx_signers = [w for w in tx_signers if w in allowed]
    if not tx_signers:
        return []
    pre, post = token_balances(tx)
    sig = tx.get("transaction", {}).get("signatures", [None])[0] or ""
    when = block_iso(tx)
    events = []

    # Bukti terkuat: instruksi deposit/withdraw yang dikenal, pada pool
    # yang akunnya muncul di instruksi tersebut.
    ix_kind = {}
    for pid, accs, raw in rows:
        if pid not in progs:
            continue
        kind = lp_kind_from_data(pid, raw)
        if kind not in ("add", "remove"):
            continue
        for addr in pool_hits:
            if addr not in accs:
                continue
            for wallet in tx_signers:
                if wallet in accs:
                    ix_kind[(addr, wallet)] = (kind, pid)

    for wallet in tx_signers:
        best = None
        for addr in pool_hits:
            pool = pools[addr]
            base_mint = pool.get("base_mint")
            quote_mint = pool.get("quote_mint")
            if not base_mint or not quote_mint:
                continue
            db = post.get((wallet, base_mint), 0.0) - pre.get((wallet, base_mint), 0.0)
            dq = post.get((wallet, quote_mint), 0.0) - pre.get((wallet, quote_mint), 0.0)
            if abs(db) + abs(dq) <= EPS:
                continue
            known = ix_kind.get((addr, wallet))
            if known:
                kind, pid = known
                conf = "high"
            else:
                kind, conf = classify_deltas(db, dq)
                # Hanya dua sisi yang searah yang dipercaya. Satu sisi bisa
                # berarti swap yang akun WSOL-nya ditutup.
                if kind not in ("add", "remove") or conf != "high":
                    continue
                candidates = sorted(
                    p for p in progs if any(
                        addr in accs and pid == p for pid, accs, _ in rows
                    )
                )
                if not candidates:
                    continue
                pid = candidates[0]
            weight = abs(db) + abs(dq)
            if best is None or weight > best[0]:
                best = (weight, addr, pool, db, dq, kind, conf, pid)

        if best is None:
            # Pool tidak dikenal: hanya bukti dua sisi searah, keyakinan rendah.
            deltas = {}
            for (owner, mint), value in pre.items():
                if owner == wallet:
                    deltas[mint] = post.get((owner, mint), 0.0) - value
            for (owner, mint), value in post.items():
                if owner == wallet and mint not in deltas:
                    deltas[mint] = value - pre.get((owner, mint), 0.0)
            moved = [(m, d) for m, d in deltas.items() if abs(d) > EPS]
            if len(moved) == 2 and moved[0][1] * moved[1][1] > 0:
                kind = "add" if moved[0][1] < 0 else "remove"
                pid = sorted(progs)[0]
                events.append(
                    {
                        "sig": sig,
                        "wallet": wallet,
                        "pool": "",
                        "program": pid,
                        "dex": DEX_PROGRAMS[pid]["dex"],
                        "kind": kind,
                        "confidence": "low",
                        "base_delta": round(moved[0][1], 9),
                        "quote_delta": round(moved[1][1], 9),
                        "size_usd": 0.0,
                        "price_usd": 0.0,
                        "block_ts": when,
                    }
                )
            continue

        _, addr, pool, db, dq, kind, conf, pid = best
        events.append(
            {
                "sig": sig,
                "wallet": wallet,
                "pool": addr,
                "program": pid,
                "dex": DEX_PROGRAMS.get(pid, {}).get("dex", ""),
                "kind": kind,
                "confidence": conf,
                "base_delta": round(db, 9),
                "quote_delta": round(dq, 9),
                "size_usd": _size_usd(pool, db, dq),
                "price_usd": float(pool.get("price_usd") or 0.0),
                "block_ts": when,
            }
        )
    return events


def looks_like_lp_program(program_id):
    return is_lp_program(program_id)
