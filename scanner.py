#!/usr/bin/env python3
"""
Top LP Smart Wallet Finder — scanner utama.

Satu putaran:
  1. ambil pool trending Solana dari GeckoTerminal;
  2. tandai pool "menang" dan simpan metriknya;
  3. tarik trade terbaru untuk menemukan wallet aktif;
  4. deteksi aksi LP on-chain lewat RPC (add/remove liquidity);
  5. hitung skor LP + trading, lalu kirim kandidat ke Telegram.

Jalankan lewat cron tiap 5 menit. Tidak memegang private key.
"""

import argparse
import datetime
import sys

import base58
import config
import db
import filters
import net
import render
import scoring
import sources
import telegram
from solana_lp import classify_tx


def log(message, verbose=True):
    if verbose:
        print(message, flush=True)


def mark_pool(pool):
    return filters.apply_pool_metrics(pool)


def step_pools(conn, verbose):
    pools = sources.trending_pools()
    if not pools:
        raise net.SourceError("tidak ada pool dari GeckoTerminal")
    for pool in pools:
        mark_pool(pool)
    db.upsert_pools(conn, pools)
    winning = [p for p in pools if p["winning"]]
    log(
        "[pool] %d trending · %d menang · top: %s"
        % (
            len(pools), len(winning),
            ", ".join(p["name"] for p in sorted(pools, key=lambda x: x["pool_score"], reverse=True)[:3]),
        ),
        verbose,
    )
    if verbose:
        for p in pools[:5]:
            log(
                "  %-22s %-16s vol=%s tvl=%s v/tvl=%.2f score=%.0f %s"
                % (
                    p["name"][:22], p["dex"], filters.short_money(p["vol_h24"]),
                    filters.short_money(p["tvl"]), scoring.v_tvl(p), p["pool_score"],
                    "WIN" if p["winning"] else ("reject: " + p["reject"]),
                )
            )
    return pools, winning


def step_trades(conn, pools, verbose):
    ranked = sorted(pools, key=lambda p: (p["winning"], p["pool_score"]), reverse=True)
    selected = ranked[: config.TRADE_POOL_LIMIT]
    total_new = 0
    for pool in selected:
        try:
            trades = sources.pool_trades(pool["address"])
        except net.SourceError as exc:
            log("[trade] %s gagal: %s" % (pool["name"], exc), verbose)
            continue
        trades = [t for t in trades if not filters.wallet_is_ignored(t["wallet"])]
        new = db.insert_trades(conn, trades)
        total_new += new
        log("[trade] %-20s %d trade (%d baru)" % (pool["name"][:20], len(trades), new), verbose)
    return total_new


def _attach(event, pool_map):
    pool = pool_map.get(event.get("pool") or "")
    if pool:
        event["pool_name"] = pool.get("name") or ""
        event["pool_score"] = float(pool.get("pool_score") or 0)
        event["fee_yield_daily"] = float(pool.get("fee_yield_daily") or 0)
        event["chg_h24"] = float(pool.get("chg_h24") or 0)
    return event


def _scan_transactions(conn, signatures, pool_map, only_signers=None):
    events = []
    processed = []
    for sig in signatures:
        if not sig:
            continue
        if net.budget().remaining() < 1:
            break
        try:
            tx = sources.rpc_transaction(sig)
        except net.BudgetExceeded:
            break
        except net.SourceError as exc:
            print("[rpc] %s: %s" % (sig[:12], exc), flush=True)
            processed.append(sig)
            continue
        processed.append(sig)
        if not tx:
            continue
        for event in classify_tx(tx, pool_map, only_signers=only_signers):
            event["sig"] = event.get("sig") or sig
            events.append(_attach(event, pool_map))
    return events, processed


