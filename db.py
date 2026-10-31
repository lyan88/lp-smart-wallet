#!/usr/bin/env python3
"""
SQLite: pool, trade, wallet, event LP, antrean alert, dan state kecil.

Tidak ada ORM. Semua query ditulis eksplisit supaya mudah diaudit dan
mudah dijalankan di VPS tanpa dependensi.
"""

import datetime
import json
import sqlite3

import config
import filters
import scoring


SCHEMA = """
CREATE TABLE IF NOT EXISTS pools (
    address TEXT PRIMARY KEY,
    name TEXT,
    dex TEXT,
    base_mint TEXT,
    quote_mint TEXT,
    base_symbol TEXT,
    quote_symbol TEXT,
    price_usd REAL DEFAULT 0,
    quote_price_usd REAL DEFAULT 0,
    fdv REAL DEFAULT 0,
    tvl REAL DEFAULT 0,
    vol_m5 REAL DEFAULT 0,
    vol_h1 REAL DEFAULT 0,
    vol_h6 REAL DEFAULT 0,
    vol_h24 REAL DEFAULT 0,
    chg_m5 REAL DEFAULT 0,
    chg_h1 REAL DEFAULT 0,
    chg_h6 REAL DEFAULT 0,
    chg_h24 REAL DEFAULT 0,
    buyers_h1 INTEGER DEFAULT 0,
    sellers_h1 INTEGER DEFAULT 0,
    created_at TEXT,
    fee_pct REAL DEFAULT -1,
    pool_score REAL DEFAULT 0,
    fee_yield_daily REAL DEFAULT 0,
    winning INTEGER DEFAULT 0,
    reject TEXT,
    first_seen TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    pool TEXT,
    wallet TEXT,
    token TEXT,
    side TEXT,
    price_usd REAL,
    amount_usd REAL,
    token_amount REAL,
    block_ts TEXT,
    tx_hash TEXT,
    seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet);
CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(block_ts);

CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    first_seen TEXT,
    last_seen TEXT,
    trades INTEGER DEFAULT 0,
    volume_usd REAL DEFAULT 0,
    tokens INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    avg_peak_roi REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    lp_adds INTEGER DEFAULT 0,
    lp_removes INTEGER DEFAULT 0,
    lp_pools INTEGER DEFAULT 0,
    lp_size_usd REAL DEFAULT 0,
    lp_edge REAL DEFAULT 0,
    lp_quality REAL DEFAULT 0,
    lp_last_ts TEXT,
    lp_checked_at TEXT,
    lp_score REAL DEFAULT 0,
    trade_score REAL DEFAULT 0,
    smart_score REAL DEFAULT 0,
    tier TEXT DEFAULT 'D',
    lp_holdings INTEGER DEFAULT 0,
    lp_hold_pct REAL DEFAULT 0,
    lp_hold_usd REAL DEFAULT 0,
    lp_hold_edge REAL DEFAULT 0,
    lp_hold_quality REAL DEFAULT 0,
    lp_hold_last_ts TEXT,
    alerts INTEGER DEFAULT 0,
    last_alert_at TEXT,
    ignored INTEGER DEFAULT 0,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_wallets_score ON wallets(smart_score);
CREATE INDEX IF NOT EXISTS idx_wallets_lp_checked ON wallets(lp_checked_at);

CREATE TABLE IF NOT EXISTS wallet_tokens (
    wallet TEXT,
    token TEXT,
    pool TEXT,
    buys INTEGER DEFAULT 0,
    sells INTEGER DEFAULT 0,
    buy_usd REAL DEFAULT 0,
    sell_usd REAL DEFAULT 0,
    buy_qty REAL DEFAULT 0,
    sell_qty REAL DEFAULT 0,
    avg_buy REAL DEFAULT 0,
    avg_sell REAL DEFAULT 0,
    first_ts TEXT,
    last_ts TEXT,
    peak_price REAL DEFAULT 0,
    current_price REAL DEFAULT 0,
    PRIMARY KEY (wallet, token)
);

CREATE TABLE IF NOT EXISTS lp_events (
    sig TEXT,
    wallet TEXT,
    pool TEXT,
    program TEXT,
    dex TEXT,
    kind TEXT,
    confidence TEXT,
    base_delta REAL DEFAULT 0,
    quote_delta REAL DEFAULT 0,
    size_usd REAL DEFAULT 0,
    price_usd REAL DEFAULT 0,
    pool_score REAL DEFAULT 0,
    fee_yield_daily REAL DEFAULT 0,
    net_edge REAL DEFAULT 0,
    block_ts TEXT,
    detected_at TEXT,
    PRIMARY KEY (sig, wallet, kind)
);
CREATE INDEX IF NOT EXISTS idx_lp_wallet ON lp_events(wallet);
CREATE INDEX IF NOT EXISTS idx_lp_pool ON lp_events(pool);
CREATE INDEX IF NOT EXISTS idx_lp_ts ON lp_events(block_ts);

CREATE TABLE IF NOT EXISTS lp_holdings (
    pool TEXT,
    wallet TEXT,
    lp_mint TEXT,
    amount REAL DEFAULT 0,
    pct REAL DEFAULT 0,
    value_usd REAL DEFAULT 0,
    supply REAL DEFAULT 0,
    rank INTEGER DEFAULT 0,
    dex TEXT,
    fetched_at TEXT,
    PRIMARY KEY (pool, wallet)
);
CREATE INDEX IF NOT EXISTS idx_holdings_wallet ON lp_holdings(wallet);
CREATE INDEX IF NOT EXISTS idx_holdings_pool ON lp_holdings(pool);
CREATE INDEX IF NOT EXISTS idx_holdings_ts ON lp_holdings(fetched_at);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT,
    kind TEXT,
    score REAL DEFAULT 0,
    dedupe TEXT UNIQUE,
    text TEXT,
    created_at TEXT,
    sent_at TEXT,
    status TEXT DEFAULT 'pending',
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_sent ON alerts(sent_at);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS seen_sigs (
    sig TEXT PRIMARY KEY,
    seen_at TEXT
);
"""


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def connect(path=None):
    config.ensure_dirs()
    path = str(path or config.DB_PATH)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Kolom baru pada tabel lama. CREATE TABLE IF NOT EXISTS tidak menambah
