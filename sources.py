#!/usr/bin/env python3
"""
Sumber data: GeckoTerminal (pool/trade) dan RPC Solana (transaksi LP).

Semua fungsi mengembalikan dict/list Python biasa dengan angka yang sudah
dinormalisasi, supaya modul lain tidak perlu tahu bentuk mentah API.
"""

import base64
import datetime
import json

import base58
import config
import net
import programs
from net import SourceError

_cache = net.TtlCache()
_holders_cache = net.TtlCache()
_gecko = net.limiter("geckoterminal", config.GECKO_MIN_INTERVAL)
_GECKO = "https://api.geckoterminal.com/api/v2"


def _num(value, default=0.0):
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value, default=0):
    try:
        if value is None or value == "":
            return int(default)
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _token_id(data):
    raw = ((data or {}).get("id") or "")
    return raw.split("_", 1)[1] if "_" in raw else raw


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(text):
    """ISO-8601 (dengan Z) -> datetime UTC; None kalau gagal."""
    if not text:
        return None
    try:
        clean = str(text).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except ValueError:
        return None


def _gecko_get(path, max_age=0):
    url = _GECKO + path
    if max_age:
        cached = _cache.get(url, max_age)
        if cached is not None:
            return cached
    data = net.get_json(url, lim=_gecko)
    if max_age:
        _cache.put(url, data)
    return data


# ---------------------------------------------------------------- Gecko pools
def normalize_pool(item):
    attr = item.get("attributes") or {}
    rel = item.get("relationships") or {}
    base = _token_id((rel.get("base_token") or {}).get("data"))
    quote = _token_id((rel.get("quote_token") or {}).get("data"))
    dex = ((rel.get("dex") or {}).get("data") or {}).get("id") or ""
    tx = attr.get("transactions") or {}
    h1 = tx.get("h1") or {}
    vol = attr.get("volume_usd") or {}
    chg = attr.get("price_change_percentage") or {}
    name = attr.get("name") or ""
    parts = [p.strip() for p in name.split("/")]
    return {
        "address": attr.get("address") or item.get("id", "").split("_", 1)[-1],
        "name": name,
        "dex": dex,
        "base_mint": base,
        "quote_mint": quote,
        "base_symbol": parts[0] if parts else "",
        "quote_symbol": parts[1] if len(parts) > 1 else "",
        "price_usd": _num(attr.get("base_token_price_usd")),
        "quote_price_usd": _num(attr.get("quote_token_price_usd")),
        "fdv": _num(attr.get("fdv_usd") or attr.get("market_cap_usd")),
        "tvl": _num(attr.get("reserve_in_usd")),
        "vol_m5": _num(vol.get("m5")),
        "vol_h1": _num(vol.get("h1")),
        "vol_h6": _num(vol.get("h6")),
        "vol_h24": _num(vol.get("h24")),
        "chg_m5": _num(chg.get("m5")),
        "chg_h1": _num(chg.get("h1")),
        "chg_h6": _num(chg.get("h6")),
        "chg_h24": _num(chg.get("h24")),
        "buyers_h1": _int(h1.get("buyers")),
        "sellers_h1": _int(h1.get("sellers")),
        "created_at": attr.get("pool_created_at") or "",
        "fee_pct": _num(attr.get("pool_fee_percentage"), -1),
    }


def trending_pools(pages=None, max_age=90):
    """Ambil pool trending, urut volume 24 jam terbesar."""
    pages = config.SCAN_PAGES if pages is None else pages
    out = []
    seen = set()
    for page in range(1, max(1, pages) + 1):
        path = "/networks/%s/trending_pools?page=%d" % (config.GECKO_NETWORK, page)
        try:
            data = _gecko_get(path, max_age=max_age)
        except SourceError:
            if page == 1:
                raise
            break
        for item in data.get("data") or []:
            pool = normalize_pool(item)
            if not pool["address"] or pool["address"] in seen:
                continue
            seen.add(pool["address"])
            out.append(pool)
    out.sort(key=lambda p: p["vol_h24"], reverse=True)
    return out[: config.SCAN_POOL_LIMIT]


