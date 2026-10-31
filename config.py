#!/usr/bin/env python3
"""
Konfigurasi Top LP Smart Wallet Finder (Solana).

Bot ini mencari wallet yang layak disebut "smart LP":
  - pernah menambah likuiditas (LP add) di pool yang sedang menang, dan
  - secara proxy menghasilkan fee lebih besar daripada impermanent loss.

Semua sumber data gratis dan tanpa API key:
  - GeckoTerminal untuk pool trending, harga, volume, dan trade.
  - RPC Solana publik untuk membaca transaksi dan mendeteksi aksi LP.

Tidak ada private key, tidak ada transaksi keluar. Bot hanya membaca.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LSW_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("LSW_DB", DATA_DIR / "lp_smart_wallet.db"))


def _load_env(path):
    """Pembaca .env sederhana, tanpa dependensi luar."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env(BASE_DIR / ".env")


def _s(key, default=""):
    return os.environ.get(key, default).strip()


def _f(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _b(key, default):
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "ya")


# ---------------------------------------------------------------- identitas
CHAIN_NAME = "Solana"
GECKO_NETWORK = _s("GECKO_NETWORK", "solana")
EXPLORER_URL = _s("EXPLORER_URL", "https://solscan.io")
DEXSCREENER_URL = _s("DEXSCREENER_URL", "https://dexscreener.com/solana")
GMGN_URL = _s("GMGN_URL", "https://gmgn.ai/sol/address")
BIRDEYE_URL = _s("BIRDEYE_URL", "https://birdeye.so/token")

# ---------------------------------------------------------------- pool
# Trending pool diambil beberapa halaman. Halaman 1 biasanya sudah cukup,
# halaman 2 dipakai kalau ingin cakupan lebih luas (2 panggilan Gecko).
SCAN_PAGES = _i("SCAN_PAGES", 1)
SCAN_POOL_LIMIT = _i("SCAN_POOL_LIMIT", 40)

# Definisi "pool menang": ramai, likuid, dan tidak sedang dump.
MIN_POOL_VOL_USD = _f("MIN_POOL_VOL_USD", 100_000)
MIN_POOL_TVL_USD = _f("MIN_POOL_TVL_USD", 20_000)
MIN_V_TVL = _f("MIN_V_TVL", 2.0)
MAX_POOL_DROP_1H = _f("MAX_POOL_DROP_1H", -25.0)   # chg 1 jam minimal -25%
MAX_POOL_DROP_24H = _f("MAX_POOL_DROP_24H", -60.0)  # chg 24 jam minimal -60%
MIN_POOL_BUYERS_1H = _i("MIN_POOL_BUYERS_1H", 10)

# Fee pool default kalau GeckoTerminal tidak menyebutkan fee spesifik.
# Dipakai untuk proxy "fee > IL", bukan angka pasti.
DEFAULT_FEE_RATE = _f("DEFAULT_FEE_RATE", 0.003)
MAX_FEE_YIELD = _f("MAX_FEE_YIELD", 5.0)  # batas wajar fee 500% per periode
LP_MAX_HOLD_DAYS = _f("LP_MAX_HOLD_DAYS", 30.0)

# ---------------------------------------------------------------- trade
# Trade dipakai untuk menemukan wallet aktif dan mengukur kualitas entry.
TRADE_POOL_LIMIT = _i("TRADE_POOL_LIMIT", 5)
MIN_TRADE_USD = _f("MIN_TRADE_USD", 5.0)
WALLET_MIN_TRADES = _i("WALLET_MIN_TRADES", 2)
WALLET_MIN_VOLUME_USD = _f("WALLET_MIN_VOLUME_USD", 25.0)
TRADE_LOOKBACK_HOURS = _i("TRADE_LOOKBACK_HOURS", 72)

# ---------------------------------------------------------------- deteksi LP
RPC_URL = _s("RPC_URL", "https://solana-rpc.publicnode.com")
RPC_FALLBACKS = [
    u.strip()
    for u in _s(
        "RPC_FALLBACKS",
        "https://api.mainnet-beta.solana.com,https://solana.drpc.org",
    ).split(",")
    if u.strip()
]
RPC_MIN_INTERVAL = _f("RPC_MIN_INTERVAL", 0.12)
RPC_TIMEOUT = _f("RPC_TIMEOUT", 25.0)
RPC_RETRIES = _i("RPC_RETRIES", 2)
# Anggaran panggilan RPC per proses. RPC publik membatasi laju; lebih baik
# memproses sedikit pool tiap putaran daripada diblokir.
RPC_BUDGET_PER_RUN = _i("RPC_BUDGET_PER_RUN", 90)