def step_lp_pools(conn, winning, pool_map, verbose):
    """Pindai transaksi pool bergiliran untuk menemukan wallet LP baru."""
    if not winning:
        return []
    ordered = sorted(winning, key=lambda p: p["pool_score"], reverse=True)
    limit = min(config.LP_POOL_SCAN_LIMIT, len(ordered))
    start = int(db.state_get(conn, "lp_pool_idx", 0) or 0) % len(ordered)
    selected = [ordered[(start + i) % len(ordered)] for i in range(limit)]
    db.state_set(conn, "lp_pool_idx", (start + limit) % len(ordered))

    found = []
    for pool in selected:
        if net.budget().remaining() < 3:
            log("[lp] anggaran RPC habis sebelum pool %s" % pool["name"], verbose)
            break
        try:
            sigs = sources.rpc_signatures(
                pool["address"], limit=config.LP_POOL_TX_SCAN * 2
            )
        except net.SourceError as exc:
            log("[lp] signatures %s gagal: %s" % (pool["name"], exc), verbose)
            continue
        names = [s.get("signature") for s in sigs if s.get("signature")]
        fresh = db.filter_unseen_sigs(conn, names)[: config.LP_POOL_TX_SCAN]
        if not fresh:
            log("[lp] %-20s tidak ada tx baru" % pool["name"][:20], verbose)
            continue
        events, processed = _scan_transactions(conn, fresh, pool_map)
        db.mark_sigs_seen(conn, processed)
        found.extend(events)
        log(
            "[lp] %-20s %d tx · %d event"
            % (pool["name"][:20], len(processed), len(events)),
            verbose,
        )
    return found


def step_lp_wallets(conn, pool_map, verbose):
    """Periksa transaksi terbaru wallet kandidat untuk bukti LP."""
    candidates = db.wallets_for_enrichment(conn, config.LP_WALLET_ENRICH_LIMIT)
    found = []
    for wallet in candidates:
        if net.budget().remaining() < 2:
            break
        address = wallet["address"]
        try:
            sigs = sources.rpc_signatures(address, limit=config.LP_WALLET_SIG_LIMIT)
        except net.SourceError as exc:
            log("[lp] wallet %s gagal: %s" % (filters.short_addr(address), exc), verbose)
            continue
        names = [s.get("signature") for s in sigs if s.get("signature")]
        fresh = db.filter_unseen_sigs(conn, names)[: config.LP_WALLET_TX_SCAN]
        before = len(found)
        if fresh:
            events, processed = _scan_transactions(
                conn, fresh, pool_map, only_signers=[address]
            )
            db.mark_sigs_seen(conn, processed)
            found.extend(events)
        db.mark_lp_checked(conn, address)
        log(
            "[lp] wallet %s · %d tx · %d event"
            % (filters.short_addr(address), len(fresh), len(found) - before),
            verbose,
        )
    return found


def step_lp_holders(conn, winning, verbose):
    """
    Enumerasi pemegang LP token langsung dari SPL mint pool.

    Hanya pool Raydium CPMM / AMM v4 yang punya LP mint fungible. DEX dengan
    posisi NFT (Orca, Raydium CLMM, Meteora DLMM) otomatis dilewati.
    """
    if not winning:
        return []
    ordered = sorted(winning, key=lambda p: p["pool_score"], reverse=True)
    limit = min(config.LP_HOLDER_SCAN_LIMIT, len(ordered))
    start = int(db.state_get(conn, "lp_holder_idx", 0) or 0) % len(ordered)
    cache_cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=config.LP_HOLDER_CACHE_MIN)
    ).replace(microsecond=0).isoformat()

    scanned = 0
    new_holders = []
    for i in range(len(ordered)):
        if scanned >= limit:
            break
        pool = ordered[(start + i) % len(ordered)]
        address = pool["address"]
        last = db.state_get(conn, "lp_holder_at:%s" % address)
        if last and str(last) >= cache_cutoff:
            continue
        if net.budget().remaining() < 4:
            log("[hold] anggaran RPC habis sebelum pool %s" % pool["name"], verbose)
            break
        try:
            data = sources.fetch_lp_holders(
                address, top=config.LP_HOLDER_TOP
            )
        except net.BudgetExceeded:
            break
        except net.SourceError as exc:
            log("[hold] %s gagal: %s" % (pool["name"], exc), verbose)
            scanned += 1
            db.state_set(conn, "lp_holder_at:%s" % address, db.utc_now())
            continue
        scanned += 1
        db.state_set(conn, "lp_holder_at:%s" % address, db.utc_now())
        if not data:
            log(
                "[hold] %-20s dilewati (DEX tanpa LP mint fungible)"
                % pool["name"][:20], verbose,
            )
            continue
        if not data.get("holders"):
            log(
                "[hold] %-20s supply LP 0 / tanpa holder"
                % pool["name"][:20], verbose,
            )
            continue
        result = db.upsert_lp_holders(
            conn, address, data.get("lp_mint"), data.get("supply"),
            data.get("holders"), dex=data.get("dex") or pool.get("dex"),
            tvl=pool.get("tvl") or 0,
        )
        price = float(pool.get("price_usd") or 0)
        chg24 = float(pool.get("chg_h24") or 0)
        entry = price / (1.0 + chg24 / 100.0) if price > 0 and chg24 > -95 else price
        edge = scoring.lp_net_edge(entry, price, pool, 1.0)
        for item in result["new"]:
            item["pool_name"] = pool.get("name") or ""
            item["pool_dex"] = pool.get("dex") or ""
            item["pool_score"] = float(pool.get("pool_score") or 0)
            item["fee_yield_daily"] = float(pool.get("fee_yield_daily") or 0)
            item["edge"] = edge
            new_holders.append(item)
        log(
            "[hold] %-20s %d holder · %d baru · mint %s"
            % (
                pool["name"][:20], result["stored"], len(result["new"]),
                (data.get("lp_mint") or "")[:8],
            ),
            verbose,
        )
    db.state_set(conn, "lp_holder_idx", (start + max(1, scanned)) % len(ordered))
    conn.commit()
    return new_holders


