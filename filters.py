#!/usr/bin/env python3
"""Filter pool dan wallet, plus helper format yang dipakai UI/alert."""

import datetime

import config
import scoring


def v_tvl(pool):
    return scoring.v_tvl(pool)


def pool_fee_yield(pool):
    return scoring.fee_yield_daily(pool)


def pool_quality(pool):
    return scoring.pool_score(pool)


def pool_rejection_reasons(pool):
    """Daftar alasan pool tidak masuk kategori 'menang'."""
    reasons = []
    vol = float(pool.get("vol_h24") or 0)
    tvl = float(pool.get("tvl") or 0)
    ratio = v_tvl(pool)
    if vol < config.MIN_POOL_VOL_USD:
        reasons.append("volume 24j < %s" % short_money(config.MIN_POOL_VOL_USD))
    if tvl < config.MIN_POOL_TVL_USD:
        reasons.append("TVL < %s" % short_money(config.MIN_POOL_TVL_USD))
    if ratio < config.MIN_V_TVL:
        reasons.append("V/TVL %.2fx < %.2fx" % (ratio, config.MIN_V_TVL))
    if float(pool.get("chg_h1") or 0) < config.MAX_POOL_DROP_1H:
        reasons.append("dump 1j %.1f%%" % float(pool.get("chg_h1") or 0))
    if float(pool.get("chg_h24") or 0) < config.MAX_POOL_DROP_24H:
        reasons.append("dump 24j %.1f%%" % float(pool.get("chg_h24") or 0))
    if int(pool.get("buyers_h1") or 0) < config.MIN_POOL_BUYERS_1H:
        reasons.append("buyer 1j < %d" % config.MIN_POOL_BUYERS_1H)
    return reasons


def is_winning_pool(pool):
    return not pool_rejection_reasons(pool)


def apply_pool_metrics(pool):
    """Lengkapi pool dengan status menang, skor, dan fee yield."""
    reasons = pool_rejection_reasons(pool)
    pool["winning"] = not reasons
    pool["reject"] = "; ".join(reasons)
    pool["pool_score"] = scoring.pool_score(pool)
    pool["fee_yield_daily"] = round(scoring.fee_yield_daily(pool), 6)
    return pool


def ranked_winning_pools(pools, limit=None):
    """Pool menang, diurutkan dari yang paling menarik untuk LP."""
    rows = [p for p in pools if is_winning_pool(p)]
    for p in rows:
        p["pool_score"] = pool_quality(p)
        p["fee_yield_daily"] = round(pool_fee_yield(p), 6)
    rows.sort(
        key=lambda p: (p.get("pool_score", 0), p.get("fee_yield_daily", 0)),
        reverse=True,
    )
    return rows[:limit] if limit else rows


def wallet_is_ignored(address, ignored=None):
    if not address or address in config.IGNORE_ADDRESSES:
        return True
    if ignored and address in ignored:
        return True
    return False


def wallet_has_lp_evidence(row):
    """Bukti LP: transaksi add yang terverifikasi atau LP token yang dipegang."""
    return (
        int(row.get("lp_adds") or 0) > 0
        or int(row.get("lp_holdings") or 0) > 0
    )


def wallet_eligible(row):
    """Cukup data untuk masuk ranking/alert."""
    if wallet_is_ignored(row.get("address")):
        return False
    if row.get("ignored"):
        return False
    if not wallet_has_lp_evidence(row):
        return False
    if float(row.get("lp_size_usd") or 0) < config.MIN_LP_TOTAL_SIZE_USD:
        return False
    if float(row.get("smart_score") or 0) < config.TIER_C:
        return False
    return True


def alert_ready(row, now=None):
    """Wallet layak dikirim sebagai alert baru."""
    if not wallet_eligible(row):
        return False
    if float(row.get("smart_score") or 0) < config.ALERT_MIN_SCORE:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    last = row.get("last_alert_at")
    if last:
        try:
            then = datetime.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if then.tzinfo is None:
                then = then.replace(tzinfo=datetime.timezone.utc)
            hours = (now - then).total_seconds() / 3600.0
            if hours < config.ALERT_COOLDOWN_HOURS:
                return False
        except (ValueError, TypeError):
            pass
    return True


def short_addr(address, head=5, tail=4):
    address = str(address or "")
    if len(address) <= head + tail + 1:
        return address
    return "%s...%s" % (address[:head], address[-tail:])


def short_money(value):
    value = float(value or 0)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000:
        return "%s$%.2fB" % (sign, value / 1_000_000_000)
    if value >= 1_000_000:
        return "%s$%.2fM" % (sign, value / 1_000_000)
    if value >= 1_000:
        return "%s$%.1fK" % (sign, value / 1_000)
    if value >= 1:
        return "%s$%.0f" % (sign, value)
    return "%s$%.2f" % (sign, value)


def short_pct(value, digits=1):
    value = float(value or 0)
    return "%+.{}f%%".format(digits) % value


def age_days(iso_text, now=None):
    if not iso_text:
        return 9999.0
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        then = datetime.datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, (now - then).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 9999.0


def age_label(iso_text, now=None):
    days = age_days(iso_text, now)
    if days >= 9999:
        return "-"
    if days >= 1:
        return "%.1fh lalu" % (days * 24.0) if days < 2 else "%.0fh lalu" % (days * 24.0)
    hours = days * 24.0
    if hours >= 1:
        return "%.1fj lalu" % hours
    return "%.0fm lalu" % max(1.0, hours * 60.0)