# kolom ke database yang sudah ada, jadi migrasi ringan ini perlu dijalankan.
WALLET_EXTRA_COLUMNS = {
    "lp_holdings": "INTEGER DEFAULT 0",
    "lp_hold_pct": "REAL DEFAULT 0",
    "lp_hold_usd": "REAL DEFAULT 0",
    "lp_hold_edge": "REAL DEFAULT 0",
    "lp_hold_quality": "REAL DEFAULT 0",
    "lp_hold_last_ts": "TEXT",
}


def _ensure_columns(conn, table, columns):
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)
    }
    for name, decl in columns.items():
        if name not in existing:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))


def init(conn):
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "wallets", WALLET_EXTRA_COLUMNS)
    # Bersihkan sekali event lama berkeyakinan "medium". Deteksi versi lama
    # memberinya label medium untuk perubahan saldo satu sisi; audit mainnet
    # membuktikan buy/sell PumpSwap juga hanya menampilkan satu sisi, jadi
    # event itu false positive. Kode baru tidak pernah membuat medium.
    done = conn.execute(
        "SELECT value FROM state WHERE key='migration_drop_medium_lp'"
    ).fetchone()
    if not done:
        conn.execute("DELETE FROM lp_events WHERE confidence='medium'")
        conn.execute(
            "INSERT OR REPLACE INTO state(key, value) VALUES(?, ?)",
            ("migration_drop_medium_lp", json.dumps(utc_now())),
        )
    conn.commit()
    return conn


def open_db(path=None):
    return init(connect(path))


# ---------------------------------------------------------------- state
def state_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return row["value"]


def state_set(conn, key, value):
    conn.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False, default=str)),
    )


# ---------------------------------------------------------------- pools
POOL_FIELDS = [
    "address", "name", "dex", "base_mint", "quote_mint", "base_symbol",
    "quote_symbol", "price_usd", "quote_price_usd", "fdv", "tvl", "vol_m5",
    "vol_h1", "vol_h6", "vol_h24", "chg_m5", "chg_h1", "chg_h6", "chg_h24",
    "buyers_h1", "sellers_h1", "created_at", "fee_pct", "pool_score",
    "fee_yield_daily", "winning", "reject", "first_seen", "last_seen",
]


