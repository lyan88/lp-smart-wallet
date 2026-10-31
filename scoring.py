#!/usr/bin/env python3
"""
Skor murni (tanpa I/O) untuk wallet LP dan pool.

Semua angka adalah proxy yang dijelaskan di README:
  - fee yield  : volume 24 jam / TVL x fee rate (asumsi full-range, share 1)
  - IL         : rumus full-range 2*sqrt(r)/(1+r) - 1
  - edge       : fee yield - |IL| selama posisi dipegang
Ini bukan laporan PnL akuntansi; ini penyaring awal yang konsisten.
"""

import math

import config


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def fee_rate_for(pool):
    """Fee rate desimal. Gecko memberi persen (0.3 = 0.3%)."""
    pct = float((pool or {}).get("fee_pct") or -1)
    if 0 <= pct <= 10:
        return pct / 100.0
    return config.DEFAULT_FEE_RATE


def v_tvl(pool):
    tvl = float((pool or {}).get("tvl") or 0)
    vol = float((pool or {}).get("vol_h24") or 0)
    return (vol / tvl) if tvl > 0 else 0.0


def fee_yield_daily(pool):
    """Perkiraan fee per hari sebagai fraksi dari nilai posisi."""
    rate = fee_rate_for(pool)
    raw = v_tvl(pool) * rate
    return max(0.0, min(raw, config.MAX_FEE_YIELD))


def impermanent_loss(entry_price, current_price):
    """IL full-range (negatif). 0 kalau harga tidak berubah."""
    entry = float(entry_price or 0)
    current = float(current_price or 0)
    if entry <= 0 or current <= 0:
        return 0.0
    ratio = current / entry
    if ratio <= 0:
        return 0.0
    return (2.0 * math.sqrt(ratio) / (1.0 + ratio)) - 1.0


def lp_net_edge(entry_price, current_price, pool, days, share=1.0):
    """
    Fee - |IL| selama `days` hari. Positif berarti fee menutup IL.
    `share` 1.0 berarti seluruh TVL pool dianggap milik posisi ini.
    """
    days = max(0.0, min(float(days or 0), config.LP_MAX_HOLD_DAYS))
    if days <= 0:
        return 0.0
    fees = fee_yield_daily(pool) * days * clamp(float(share or 0), 0.0, 1.0)
    il = abs(impermanent_loss(entry_price, current_price))
    return fees - il


def pool_score(pool):
    """0-100: seberapa menarik pool untuk LP saat ini."""
    vol = max(0.0, float((pool or {}).get("vol_h24") or 0))
    tvl = max(0.0, float((pool or {}).get("tvl") or 0))
    ratio = v_tvl(pool)
    buyers = max(0.0, float((pool or {}).get("buyers_h1") or 0))
    chg24 = float((pool or {}).get("chg_h24") or 0)
    chg1 = float((pool or {}).get("chg_h1") or 0)

    vol_part = clamp((math.log10(vol) - 4.0) / 3.0) if vol > 0 else 0.0
    ratio_part = clamp(ratio / 10.0)
    buyer_part = clamp(buyers / 500.0)
    momentum = 1.0
    if chg24 < -25 or chg1 < -20:
        momentum = 0.0
    elif chg24 > 300:
        momentum = 0.5
    elif chg24 < 0:
        momentum = 0.6
    return round(
        100.0
        * (0.35 * vol_part + 0.30 * ratio_part + 0.20 * momentum + 0.15 * buyer_part),
        2,
    )