def _recent(iso_text, hours):
    dt = sources.parse_ts(iso_text)
    if not dt:
        return False
    age = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
    return 0 <= age <= hours * 3600


def step_alerts(conn, new_events, verbose, new_holders=None):
    queued = set()
    pool_map = db.pool_map(conn)
    event_count = 0

    if config.ALERT_LP_EVENTS:
        seen = set()
        for event in sorted(
            new_events, key=lambda e: float(e.get("size_usd") or 0), reverse=True
        ):
            pool = pool_map.get(event.get("pool") or "")
            if event.get("kind") != "add" or not pool:
                continue
            if not pool.get("winning"):
                continue
            if event.get("confidence") not in ("high", "medium"):
                continue
            if float(event.get("size_usd") or 0) < config.MIN_LP_SIZE_USD:
                continue
            if not _recent(event.get("block_ts"), config.LP_ALERT_MAX_AGE_HOURS):
                continue
            key = (event.get("sig"), event.get("wallet"))
            if key in seen:
                continue
            seen.add(key)
            wallet = db.get_wallet(conn, event.get("wallet"))
            if not wallet or wallet.get("ignored"):
                continue
            if (
                wallet.get("last_alert_at")
                and filters.age_days(wallet.get("last_alert_at"))
                * 24.0
                < config.LP_EVENT_COOLDOWN_HOURS
            ):
                continue
            stored = db.get_lp_event(
                conn, event.get("sig"), event.get("wallet"), event.get("kind")
            )
            if stored:
                # Ambil net_edge/fee_yield yang sudah dihitung database.
                event = stored
            dedupe = "lp:%s:%s" % (event.get("sig"), event.get("wallet"))
            text = render.lp_event_alert(wallet, event)
            score = max(float(wallet.get("smart_score") or 0), float(pool.get("pool_score") or 0))
            if db.queue_alert(
                conn, event.get("wallet"), "lp_add", score, text, dedupe
            ):
                queued.add(event.get("wallet"))
                event_count += 1
                log(
                    "[alert] LP %s di %s (%s)"
                    % (filters.short_addr(event.get("wallet")), pool.get("name"),
                       filters.short_money(event.get("size_usd"))),
                    verbose,
                )
            if event_count >= config.ALERT_MAX_PER_RUN:
                break

    # Pemegang LP token terbesar (bukti langsung, bukan tebakan transaksi).
    holder_count = 0
    remaining = max(0, config.ALERT_MAX_PER_RUN - event_count)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    holder_items = (
        sorted(
            new_holders or [],
            key=lambda h: float(h.get("value_usd") or 0),
            reverse=True,
        )
        if config.ALERT_LP_HOLDERS
        else []
    )
    for item in holder_items:
        if holder_count >= remaining:
            break
        pool = pool_map.get(item.get("pool") or "")
        if not pool or not pool.get("winning"):
            continue
        pct = float(item.get("pct") or 0)
        value = float(item.get("value_usd") or 0)
        if (
            pct < config.LP_HOLDER_ALERT_MIN_PCT
            and value < config.LP_HOLDER_ALERT_MIN_USD
        ):
            continue
        wallet = db.get_wallet(conn, item.get("wallet"))
        if not wallet or wallet.get("ignored"):
            continue
        if (
            wallet.get("last_alert_at")
            and filters.age_days(wallet.get("last_alert_at"))
            * 24.0
            < config.LP_HOLDER_COOLDOWN_HOURS
        ):
            continue
        dedupe = "hold:%s:%s:%s" % (
            item.get("pool"), item.get("wallet"), today,
        )
        text = render.lp_holder_alert(wallet, item)
        score = max(
            float(wallet.get("smart_score") or 0),
            float(pool.get("pool_score") or 0),
        )
        if db.queue_alert(
            conn, item.get("wallet"), "lp_hold", score, text, dedupe
        ):
            queued.add(item.get("wallet"))
            holder_count += 1
            log(
                "[alert] TOP LP %s di %s (%.2f%% share)"
                % (
                    filters.short_addr(item.get("wallet")), pool.get("name"),
                    pct * 100,
                ),
                verbose,
            )

    wallet_count = 0
    for row in db.top_wallets(conn, limit=50, eligible_only=True):
        if wallet_count >= config.ALERT_MAX_PER_RUN:
            break
        if row["address"] in queued:
            continue
        if not filters.alert_ready(row):
            continue
        events = db.wallet_lp_events(conn, row["address"], limit=5)
        text = render.wallet_alert(row, events)
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        dedupe = "wallet:%s:%s" % (row["address"], today)
        if db.queue_alert(
            conn, row["address"], "wallet", row["smart_score"], text, dedupe
        ):
            wallet_count += 1
            log(
                "[alert] wallet %s skor %.1f"
                % (filters.short_addr(row["address"]), row["smart_score"]),
                verbose,
            )
    return event_count + holder_count + wallet_count