def upsert_pools(conn, pools):
    now = utc_now()
    rows = []
    for pool in pools:
        row = {}
        for field in POOL_FIELDS:
            row[field] = pool.get(field)
        row["address"] = str(pool.get("address") or "")
        if not row["address"]:
            continue
        row["first_seen"] = pool.get("first_seen") or now
        row["last_seen"] = now
        row["winning"] = 1 if pool.get("winning") else 0
        rows.append(row)
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in POOL_FIELDS)
    updates = ",".join(
        "%s=excluded.%s" % (f, f) for f in POOL_FIELDS if f not in ("address", "first_seen")
    )
    sql = (
        "INSERT INTO pools (%s) VALUES (%s) ON CONFLICT(address) DO UPDATE SET %s"
        % (",".join(POOL_FIELDS), placeholders, updates)
    )
    conn.executemany(sql, [tuple(r[f] for f in POOL_FIELDS) for r in rows])
    conn.commit()
    return len(rows)


def all_pools(conn):
    rows = conn.execute("SELECT * FROM pools").fetchall()
    return [dict(r) for r in rows]


def pool_map(conn):
    return {r["address"]: dict(r) for r in conn.execute("SELECT * FROM pools")}


def winning_pools(conn, limit=None):
    sql = "SELECT * FROM pools WHERE winning=1 ORDER BY pool_score DESC, vol_h24 DESC"
    if limit:
        sql += " LIMIT %d" % int(limit)
    return [dict(r) for r in conn.execute(sql)]


def get_pool(conn, address):
    row = conn.execute("SELECT * FROM pools WHERE address=?", (address,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- trades
def insert_trades(conn, trades):
    if not trades:
        return 0
    now = utc_now()
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO trades "
        "(id, pool, wallet, token, side, price_usd, amount_usd, token_amount, "
        " block_ts, tx_hash, seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                t["id"], t["pool"], t["wallet"], t["token"], t["side"],
                t["price_usd"], t["amount_usd"], t["token_amount"],
                t["block_ts"], t.get("tx_hash") or "", now,
            )
            for t in trades
            if t.get("id") and t.get("wallet") and t.get("token")
        ],
    )
    inserted = conn.total_changes - before
    conn.commit()
    return inserted


def trade_count(conn):
    return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]


def refresh_wallet_tokens(conn):
    """Agregasi trade menjadi posisi per (wallet, token)."""
    conn.execute(
        """
        INSERT INTO wallet_tokens
            (wallet, token, pool, buys, sells, buy_usd, sell_usd, buy_qty,
             sell_qty, avg_buy, avg_sell, first_ts, last_ts)
        SELECT
            wallet,
            token,
            MAX(pool),
            SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END),
            SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END),
            SUM(CASE WHEN side='buy' THEN amount_usd ELSE 0 END),
            SUM(CASE WHEN side='sell' THEN amount_usd ELSE 0 END),
            SUM(CASE WHEN side='buy' THEN token_amount ELSE 0 END),
            SUM(CASE WHEN side='sell' THEN token_amount ELSE 0 END),
            CASE WHEN SUM(CASE WHEN side='buy' THEN token_amount ELSE 0 END) > 0
                 THEN SUM(CASE WHEN side='buy' THEN amount_usd ELSE 0 END)
                      / SUM(CASE WHEN side='buy' THEN token_amount ELSE 0 END)
                 ELSE 0 END,
            CASE WHEN SUM(CASE WHEN side='sell' THEN token_amount ELSE 0 END) > 0
                 THEN SUM(CASE WHEN side='sell' THEN amount_usd ELSE 0 END)
                      / SUM(CASE WHEN side='sell' THEN token_amount ELSE 0 END)
                 ELSE 0 END,
            MIN(block_ts),
            MAX(block_ts)
        FROM trades
        GROUP BY wallet, token
        ON CONFLICT(wallet, token) DO UPDATE SET
            pool=excluded.pool,
            buys=excluded.buys,
            sells=excluded.sells,
            buy_usd=excluded.buy_usd,
            sell_usd=excluded.sell_usd,
            buy_qty=excluded.buy_qty,
            sell_qty=excluded.sell_qty,
            avg_buy=excluded.avg_buy,
            avg_sell=excluded.avg_sell,
            first_ts=excluded.first_ts,
            last_ts=excluded.last_ts
        """
    )
    conn.execute(
        """
        UPDATE wallet_tokens SET
            current_price = COALESCE(
                (SELECT p.price_usd FROM pools p WHERE p.address = wallet_tokens.pool),
                current_price),
            peak_price = MAX(
                COALESCE(peak_price, 0),
                COALESCE(
                    (SELECT p.price_usd FROM pools p WHERE p.address = wallet_tokens.pool),
                    0))
        """
    )
    conn.commit()