def token_stats(rows):
    """
    Hitung metrik trading dari baris wallet_tokens.

    Setiap baris butuh: buys, sells, buy_usd, sell_usd, buy_qty, sell_qty,
    avg_buy, avg_sell, peak_price, current_price.
    """
    wins = 0
    peaks = []
    realized = 0.0
    volume = 0.0
    for row in rows or []:
        buy_usd = float(row.get("buy_usd") or 0)
        sell_usd = float(row.get("sell_usd") or 0)
        volume += buy_usd + sell_usd
        avg_buy = float(row.get("avg_buy") or 0)
        avg_sell = float(row.get("avg_sell") or 0)
        peak = float(row.get("peak_price") or 0)
        sell_qty = float(row.get("sell_qty") or 0)
        peak_roi = ((peak / avg_buy) - 1.0) if avg_buy > 0 and peak > 0 else 0.0
        if avg_buy > 0 and peak > 0:
            peaks.append(peak_roi)
        if sell_qty > 0 and avg_buy > 0:
            realized += sell_usd - (avg_buy * sell_qty)
            realized_roi = ((avg_sell / avg_buy) - 1.0) if avg_sell > 0 else 0.0
            if realized_roi >= 0.20 or peak_roi >= 0.30:
                wins += 1
        elif peak_roi >= 0.30:
            # Masih dipegang, tapi pernah naik cukup jauh.
            wins += 1
    tokens = len(rows or [])
    win_rate = (wins / tokens) if tokens else 0.0
    avg_peak = (sum(peaks) / len(peaks)) if peaks else 0.0
    return {
        "tokens": tokens,
        "wins": wins,
        "win_rate": win_rate,
        "avg_peak_roi": avg_peak,
        "realized_pnl": realized,
        "volume_usd": volume,
    }


def trade_score(stats):
    """0-100 dari skill trading (entry timing, win rate, aktivitas)."""
    tokens = int(stats.get("tokens") or 0)
    win_rate = clamp(float(stats.get("win_rate") or 0))
    avg_peak = max(0.0, float(stats.get("avg_peak_roi") or 0))
    pnl = float(stats.get("realized_pnl") or 0)
    volume = max(1.0, float(stats.get("volume_usd") or 0))
    if tokens <= 0:
        return 0.0
    peak_part = clamp(avg_peak / config.TRADE_PEAK_FULL)
    activity_part = clamp(tokens / float(config.TRADE_TARGET_TRADES or 1))
    pnl_part = clamp(0.5 + (pnl / volume) * 5.0)
    score = (
        config.TRADE_W_WINRATE * win_rate
        + config.TRADE_W_PEAK * peak_part
        + config.TRADE_W_ACTIVITY * activity_part
        + config.TRADE_W_PNL * pnl_part
    ) * 100.0
    return round(clamp(score, 0.0, 100.0), 2)


def lp_score(adds, pools, avg_edge, age_days, avg_quality, hold_count=0):
    """
    0-100 dari bukti LP: jumlah, kualitas pool, edge fee>IL, recency.

    `hold_count` adalah jumlah posisi LP token yang masih dipegang. Holding
    dihitung sebagai bukti aktivitas karena tidak semua DEX meninggalkan
    jejak transaksi add yang mudah dibaca.
    """
    adds = max(0, int(adds or 0))
    hold_count = max(0, int(hold_count or 0))
    evidence = adds + hold_count
    if evidence <= 0:
        return 0.0
    activity = clamp(evidence / float(config.LP_TARGET_ADDS or 1))
    quality = clamp(float(avg_quality or 0) / 100.0)
    edge = clamp(float(avg_edge or 0) / float(config.LP_EDGE_FULL or 0.05))
    recency = clamp(1.0 - (max(0.0, float(age_days or 0)) / max(1, config.LP_EVENT_LOOKBACK_DAYS)))
    score = (
        config.LP_W_ACTIVITY * activity
        + config.LP_W_QUALITY * quality
        + config.LP_W_EDGE * edge
        + config.LP_W_RECENCY * recency
    ) * 100.0
    return round(clamp(score, 0.0, 100.0), 2)


def smart_score(lp, trade, has_lp_evidence=True):
    """Gabungan 0-100. Tanpa bukti LP, skor dibatasi."""
    lp = clamp(float(lp or 0), 0.0, 100.0)
    trade = clamp(float(trade or 0), 0.0, 100.0)
    if not has_lp_evidence:
        return round(min(config.NO_LP_SCORE_CAP, trade * 0.55), 2)
    total = config.SMART_W_LP * lp + config.SMART_W_TRADE * trade
    return round(clamp(total, 0.0, 100.0), 2)


def tier(score):
    score = float(score or 0)
    if score >= config.TIER_S:
        return "S"
    if score >= config.TIER_A:
        return "A"
    if score >= config.TIER_B:
        return "B"
    if score >= config.TIER_C:
        return "C"
    return "D"


def summarize(pool):
    """Ringkasan satu pool untuk laporan/alert."""
    return {
        "score": pool_score(pool),
        "fee_yield_daily": round(fee_yield_daily(pool), 6),
        "v_tvl": round(v_tvl(pool), 2),
        "fee_rate": fee_rate_for(pool),
    }