LP_POOL_SCAN_LIMIT = _i("LP_POOL_SCAN_LIMIT", 2)
LP_POOL_TX_SCAN = _i("LP_POOL_TX_SCAN", 15)
LP_WALLET_ENRICH_LIMIT = _i("LP_WALLET_ENRICH_LIMIT", 6)
LP_WALLET_TX_SCAN = _i("LP_WALLET_TX_SCAN", 5)
LP_WALLET_SIG_LIMIT = _i("LP_WALLET_SIG_LIMIT", 12)
LP_CACHE_HOURS = _f("LP_CACHE_HOURS", 6.0)
LP_EVENT_LOOKBACK_DAYS = _i("LP_EVENT_LOOKBACK_DAYS", 30)
MIN_LP_SIZE_USD = _f("MIN_LP_SIZE_USD", 200.0)
# Wallet di bawah ukuran ini tidak dianggap bukti LP yang layak dianalisis.
MIN_LP_TOTAL_SIZE_USD = _f("MIN_LP_TOTAL_SIZE_USD", 500.0)

# ---------------------------------------------------------------- pemegang LP
# Enumerasi langsung pemegang LP token (Raydium CPMM / AMM v4). Ini bukti
# paling kuat bahwa sebuah wallet benar-benar LP, bukan sekadar trader.
LP_HOLDER_SCAN_LIMIT = _i("LP_HOLDER_SCAN_LIMIT", 3)
LP_HOLDER_CACHE_MIN = _f("LP_HOLDER_CACHE_MIN", 20.0)
LP_HOLDER_TOP = _i("LP_HOLDER_TOP", 20)
# Simpan hanya holder dengan share/nilai yang berarti.
LP_HOLDER_MIN_PCT = _f("LP_HOLDER_MIN_PCT", 0.001)
LP_HOLDER_MIN_USD = _f("LP_HOLDER_MIN_USD", 500.0)
# Ambang alert "TOP LP DETECTED" (lebih ketat daripada ambang simpan).
LP_HOLDER_ALERT_MIN_PCT = _f("LP_HOLDER_ALERT_MIN_PCT", 0.02)
LP_HOLDER_ALERT_MIN_USD = _f("LP_HOLDER_ALERT_MIN_USD", 5000.0)
LP_HOLDER_COOLDOWN_HOURS = _f("LP_HOLDER_COOLDOWN_HOURS", 24.0)

# Alamat yang tidak boleh masuk ranking (router/program/known pool).
IGNORE_ADDRESSES = {
    a.strip()
    for a in _s(
        "IGNORE_ADDRESSES",
        "11111111111111111111111111111111,"
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4,"
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    ).split(",")
    if a.strip()
}

# ---------------------------------------------------------------- skor
LP_W_ACTIVITY = _f("LP_W_ACTIVITY", 0.20)
LP_W_QUALITY = _f("LP_W_QUALITY", 0.30)
LP_W_EDGE = _f("LP_W_EDGE", 0.30)
LP_W_RECENCY = _f("LP_W_RECENCY", 0.20)
LP_TARGET_ADDS = _i("LP_TARGET_ADDS", 8)
LP_EDGE_FULL = _f("LP_EDGE_FULL", 0.05)  # edge +5% dianggap skor penuh

TRADE_W_WINRATE = _f("TRADE_W_WINRATE", 0.35)
TRADE_W_PEAK = _f("TRADE_W_PEAK", 0.30)
TRADE_W_ACTIVITY = _f("TRADE_W_ACTIVITY", 0.15)
TRADE_W_PNL = _f("TRADE_W_PNL", 0.20)
TRADE_TARGET_TRADES = _i("TRADE_TARGET_TRADES", 20)
TRADE_PEAK_FULL = _f("TRADE_PEAK_FULL", 2.0)  # avg peak ROI 200% = penuh