def drain_alerts(conn, dry_run, verbose):
    pending = db.pending_alerts(conn, limit=12)
    if not pending:
        return 0
    hour, day = db.alert_counts(conn)
    allowed = min(
        config.TELEGRAM_MAX_PER_RUN,
        max(0, config.TELEGRAM_MAX_PER_HOUR - hour),
        max(0, config.TELEGRAM_MAX_PER_DAY - day),
        len(pending),
    )
    sent = 0
    for alert in pending[:allowed]:
        ok = telegram.send_message(alert["text"], dry_run=dry_run)
        if ok:
            db.mark_alert_sent(conn, alert["id"])
            sent += 1
        else:
            db.mark_alert_failed(conn, alert["id"], "send gagal")
    if dry_run and sent:
        log("[alert] %d pesan ditandai terkirim (dry-run)" % sent, verbose)
    return sent


def heartbeat(pools, winning, trades_new, lp_events, sent, elapsed, dry_run):
    flag = " [dry-run]" if dry_run else ""
    print(
        "LSW | %s | %d pools · %d menang | %d trade baru | %d event LP | "
        "%d alert | rpc %d/%d | %.1fs%s"
        % (
            datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M"),
            len(pools), len(winning), trades_new, lp_events, sent,
            net.budget().used, net.budget().limit, elapsed, flag,
        ),
        flush=True,
    )


def run_once(args):
    started = datetime.datetime.now()
    net.reset_budget()
    conn = db.open_db()
    try:
        if args.wallet:
            return profile_wallet(conn, args)

        pools, winning = step_pools(conn, args.verbose)
        trades_new = step_trades(conn, pools, args.verbose)

        db.refresh_wallet_tokens(conn)
        db.ensure_trade_wallets(conn)
        for address in config.SEED_WALLETS:
            db.ensure_wallet(conn, address)
            conn.execute(
                "UPDATE wallets SET note='seed' WHERE address=? AND note IS NULL",
                (address,),
            )
        conn.commit()

        lp_events = []
        new_holders = []
        if args.no_rpc:
            log("[lp] dilewati (--no-rpc): tidak ada bukti LP baru", args.verbose)
        else:
            pool_map = db.pool_map(conn)
            lp_events += step_lp_pools(conn, winning, pool_map, args.verbose)
            lp_events += step_lp_wallets(conn, pool_map, args.verbose)
            new_holders = step_lp_holders(conn, winning, args.verbose)

        if lp_events:
            inserted = db.insert_lp_events(conn, lp_events)
            log(
                "[lp] %d event terdeteksi · %d baru disimpan"
                % (len(lp_events), inserted),
                args.verbose,
            )
        if new_holders:
            log("[hold] %d pemegang LP baru" % len(new_holders), args.verbose)
        db.update_lp_edges(conn)
        db.refresh_wallet_scores(conn)
        db.prune(conn)

        queued = step_alerts(conn, lp_events, args.verbose, new_holders)
        sent = drain_alerts(conn, args.dry_run, args.verbose)
        heartbeat(
            pools, winning, trades_new, len(lp_events), sent,
            (datetime.datetime.now() - started).total_seconds(), args.dry_run,
        )
        if args.verbose:
            summary = db.summary(conn)
            log("[db] %s" % summary)
    finally:
        conn.close()