# ---------------------------------------------------------------- wallets
def ensure_wallet(conn, address, when=None):
    if not address:
        return
    when = when or utc_now()
    conn.execute(
        "INSERT OR IGNORE INTO wallets(address, first_seen, last_seen) VALUES(?,?,?)",
        (address, when, when),
    )


def ensure_trade_wallets(conn):
    """Buat baris wallet untuk setiap wallet yang muncul di trade."""
    conn.execute(
        "INSERT OR IGNORE INTO wallets(address, first_seen, last_seen) "
        "SELECT wallet, MIN(block_ts), MAX(block_ts) FROM trades "
        "WHERE wallet IS NOT NULL AND wallet != '' GROUP BY wallet"
    )
    conn.commit()


def insert_lp_events(conn, events):
    """Simpan event LP; hitung edge awal (proxy 1 hari) saat pertama terlihat."""
    if not events:
        return 0
    now = utc_now()
    inserted = 0
    for ev in events:
        pool = ev.get("pool") or ""
        pool_score = float(ev.get("pool_score") or 0)
        fee_yield = float(ev.get("fee_yield_daily") or 0)
        price = float(ev.get("price_usd") or 0)
        chg24 = float(ev.get("chg_h24") or 0)
        edge = 0.0
        if fee_yield:
            entry = price / (1.0 + chg24 / 100.0) if price > 0 and chg24 > -95 else price
            # Proxy 1 hari: fee harian pool dikurangi IL dari perubahan harga 24 jam.
            edge = fee_yield - abs(scoring.impermanent_loss(entry, price))
        ensure_wallet(conn, ev.get("wallet"), ev.get("block_ts") or now)
        cur = conn.execute(
            "INSERT OR IGNORE INTO lp_events "
            "(sig, wallet, pool, program, dex, kind, confidence, base_delta, "
            " quote_delta, size_usd, price_usd, pool_score, fee_yield_daily, "
            " net_edge, block_ts, detected_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ev.get("sig"), ev.get("wallet"), pool, ev.get("program"),
                ev.get("dex"), ev.get("kind"), ev.get("confidence"),
                ev.get("base_delta"), ev.get("quote_delta"),
                ev.get("size_usd"), price, pool_score, fee_yield,
                round(edge, 6), ev.get("block_ts") or now, now,
            ),
        )
        if cur.rowcount:
            inserted += 1
    conn.commit()
    return inserted


def update_lp_edges(conn):
    """Perbarui fee>IL dengan harga pool terkini dan umur posisi."""
    rows = conn.execute(
        "SELECT e.rowid AS rid, e.*, p.price_usd AS current_price, p.tvl AS tvl, "
        "p.vol_h24 AS vol_h24, p.fee_pct AS fee_pct "
        "FROM lp_events e LEFT JOIN pools p ON p.address = e.pool "
        "WHERE e.kind='add' AND e.pool != ''"
    ).fetchall()
    now = utc_now()
    for row in rows:
        current = float(row["current_price"] or 0) or float(row["price_usd"] or 0)
        entry = float(row["price_usd"] or 0) or current
        if current <= 0 or entry <= 0:
            continue
        days = filters.age_days(row["block_ts"], datetime.datetime.fromisoformat(now))
        # Posisi yang baru beberapa menit jangan langsung diberi edge 0:
        # normalkan ke minimal 1 hari supaya fee harian tetap terukur.
        days = max(1.0, days)
        pool = {
            "tvl": float(row["tvl"] or 0),
            "vol_h24": float(row["vol_h24"] or 0),
            "fee_pct": float(row["fee_pct"] if row["fee_pct"] is not None else -1),
        }
        edge = scoring.lp_net_edge(entry, current, pool, days)
        conn.execute(
            "UPDATE lp_events SET net_edge=?, fee_yield_daily=? WHERE rowid=?",
            (round(edge, 6), round(scoring.fee_yield_daily(pool), 6), row["rid"]),
        )
    conn.commit()
    return len(rows)