# Bobot gabungan: bukti LP lebih penting daripada skill trading.
SMART_W_LP = _f("SMART_W_LP", 0.70)
SMART_W_TRADE = _f("SMART_W_TRADE", 0.30)
NO_LP_SCORE_CAP = _f("NO_LP_SCORE_CAP", 45.0)

TIER_S = _f("TIER_S", 85.0)
TIER_A = _f("TIER_A", 70.0)
TIER_B = _f("TIER_B", 55.0)
TIER_C = _f("TIER_C", 40.0)

# ---------------------------------------------------------------- alert
TELEGRAM_BOT_TOKEN = _s("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _s("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = _b("TELEGRAM_ENABLED", True)
ALERT_LANG = _s("ALERT_LANG", "id").strip().lower()
if ALERT_LANG not in ("en", "id", "both"):
    ALERT_LANG = "id"
TELEGRAM_MIN_INTERVAL_SEC = _f("TELEGRAM_MIN_INTERVAL_SEC", 1.2)
TELEGRAM_MAX_PER_RUN = _i("TELEGRAM_MAX_PER_RUN", 4)
TELEGRAM_MAX_PER_HOUR = _i("TELEGRAM_MAX_PER_HOUR", 30)
TELEGRAM_MAX_PER_DAY = _i("TELEGRAM_MAX_PER_DAY", 250)

ALERT_MIN_SCORE = _f("ALERT_MIN_SCORE", 70.0)
ALERT_COOLDOWN_HOURS = _f("ALERT_COOLDOWN_HOURS", 24.0)
ALERT_MAX_PER_RUN = _i("ALERT_MAX_PER_RUN", 3)
ALERT_LP_EVENTS = _b("ALERT_LP_EVENTS", True)
ALERT_LP_HOLDERS = _b("ALERT_LP_HOLDERS", True)
LP_ALERT_MAX_AGE_HOURS = _f("LP_ALERT_MAX_AGE_HOURS", 6.0)
LP_EVENT_COOLDOWN_HOURS = _f("LP_EVENT_COOLDOWN_HOURS", 6.0)
ALERT_NEW_WALLET_AFTER_DAYS = _f("ALERT_NEW_WALLET_AFTER_DAYS", 7.0)
ALERT_QUEUE_TTL_SEC = _i("ALERT_QUEUE_TTL_SEC", 1800)
ALERT_MAX_ATTEMPTS = _i("ALERT_MAX_ATTEMPTS", 3)

# Wallet yang selalu dipantau (dipisah koma). Boleh kosong.
SEED_WALLETS = [
    w.strip()
    for w in _s("SEED_WALLETS", "").split(",")
    if w.strip()
]

# ---------------------------------------------------------------- operasional
SCAN_INTERVAL_SEC = _i("SCAN_INTERVAL_SEC", 300)
LOG_KEEP_DAYS = _i("LOG_KEEP_DAYS", 14)
SEEN_SIG_KEEP_DAYS = _i("SEEN_SIG_KEEP_DAYS", 7)
HTTP_TIMEOUT = _f("HTTP_TIMEOUT", 25.0)
HTTP_RETRIES = _i("HTTP_RETRIES", 3)
GECKO_MIN_INTERVAL = _f("GECKO_MIN_INTERVAL", 3.5)
USER_AGENT = _s(
    "USER_AGENT",
    "lp-smart-wallet-finder/1.0 (Solana; read-only research bot)",
)
DRY_RUN = _b("DRY_RUN", False)


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def summary():
    return {
        "chain": CHAIN_NAME,
        "pool": {
            "vol_min": MIN_POOL_VOL_USD,
            "tvl_min": MIN_POOL_TVL_USD,
            "v_tvl_min": MIN_V_TVL,
            "drop_1h_max": MAX_POOL_DROP_1H,
            "buyers_1h_min": MIN_POOL_BUYERS_1H,
        },
        "lp": {
            "pool_scan": LP_POOL_SCAN_LIMIT,
            "tx_scan": LP_POOL_TX_SCAN,
            "wallet_enrich": LP_WALLET_ENRICH_LIMIT,
            "min_size": MIN_LP_SIZE_USD,
        },
        "alert": {"score_min": ALERT_MIN_SCORE, "max_per_run": ALERT_MAX_PER_RUN},
        "rpc_budget": RPC_BUDGET_PER_RUN,
    }