def profile_wallet(conn, args):
    address = args.wallet.strip()
    if not base58.is_valid(address, length=32):
        print("Alamat wallet tidak valid: %s" % address)
        return 2
    db.ensure_wallet(conn, address)
    conn.commit()
    if not args.no_rpc:
        pool_map = db.pool_map(conn)
        try:
            sigs = sources.rpc_signatures(address, limit=config.LP_WALLET_SIG_LIMIT)
            names = [s.get("signature") for s in sigs if s.get("signature")]
            fresh = db.filter_unseen_sigs(conn, names)[: config.LP_WALLET_TX_SCAN]
            events, processed = _scan_transactions(
                conn, fresh, pool_map, only_signers=[address]
            )
            db.mark_sigs_seen(conn, processed)
            db.insert_lp_events(conn, events)
            db.mark_lp_checked(conn, address)
        except net.SourceError as exc:
            print("[rpc] %s" % exc)
    db.update_lp_edges(conn)
    db.refresh_wallet_scores(conn)
    row = db.get_wallet(conn, address)
    print(render.wallet_alert(row, db.wallet_lp_events(conn, address, limit=10)))
    print("\nToken trading:")
    for t in db.wallet_tokens(conn, address, limit=10):
        print(
            "  %s · buy %s sell %s · avg %s -> %s · peak %s"
            % (
                (t.get("token") or "")[:12],
                filters.short_money(t.get("buy_usd")),
                filters.short_money(t.get("sell_usd")),
                t.get("avg_buy"), t.get("avg_sell"), t.get("peak_price"),
            )
        )
    print("\nEvent LP:")
    for e in db.wallet_lp_events(conn, address, limit=10):
        print(
            "  %s %s %s · %s · edge %s · %s"
            % (
                e.get("kind"), e.get("pool_name") or filters.short_addr(e.get("pool")),
                e.get("confidence"), filters.short_money(e.get("size_usd")),
                "%.2f%%" % (float(e.get("net_edge") or 0) * 100),
                e.get("block_ts"),
            )
        )
    print("\nLP holdings:")
    for h in db.wallet_lp_holdings(conn, address, limit=10):
        print(
            "  %s · %.2f%% share · ~%s · rank %d · %s"
            % (
                h.get("pool_name") or filters.short_addr(h.get("pool")),
                float(h.get("pct") or 0) * 100,
                filters.short_money(h.get("value_usd")),
                int(h.get("rank") or 0), h.get("fetched_at"),
            )
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Top LP Smart Wallet Finder (Solana, read-only)"
    )
    parser.add_argument("--once", action="store_true", help="jalankan satu putaran")
    parser.add_argument("--dry-run", action="store_true", help="jangan kirim Telegram")
    parser.add_argument("--verbose", action="store_true", help="log rinci")
    parser.add_argument("--no-rpc", action="store_true", help="lewati pemindaian LP")
    parser.add_argument("--wallet", help="profil satu wallet lalu keluar")
    parser.add_argument("--test-telegram", action="store_true", help="kirim pesan uji")
    parser.add_argument("--summary", action="store_true", help="tampilkan isi database")
    args = parser.parse_args(argv)

    if args.test_telegram:
        ok = telegram.test(dry_run=args.dry_run)
        print("Telegram OK" if ok else "Telegram gagal")
        return 0 if ok else 1
    if args.summary:
        conn = db.open_db()
        try:
            print(db.summary(conn))
        finally:
            conn.close()
        return 0

    args.dry_run = args.dry_run or config.DRY_RUN
    try:
        return run_once(args) or 0
    except net.SourceError as exc:
        print("ERROR sumber data: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