def lp_aggregate(conn, address, lookback_days=None):
    """
    Gabungkan bukti LP dari dua sumber:

      - lp_events : transaksi add/remove yang terverifikasi on-chain;
      - lp_holdings: kepemilikan LP token saat ini (Raydium CPMM / v4).

    Kepemilikan token adalah bukti paling kuat. Edge posisi holder dihitung
    dengan proxy fee 1 hari (sama seperti event) karena harga masuknya tidak
    diketahui.
    """
    lookback_days = lookback_days or config.LP_EVENT_LOOKBACK_DAYS
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)
    ).replace(microsecond=0).isoformat()
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN kind='add' AND pool != '' THEN 1 ELSE 0 END) AS adds,
            SUM(CASE WHEN kind='remove' AND pool != '' THEN 1 ELSE 0 END) AS removes,
            COUNT(DISTINCT CASE WHEN pool != '' THEN pool END) AS pools,
            MAX(CASE WHEN kind='add' THEN size_usd ELSE 0 END) AS max_size,
            AVG(CASE WHEN kind='add' AND pool != '' THEN net_edge END) AS avg_edge,
            AVG(CASE WHEN kind='add' AND pool != '' THEN pool_score END) AS avg_quality,
            MAX(CASE WHEN kind='add' THEN block_ts END) AS last_ts
        FROM lp_events
        WHERE wallet=? AND block_ts >= ?
        """,
        (address, cutoff),
    ).fetchone()
    event_pools = {
        r["pool"]
        for r in conn.execute(
            "SELECT DISTINCT pool FROM lp_events "
            "WHERE wallet=? AND block_ts >= ? AND pool != ''",
            (address, cutoff),
        )
    }
    hold_rows = [
        dict(r)
        for r in conn.execute(
            "SELECT h.*, p.pool_score, p.fee_yield_daily, p.tvl, p.vol_h24, "
            " p.price_usd, p.chg_h24, p.fee_pct "
            "FROM lp_holdings h LEFT JOIN pools p ON p.address=h.pool "
            "WHERE h.wallet=? AND h.fetched_at >= ?",
            (address, cutoff),
        )
    ]

    adds = int(row["adds"] or 0)
    hold_count = len(hold_rows)
    edges = []
    quality = []
    if adds:
        edges.extend([float(row["avg_edge"] or 0)] * adds)
        quality.extend([float(row["avg_quality"] or 0)] * adds)
    hold_edges = []
    hold_quality = []
    max_hold_value = 0.0
    max_hold_pct = 0.0
    hold_last_ts = ""
    for item in hold_rows:
        price = float(item.get("price_usd") or 0)
        chg24 = float(item.get("chg_h24") or 0)
        entry = price / (1.0 + chg24 / 100.0) if price > 0 and chg24 > -95 else price
        pool = {
            "tvl": float(item.get("tvl") or 0),
            "vol_h24": float(item.get("vol_h24") or 0),
            "fee_pct": float(
                item.get("fee_pct") if item.get("fee_pct") is not None else -1
            ),
        }
        edge = scoring.lp_net_edge(entry, price, pool, 1.0)
        hold_edges.append(edge)
        hold_quality.append(float(item.get("pool_score") or 0))
        max_hold_value = max(max_hold_value, float(item.get("value_usd") or 0))
        max_hold_pct = max(max_hold_pct, float(item.get("pct") or 0))
        hold_last_ts = max(hold_last_ts, str(item.get("fetched_at") or ""))
    edges.extend(hold_edges)
    quality.extend(hold_quality)

    hold_pools = {item["pool"] for item in hold_rows if item.get("pool")}
    last_ts = max(
        [str(row["last_ts"] or "")] + ([hold_last_ts] if hold_last_ts else [])
    )
    out = {
        "adds": adds,
        "removes": int(row["removes"] or 0),
        "pools": len(event_pools | hold_pools),
        "max_size": max(float(row["max_size"] or 0), max_hold_value),
        "avg_edge": (sum(edges) / len(edges)) if edges else 0.0,
        "avg_quality": (sum(quality) / len(quality)) if quality else 0.0,
        "last_ts": last_ts if last_ts else "",
        "hold_count": hold_count,
        "hold_pools": len(hold_pools),
        "hold_max_pct": max_hold_pct,
        "hold_max_usd": max_hold_value,
        "hold_edge": (sum(hold_edges) / len(hold_edges)) if hold_edges else 0.0,
        "hold_quality": (
            (sum(hold_quality) / len(hold_quality)) if hold_quality else 0.0
        ),
        "hold_last_ts": hold_last_ts,
    }
    out["age_days"] = filters.age_days(out["last_ts"]) if out["last_ts"] else 9999.0
    return out


def refresh_wallet_scores(conn):
    """Hitung ulang metrik trading, skor LP, skor gabungan, dan tier."""
    addresses = [
        r["address"]
        for r in conn.execute(
            "SELECT w.address FROM wallets w "
            "WHERE w.ignored=0 AND ("
            "  EXISTS (SELECT 1 FROM lp_events e WHERE e.wallet=w.address) "
            "  OR EXISTS (SELECT 1 FROM lp_holdings h WHERE h.wallet=w.address) "
            "  OR EXISTS (SELECT 1 FROM trades t WHERE t.wallet=w.address) "
            # Sertakan wallet yang masih menyimpan agregat LP lama supaya bisa
            # direset setelah event/holding-nya hilang (mis. migrasi cleanup).
            "  OR w.lp_adds > 0 OR w.lp_holdings > 0"
            ") "
        )
    ]
    now = utc_now()
    for address in addresses:
        token_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM wallet_tokens WHERE wallet=?", (address,)
            )
        ]
        st = scoring.token_stats(token_rows)
        ts = scoring.trade_score(st)
        agg = lp_aggregate(conn, address)
        ls = scoring.lp_score(
            agg["adds"], agg["pools"], agg["avg_edge"], agg["age_days"],
            agg["avg_quality"], hold_count=agg["hold_count"],
        )
        has_lp = agg["adds"] > 0 or agg["hold_count"] > 0
        smart = scoring.smart_score(ls, ts, has_lp)
        conn.execute(
            """
            UPDATE wallets SET
                tokens=?, wins=?, win_rate=?, avg_peak_roi=?, realized_pnl=?,
                volume_usd=?,
                trades=(SELECT COUNT(*) FROM trades WHERE wallet=?),
                last_seen=COALESCE(
                    (SELECT MAX(block_ts) FROM trades WHERE wallet=?), last_seen),
                lp_adds=?, lp_removes=?, lp_pools=?, lp_size_usd=?,
                lp_edge=?, lp_quality=?, lp_last_ts=?,
                lp_holdings=?, lp_hold_pct=?, lp_hold_usd=?,
                lp_hold_edge=?, lp_hold_quality=?, lp_hold_last_ts=?,
                lp_score=?, trade_score=?, smart_score=?, tier=?
            WHERE address=?
            """,
            (
                st["tokens"], st["wins"], st["win_rate"], st["avg_peak_roi"],
                st["realized_pnl"], st["volume_usd"], address, address,
                agg["adds"], agg["removes"], agg["pools"], agg["max_size"],
                agg["avg_edge"], agg["avg_quality"], agg["last_ts"],
                agg["hold_count"], agg["hold_max_pct"], agg["hold_max_usd"],
                agg["hold_edge"], agg["hold_quality"], agg["hold_last_ts"],
                ls, ts, smart, scoring.tier(smart), address,
            ),
        )
    conn.execute("UPDATE wallets SET last_seen=? WHERE last_seen IS NULL", (now,))
    conn.commit()
    return len(addresses)


def get_wallet(conn, address):
    row = conn.execute("SELECT * FROM wallets WHERE address=?", (address,)).fetchone()
    return dict(row) if row else None


def top_wallets(conn, limit=10, eligible_only=True):
    sql = "SELECT * FROM wallets WHERE ignored=0 "
    if eligible_only:
        sql += (
            "AND (lp_adds > 0 OR lp_holdings > 0) "
            "AND smart_score >= %s AND lp_size_usd >= %s "
            % (float(config.TIER_C), float(config.MIN_LP_TOTAL_SIZE_USD))
        )
    sql += "ORDER BY smart_score DESC, lp_size_usd DESC LIMIT %d" % int(limit)
    return [dict(r) for r in conn.execute(sql)]


def wallets_for_enrichment(conn, limit):
    """Wallet aktif yang paling lama tidak dicek transaksi LP-nya."""
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=config.LP_CACHE_HOURS)
    ).replace(microsecond=0).isoformat()
    rows = conn.execute(
        """
        SELECT w.* FROM wallets w
        WHERE w.ignored=0
          AND (w.lp_checked_at IS NULL OR w.lp_checked_at < ?)
          AND (
            EXISTS (SELECT 1 FROM trades t WHERE t.wallet=w.address
                    AND t.block_ts >= ?)
            OR w.lp_adds > 0
            OR w.lp_holdings > 0
            OR w.note = 'seed'
          )
        ORDER BY w.smart_score DESC, w.last_seen DESC
        LIMIT ?
        """,
        (
            cutoff,
            (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=config.TRADE_LOOKBACK_HOURS)).isoformat(),
            int(limit),
        ),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_lp_checked(conn, address, when=None):
    conn.execute(
        "UPDATE wallets SET lp_checked_at=? WHERE address=?",
        (when or utc_now(), address),
    )
    conn.commit()


def wallet_lp_events(conn, address, limit=10):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT e.*, p.name AS pool_name, p.dex AS pool_dex "
            "FROM lp_events e LEFT JOIN pools p ON p.address=e.pool "
            "WHERE e.wallet=? ORDER BY e.block_ts DESC LIMIT ?",
            (address, int(limit)),
        )
    ]


def get_lp_event(conn, sig, wallet, kind):
    row = conn.execute(
        "SELECT e.*, p.name AS pool_name, p.dex AS pool_dex "
        "FROM lp_events e LEFT JOIN pools p ON p.address=e.pool "
        "WHERE e.sig=? AND e.wallet=? AND e.kind=?",
        (sig, wallet, kind),
    ).fetchone()
    return dict(row) if row else None


def upsert_lp_holders(
    conn,
    pool,
    lp_mint,
    supply,
    holders,
    dex="",
    tvl=0.0,
    fetched_at=None,
    min_pct=None,
    min_usd=None,
):
    """
    Simpan snapshot pemegang LP token sebuah pool.

    Snapshot lama diganti supaya holder yang sudah keluar tidak tertinggal.
    Kembalikan dict: stored (jumlah baris), new (holder yang belum ada di
    snapshot sebelumnya), dan wallets (semua holder yang disimpan).
    """
    now = fetched_at or utc_now()
    min_pct = config.LP_HOLDER_MIN_PCT if min_pct is None else float(min_pct)
    min_usd = config.LP_HOLDER_MIN_USD if min_usd is None else float(min_usd)
    old = {
        row["wallet"]
        for row in conn.execute(
            "SELECT wallet FROM lp_holdings WHERE pool=?", (pool,)
        )
    }
    conn.execute("DELETE FROM lp_holdings WHERE pool=?", (pool,))
    stored = []
    total = float(supply or 0)
    for holder in holders or []:
        wallet = holder.get("wallet") or ""
        if not wallet:
            continue
        pct = float(holder.get("pct") or 0)
        value = float(tvl or 0) * pct
        if pct < min_pct and value < min_usd:
            continue
        ensure_wallet(conn, wallet, now)
        row = {
            "pool": pool,
            "wallet": wallet,
            "lp_mint": lp_mint,
            "amount": float(holder.get("amount") or 0),
            "pct": pct,
            "value_usd": round(value, 2),
            "supply": total,
            "rank": int(holder.get("rank") or 0),
            "dex": dex,
            "fetched_at": now,
        }
        conn.execute(
            "INSERT OR REPLACE INTO lp_holdings "
            "(pool, wallet, lp_mint, amount, pct, value_usd, supply, rank, "
            " dex, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row["pool"], row["wallet"], row["lp_mint"], row["amount"],
                row["pct"], row["value_usd"], row["supply"], row["rank"],
                row["dex"], row["fetched_at"],
            ),
        )
        stored.append(row)
    conn.commit()
    return {
        "stored": len(stored),
        "wallets": [row["wallet"] for row in stored],
        "new": [row for row in stored if row["wallet"] not in old],
    }


def holders_for_pool(conn, pool, limit=50):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM lp_holdings WHERE pool=? "
            "ORDER BY pct DESC, value_usd DESC LIMIT ?",
            (pool, int(limit)),
        )
    ]


def wallet_lp_holdings(conn, address, limit=20):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT h.*, p.name AS pool_name, p.dex AS pool_dex, "
            " p.pool_score, p.fee_yield_daily, p.tvl "
            "FROM lp_holdings h LEFT JOIN pools p ON p.address=h.pool "
            "WHERE h.wallet=? ORDER BY h.pct DESC LIMIT ?",
            (address, int(limit)),
        )
    ]


def top_lp_holders(conn, limit=20):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT h.*, p.name AS pool_name, p.pool_score, p.tvl, "
            " w.smart_score, w.tier "
            "FROM lp_holdings h LEFT JOIN pools p ON p.address=h.pool "
            "LEFT JOIN wallets w ON w.address=h.wallet "
            "ORDER BY h.value_usd DESC, h.pct DESC LIMIT ?",
            (int(limit),),
        )
    ]


def wallet_tokens(conn, address, limit=20):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM wallet_tokens WHERE wallet=? "
            "ORDER BY (buy_usd + sell_usd) DESC LIMIT ?",
            (address, int(limit)),
        )
    ]


# ---------------------------------------------------------------- seen sigs
def filter_unseen_sigs(conn, sigs):
    if not sigs:
        return []
    marks = ",".join("?" for _ in sigs)
    known = {
        r["sig"]
        for r in conn.execute(
            "SELECT sig FROM seen_sigs WHERE sig IN (%s)" % marks, tuple(sigs)
        )
    }
    return [s for s in sigs if s not in known]


def mark_sigs_seen(conn, sigs):
    if not sigs:
        return
    now = utc_now()
    conn.executemany(
        "INSERT OR IGNORE INTO seen_sigs(sig, seen_at) VALUES(?,?)",
        [(s, now) for s in sigs],
    )
    conn.commit()


def prune(conn):
    old = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=config.SEEN_SIG_KEEP_DAYS)
    ).replace(microsecond=0).isoformat()
    conn.execute("DELETE FROM seen_sigs WHERE seen_at < ?", (old,))
    conn.commit()


# ---------------------------------------------------------------- alerts
def queue_alert(conn, wallet, kind, score, text, dedupe):
    now = utc_now()
    cur = conn.execute(
        "INSERT OR IGNORE INTO alerts(wallet, kind, score, dedupe, text, created_at, status) "
        "VALUES(?,?,?,?,?,?, 'pending')",
        (wallet, kind, score, dedupe, text, now),
    )
    conn.commit()
    return cur.rowcount > 0


def pending_alerts(conn, limit=10):
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=config.ALERT_QUEUE_TTL_SEC)
    ).replace(microsecond=0).isoformat()
    rows = conn.execute(
        "SELECT * FROM alerts WHERE status='pending' AND created_at >= ? "
        "ORDER BY score DESC, id ASC LIMIT ?",
        (cutoff, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_alert_sent(conn, alert_id):
    now = utc_now()
    conn.execute(
        "UPDATE alerts SET status='sent', sent_at=? WHERE id=?", (now, alert_id)
    )
    row = conn.execute("SELECT wallet FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if row:
        conn.execute(
            "UPDATE wallets SET alerts=alerts+1, last_alert_at=? WHERE address=?",
            (now, row["wallet"]),
        )
    conn.commit()


def mark_alert_failed(conn, alert_id, error):
    conn.execute(
        "UPDATE alerts SET status='failed', error=? WHERE id=?",
        (str(error)[:500], alert_id),
    )
    conn.commit()


def alert_counts(conn):
    now = datetime.datetime.now(datetime.timezone.utc)
    hour_ago = (now - datetime.timedelta(hours=1)).replace(microsecond=0).isoformat()
    day_ago = (now - datetime.timedelta(days=1)).replace(microsecond=0).isoformat()
    hour = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE sent_at >= ?", (hour_ago,)
    ).fetchone()[0]
    day = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE sent_at >= ?", (day_ago,)
    ).fetchone()[0]
    return int(hour), int(day)


# ---------------------------------------------------------------- ringkasan
def summary(conn):
    return {
        "pools": conn.execute("SELECT COUNT(*) FROM pools").fetchone()[0],
        "winning_pools": conn.execute(
            "SELECT COUNT(*) FROM pools WHERE winning=1"
        ).fetchone()[0],
        "trades": conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
        "wallets": conn.execute("SELECT COUNT(*) FROM wallets").fetchone()[0],
        "wallets_with_lp": conn.execute(
            "SELECT COUNT(*) FROM wallets WHERE lp_adds>0 OR lp_holdings>0"
        ).fetchone()[0],
        "lp_events": conn.execute("SELECT COUNT(*) FROM lp_events").fetchone()[0],
        "lp_holdings": conn.execute("SELECT COUNT(*) FROM lp_holdings").fetchone()[0],
        "wallets_with_holdings": conn.execute(
            "SELECT COUNT(DISTINCT wallet) FROM lp_holdings"
        ).fetchone()[0],
        "alerts_sent": conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE status='sent'"
        ).fetchone()[0],
    }