def pool_details(addresses):
    """Ambil detail beberapa pool sekaligus (maks 30 alamat per panggilan)."""
    addresses = [a for a in addresses if a][:30]
    if not addresses:
        return {}
    path = "/networks/%s/pools/multi/%s" % (
        config.GECKO_NETWORK,
        ",".join(addresses),
    )
    data = _gecko_get(path, max_age=60)
    out = {}
    for item in data.get("data") or []:
        pool = normalize_pool(item)
        if pool["address"]:
            out[pool["address"]] = pool
    return out


def pool_trades(address, min_usd=None):
    """Trade terbaru satu pool, dinormalisasi dan diurutkan dari terlama."""
    min_usd = config.MIN_TRADE_USD if min_usd is None else min_usd
    path = "/networks/%s/pools/%s/trades?trade_volume_in_usd_greater_than=%s" % (
        config.GECKO_NETWORK,
        address,
        max(0, int(min_usd)),
    )
    data = _gecko_get(path, max_age=30)
    trades = []
    for item in data.get("data") or []:
        attr = item.get("attributes") or {}
        side = (attr.get("kind") or "").lower()
        if side not in ("buy", "sell"):
            continue
        wallet = attr.get("tx_from_address") or ""
        if not wallet:
            continue
        if side == "buy":
            token = attr.get("to_token_address") or ""
            price = _num(attr.get("price_to_in_usd"))
            amount = _num(attr.get("to_token_amount"))
        else:
            token = attr.get("from_token_address") or ""
            price = _num(attr.get("price_from_in_usd"))
            amount = _num(attr.get("from_token_amount"))
        volume = _num(attr.get("volume_in_usd"))
        if volume < min_usd or price <= 0:
            continue
        trades.append(
            {
                "id": item.get("id") or attr.get("tx_hash"),
                "pool": address,
                "wallet": wallet,
                "token": token,
                "side": side,
                "price_usd": price,
                "amount_usd": volume,
                "token_amount": amount,
                "block_ts": attr.get("block_timestamp") or "",
                "tx_hash": attr.get("tx_hash") or "",
                "anomalous": bool(attr.get("is_anomalous")),
            }
        )
    trades.sort(key=lambda t: t["block_ts"])
    return trades


# ---------------------------------------------------------------- Solana RPC
def rpc_signatures(address, limit=20):
    limit = max(1, min(int(limit), 1000))
    result = net.rpc_call(
        "getSignaturesForAddress",
        [address, {"limit": limit, "commitment": "confirmed"}],
    )
    return result or []


def rpc_transaction(signature):
    """
    Ambil transaksi terparse. Coba maxSupportedTransactionVersion=1 lalu 0
    karena sebagian endpoint publik menolak versi tertentu.
    """
    last = None
    for version in (1, 0):
        try:
            return net.rpc_call(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": version,
                        "commitment": "confirmed",
                    },
                ],
            )
        except SourceError as exc:
            last = exc
            if "not supported" not in str(exc).lower() and "version" not in str(exc).lower():
                raise
    if last:
        raise last
    return None


def rpc_health():
    return net.rpc_call("getHealth", [])


def token_supply(mint):
    result = net.rpc_call("getTokenSupply", [mint])
    return _num(((result or {}).get("value") or {}).get("uiAmount"))


def _account_bytes(value):
    """Ambil data akun base64 dari hasil getAccountInfo."""
    raw = (value or {}).get("data")
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        try:
            return base64.b64decode(raw[0])
        except (TypeError, ValueError):
            return b""
    return b""


