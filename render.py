#!/usr/bin/env python3
"""Perakit pesan Telegram (HTML) untuk alert dan laporan."""

import datetime
import html

import config
import filters
import i18n

TIER_EMOJI = {"S": "🟣", "A": "🟢", "B": "🔵", "C": "🟡", "D": "⚪"}


def _e(value):
    return html.escape(str(value if value is not None else ""), quote=False)


def _tier(row):
    tier = row.get("tier") or "D"
    return "%s %s" % (TIER_EMOJI.get(tier, "⚪"), tier)


def _pct(fraction, digits=1):
    return "%+.*f%%" % (digits, float(fraction or 0) * 100.0)


def _age(iso_text):
    try:
        then = datetime.datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=datetime.timezone.utc)
        seconds = (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds()
        return i18n.age_text(seconds)
    except (ValueError, TypeError):
        return "-"


def _pool_name(event):
    name = event.get("pool_name") or ""
    if name:
        return _e(name)
    pool = event.get("pool") or ""
    return filters.short_addr(pool, 5, 4) if pool else "?"


def _wallet_block(row, events, lang):
    L = i18n.labels(lang)
    address = row.get("address") or ""
    tier = row.get("tier") or "D"
    lines = []
    lines.append(
        "%s <b>%s · %s · %.1f</b>"
        % (TIER_EMOJI.get(tier, "⚪"), L["wallet_alert"], _tier(row),
           float(row.get("smart_score") or 0))
    )
    lines.append("<code>%s</code>" % _e(address))
    lines.append("")

    lp_bits = [
        "%d %s" % (int(row.get("lp_adds") or 0), L["lp_adds"]),
        "%d %s" % (int(row.get("lp_pools") or 0), L["lp_pools"]),
        "%s %s" % (filters.short_money(row.get("lp_size_usd") or 0), L["lp_size"].lower()),
    ]
    if int(row.get("lp_holdings") or 0) > 0:
        lp_bits.append(
            "%d %s" % (int(row.get("lp_holdings") or 0), L["lp_holdings"])
        )
    lines.append("<b>%s</b>: %s" % (L["lp_evidence"], " · ".join(lp_bits)))
    lines.append(
        "%s: %s · %s %s"
        % (
            L["lp_edge"], _pct(row.get("lp_edge") or 0),
            L["lp_last"], _age(row.get("lp_last_ts")),
        )
    )

    if int(row.get("tokens") or 0) > 0:
        lines.append(
            "<b>%s</b>: %.0f%% %s · %s %s · %d %s · %s"
            % (
                L["trading"],
                float(row.get("win_rate") or 0) * 100,
                L["winrate"].lower(),
                _pct(row.get("avg_peak_roi") or 0, 0),
                L["avg_peak"].lower(),
                int(row.get("trades") or 0), L["trades"],
                filters.short_money(row.get("volume_usd") or 0),
            )
        )
    else:
        lines.append("<b>%s</b>: —" % L["trading"])

    adds = [e for e in events if e.get("kind") == "add"][:3]
    if adds:
        lines.append("")
        lines.append("<b>%s</b>" % L["recent_lp"])
        for ev in adds:
            lines.append(
                "• %s · %s · %s · %s"
                % (
                    _pool_name(ev),
                    filters.short_money(ev.get("size_usd") or 0),
                    _pct(ev.get("net_edge") or 0),
                    _age(ev.get("block_ts")),
                )
            )

    lines.append("")
    reasons = []
    if adds:
        top = adds[0]
        reasons.append(
            ("LP add to " if lang == "en" else "LP add di ") + _pool_name(top)
        )
        if float(top.get("fee_yield_daily") or 0) > 0:
            reasons.append(
                "%s %.1f%%/%s"
                % (L["fees"], float(top["fee_yield_daily"]) * 100,
                   "day" if lang == "en" else "hari")
            )
        reasons.append("%s %s" % (L["lp_edge"].lower(), _pct(top.get("net_edge") or 0)))
    if row.get("win_rate"):
        reasons.append(
            "%s %.0f%%" % (L["winrate"].lower(), float(row["win_rate"]) * 100)
        )
    lines.append("<b>%s</b>: %s" % (L["why"], "; ".join(reasons) or "—"))

    links = [
        '<a href="%s/account/%s">%s</a>'
        % (_e(config.EXPLORER_URL.rstrip("/")), _e(address), L["open"])
    ]
    if adds and adds[0].get("sig"):
        links.append(
            '<a href="%s/tx/%s">%s</a>'
            % (_e(config.EXPLORER_URL.rstrip("/")), _e(adds[0]["sig"]), L["tx"])
        )
    lines.append(" · ".join(links))
    lines.append("<i>%s</i>" % L["disclaimer"])
    return "\n".join(lines)


def wallet_alert(row, events, lang=None):
    lang = lang or config.ALERT_LANG
    if lang == "both":
        return (
            _wallet_block(row, events, "en")
            + "\n\n———\n\n"
            + _wallet_block(row, events, "id")
        )
    return _wallet_block(row, events, lang if lang in ("id", "en") else "id")


def _lp_event_block(row, event, lang):
    L = i18n.labels(lang)
    address = row.get("address") or ""
    lines = []
    lines.append(
        "🟢 <b>%s · %s</b>" % (L["lp_add_alert"], _tier(row))
    )
    lines.append("<code>%s</code>" % _e(address))
    lines.append("<b>%s</b>: %s" % (L["pool"], _pool_name(event)))
    lines.append(
        "<b>%s</b>: %s · %s %.1f%%/%s · %s %.0f"
        % (
            L["size"], filters.short_money(event.get("size_usd") or 0),
            L["fees"], float(event.get("fee_yield_daily") or 0) * 100,
            "day" if lang == "en" else "hari",
            L["score"], float(event.get("pool_score") or 0),
        )
    )
    lines.append(
        "<b>%s</b>: %s · %d %s · %d %s · %s %s"
        % (
            L["wallet_alert"], _tier(row),
            int(row.get("lp_adds") or 0), L["lp_adds"],
            int(row.get("lp_pools") or 0), L["lp_pools"],
            L["lp_edge"].lower(), _pct(event.get("net_edge") or 0),
        )
    )
    lines.append("<b>%s</b>: %s" % (L["age"], _age(event.get("block_ts"))))
    links = [
        '<a href="%s/account/%s">%s</a>'
        % (_e(config.EXPLORER_URL.rstrip("/")), _e(address), L["open"])
    ]
    if event.get("sig"):
        links.append(
            '<a href="%s/tx/%s">%s</a>'
            % (_e(config.EXPLORER_URL.rstrip("/")), _e(event["sig"]), L["tx"])
        )
    lines.append(" · ".join(links))
    lines.append("<i>%s</i>" % L["disclaimer"])
    return "\n".join(lines)


def lp_event_alert(row, event, lang=None):
    lang = lang or config.ALERT_LANG
    if lang == "both":
        return (
            _lp_event_block(row, event, "en")
            + "\n\n———\n\n"
            + _lp_event_block(row, event, "id")
        )
    return _lp_event_block(row, event, lang if lang in ("id", "en") else "id")


def _lp_holder_block(row, holding, lang):
    L = i18n.labels(lang)
    address = row.get("address") or ""
    pct = float(holding.get("pct") or 0)
    value = float(holding.get("value_usd") or 0)
    rank = int(holding.get("rank") or 0)
    pool_name = holding.get("pool_name") or filters.short_addr(
        holding.get("pool"), 5, 4
    )
    lines = []
    lines.append(
        "🟣 <b>%s · %s</b>" % (L["lp_hold_alert"], _tier(row))
    )
    lines.append("<code>%s</code>" % _e(address))
    lines.append("<b>%s</b>: %s" % (L["pool"], _e(pool_name)))
    share = "<b>%s</b>: %.2f%% · %s" % (
        L["lp_share"], pct * 100.0, filters.short_money(value),
    )
    if rank:
        share += " · %s #%d" % (L["lp_rank"], rank)
    lines.append(share)
    lines.append(
        "<b>%s</b>: %.1f%%/%s · %s %s"
        % (
            L["fees"], float(holding.get("fee_yield_daily") or 0) * 100,
            "day" if lang == "en" else "hari",
            L["lp_edge"].lower(), _pct(holding.get("edge") or 0),
        )
    )
    lines.append(
        "<b>%s</b>: %s · %.1f"
        % (
            L["wallet_alert"], _tier(row),
            float(row.get("smart_score") or 0),
        )
    )
    lines.append("<b>%s</b>: %s" % (L["age"], _age(holding.get("fetched_at"))))
    links = [
        '<a href="%s/account/%s">%s</a>'
        % (_e(config.EXPLORER_URL.rstrip("/")), _e(address), L["open"])
    ]
    if holding.get("pool"):
        links.append(
            '<a href="%s/account/%s">%s</a>'
            % (
                _e(config.EXPLORER_URL.rstrip("/")),
                _e(holding["pool"]),
                L["pool"],
            )
        )
    lines.append(" · ".join(links))
    lines.append("<i>%s</i>" % L["disclaimer"])
    return "\n".join(lines)


def lp_holder_alert(row, holding, lang=None):
    lang = lang or config.ALERT_LANG
    if lang == "both":
        return (
            _lp_holder_block(row, holding, "en")
            + "\n\n———\n\n"
            + _lp_holder_block(row, holding, "id")
        )
    return _lp_holder_block(row, holding, lang if lang in ("id", "en") else "id")


def _report_block(wallets, pools, stats, lang):
    L = i18n.labels(lang)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%d %b %Y")
    lines = ["📊 <b>%s · %s</b>" % (L["report_title"], today)]
    lines.append("")
    lines.append("<b>%s</b>" % L["report_top"])
    if not wallets:
        lines.append(L["no_wallets"])
    for idx, w in enumerate(wallets[:10], 1):
        lines.append(
            "%d. %s %s · <code>%s</code> · %d LP / %d %s · edge %s"
            % (
                idx,
                TIER_EMOJI.get(w.get("tier") or "D", "⚪"),
                "%.1f" % float(w.get("smart_score") or 0),
                _e(filters.short_addr(w.get("address"))),
                int(w.get("lp_adds") or 0),
                int(w.get("lp_pools") or 0),
                L["lp_pools"],
                _pct(w.get("lp_edge") or 0),
            )
        )
    lines.append("")
    lines.append("<b>%s</b>" % L["report_pools"])
    if not pools:
        lines.append("—")
    for p in pools[:5]:
        lines.append(
            "• %s · %s · V/TVL %.2fx · %.1f%%/%s · %s %.0f"
            % (
                _e(p.get("name") or "?"),
                _e((p.get("dex") or "").upper() or "DEX"),
                float(p.get("vol_h24") or 0) / max(1.0, float(p.get("tvl") or 0)),
                float(p.get("fee_yield_daily") or 0) * 100,
                "day" if lang == "en" else "hari",
                L["score"],
                float(p.get("pool_score") or 0),
            )
        )
    lines.append("")
    lines.append(
        "<b>%s</b>: %d %s · %d %s · %d %s · %d %s · %d %s"
        % (
            L["report_stats"],
            stats.get("pools", 0), "pools" if lang == "en" else "pool",
            stats.get("winning_pools", 0), "winning" if lang == "en" else "menang",
            stats.get("wallets_with_lp", 0), L["wallets"],
            stats.get("lp_events", 0), L["events"],
            stats.get("alerts_sent", 0), L["alerts"],
        )
    )
    lines.append("<i>%s</i>" % L["disclaimer"])
    return "\n".join(lines)


def daily_report(wallets, pools, stats, lang=None):
    lang = lang or config.ALERT_LANG
    if lang == "both":
        return (
            _report_block(wallets, pools, stats, "en")
            + "\n\n———\n\n"
            + _report_block(wallets, pools, stats, "id")
        )
    return _report_block(wallets, pools, stats, lang if lang in ("id", "en") else "id")


def test_message(lang=None):
    lang = lang or config.ALERT_LANG
    L = i18n.labels(lang if lang in ("id", "en") else "id")
    return "✅ <b>%s</b>\n<i>%s</i>" % (_e(L["test"]), L["disclaimer"])