def fetch_lp_holders(pool_address, top=20, max_age=0):
    """
    Pemegang LP token terbesar untuk satu pool, langsung dari SPL mint.

    Hanya pool dengan LP mint fungible yang didukung (lihat
    programs.LP_MINT_OFFSETS). Untuk DEX dengan posisi NFT (Orca, CLMM,
    DLMM), fungsi mengembalikan None tanpa panggilan tambahan.

    Hasil:
      {"pool", "lp_mint", "supply", "decimals", "dex", "holders": [
          {"wallet", "token_account", "amount", "pct", "rank"}
      ]}
    """
    key = "holders:%s" % pool_address
    if max_age:
        cached = _holders_cache.get(key, max_age)
        if cached is not None:
            return cached

    account = net.rpc_call(
        "getAccountInfo", [pool_address, {"encoding": "base64"}]
    )
    value = (account or {}).get("value") or {}
    owner = value.get("owner") or ""
    layout = programs.lp_mint_layout(owner)
    if not layout:
        return None
    data = _account_bytes(value)
    offset = int(layout.get("offset") or 0)
    if len(data) < offset + 32:
        return None
    lp_mint = base58.encode(data[offset : offset + 32])
    excluded_accounts = set()
    vault_offset = layout.get("lp_vault_offset")
    if vault_offset and len(data) >= int(vault_offset) + 32:
        excluded_accounts.add(
            base58.encode(data[int(vault_offset) : int(vault_offset) + 32])
        )

    supply = net.rpc_call("getTokenSupply", [lp_mint])
    supply_info = (supply or {}).get("value") or {}
    decimals = _int(supply_info.get("decimals"))
    total = _num(supply_info.get("uiAmount"))
    if total <= 0:
        raw_supply = _num(supply_info.get("amount"))
        if raw_supply > 0:
            total = raw_supply / (10 ** max(0, decimals))
    dex = layout.get("dex") or ""
    if total <= 0:
        result = {
            "pool": pool_address,
            "lp_mint": lp_mint,
            "supply": 0.0,
            "decimals": decimals,
            "dex": dex,
            "holders": [],
        }
        if max_age:
            _holders_cache.put(key, result)
        return result

    largest = net.rpc_call("getTokenLargestAccounts", [lp_mint])
    rows = []
    for row in (largest or {}).get("value") or []:
        if not row.get("address"):
            continue
        amount = _num(row.get("uiAmount"))
        if amount <= 0:
            raw_amount = _num(row.get("amount"))
            if raw_amount > 0:
                amount = raw_amount / (10 ** max(0, decimals))
        if amount > 0:
            rows.append(row)
    rows = rows[: max(1, int(top))]
    if not rows:
        return None

    addresses = [row.get("address") for row in rows if row.get("address")]
    accounts = net.rpc_call(
        "getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}]
    )
    parsed = (accounts or {}).get("value") or []

    # Satu wallet bisa punya beberapa token account untuk mint yang sama.
    by_wallet = {}
    for row, account in zip(rows, parsed):
        info = (
            ((account or {}).get("data") or {}).get("parsed") or {}
        ).get("info") or {}
        wallet = info.get("owner") or ""
        token_amount = info.get("tokenAmount") or {}
        amount = _num(token_amount.get("uiAmount"), row.get("uiAmount"))
        if amount <= 0:
            raw_amount = _num(token_amount.get("amount"))
            token_decimals = _int(token_amount.get("decimals"), decimals)
            if raw_amount > 0:
                amount = raw_amount / (10 ** max(0, token_decimals))
        if not wallet or amount <= 0:
            continue
        if row.get("address") in excluded_accounts:
            continue
        if wallet == pool_address or wallet in config.IGNORE_ADDRESSES:
            continue
        item = by_wallet.setdefault(
            wallet,
            {"wallet": wallet, "token_account": row.get("address"), "amount": 0.0},
        )
        item["amount"] += amount

    holders = []
    for wallet, item in sorted(
        by_wallet.items(), key=lambda kv: kv[1]["amount"], reverse=True
    ):
        amount = item["amount"]
        holders.append(
            {
                "wallet": wallet,
                "token_account": item["token_account"],
                "amount": round(amount, 9),
                "pct": round(amount / total, 8),
                "rank": len(holders) + 1,
            }
        )
    result = {
        "pool": pool_address,
        "lp_mint": lp_mint,
        "supply": round(total, 9),
        "decimals": decimals,
        "dex": dex,
        "holders": holders[: max(1, int(top))],
    }
    if max_age:
        _holders_cache.put(key, result)
    return result


def clear_holders_cache():
    _holders_cache._data.clear()


def jprint(value):
    return json.dumps(value, ensure_ascii=False, default=str)
