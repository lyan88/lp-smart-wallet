#!/usr/bin/env python3
"""
Tracker: segarkan harga pool, update fee-vs-IL, hitung ulang skor wallet.

Dijalankan lebih jarang daripada scanner (mis. tiap 15 menit). Tidak
mengambil trade baru; tugasnya menjaga angka tetap segar dan menampilkan
status ranking.
"""

import argparse
import datetime
import sys

import config
import db
import filters
import net
import sources


def refresh_pools(conn, limit=50, verbose=False):
    """Ambil ulang metrik pool yang masih relevan, maks 30 per panggilan."""
    rows = conn.execute(
        "SELECT address FROM pools WHERE winning=1 "
        "OR address IN (SELECT DISTINCT pool FROM lp_events WHERE block_ts >= ?) "
        "OR address IN (SELECT DISTINCT pool FROM lp_holdings WHERE fetched_at >= ?) "
        "ORDER BY winning DESC, pool_score DESC LIMIT ?",
        (
            (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=config.LP_EVENT_LOOKBACK_DAYS)).isoformat(),
            (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=config.LP_CACHE_HOURS)).isoformat(),
            int(limit),
        ),
    ).fetchall()
    addresses = [r["address"] for r in rows if r["address"]]
    updated = 0
    for i in range(0, len(addresses), 30):
        chunk = addresses[i:i + 30]
        try:
            details = sources.pool_details(chunk)
        except net.SourceError as exc:
            print("[track] pool details gagal: %s" % exc, flush=True)
            break
        pools = [filters.apply_pool_metrics(p) for p in details.values()]
        if pools:
            db.upsert_pools(conn, pools)
            updated += len(pools)
        if verbose:
            print("[track] %d pool diperbarui" % len(pools), flush=True)
    return updated


def refresh(conn, verbose=False):
    updated = refresh_pools(conn, verbose=verbose)
    db.refresh_wallet_tokens(conn)
    changed = db.update_lp_edges(conn)
    scored = db.refresh_wallet_scores(conn)
    db.prune(conn)
    if verbose:
        print(
            "[track] %d pool · %d lp edge · %d wallet diskor"
            % (updated, changed, scored),
            flush=True,
        )
    return updated, changed, scored


def status(conn, top=15):
    summary = db.summary(conn)
    print("=== Top LP Smart Wallet Finder ===")
    print("Database : %s" % config.DB_PATH)
    print(
        "Isi      : %(pools)d pool (%(winning_pools)d menang) · %(trades)d trade · "
        "%(wallets)d wallet · %(wallets_with_lp)d punya bukti LP · "
        "%(lp_events)d event LP · %(lp_holdings)d holding · "
        "%(alerts_sent)d alert" % summary
    )
    print()
    print("Wallet teratas:")
    rows = db.top_wallets(conn, limit=top)
    if not rows:
        print("  (belum ada; jalankan scanner beberapa kali)")
    for i, w in enumerate(rows, 1):
        print(
            "  %2d. %s %5.1f  %s  LP %d/%d pool · edge %+.2f%% · size %s"
            % (
                i, w.get("tier"), float(w.get("smart_score") or 0),
                filters.short_addr(w.get("address")),
                int(w.get("lp_adds") or 0), int(w.get("lp_pools") or 0),
                float(w.get("lp_edge") or 0) * 100,
                filters.short_money(w.get("lp_size_usd") or 0),
            )
        )
    print()
    print("Event LP terakhir:")
    rows = conn.execute(
        "SELECT e.*, p.name AS pool_name FROM lp_events e "
        "LEFT JOIN pools p ON p.address=e.pool "
        "ORDER BY e.block_ts DESC LIMIT 10"
    ).fetchall()
    if not rows:
        print("  (belum ada)")
    for e in rows:
        print(
            "  %-6s %s  %-22s %-8s %s  edge %+.2f%%"
            % (
                e["kind"], filters.short_addr(e["wallet"]),
                (e["pool_name"] or filters.short_addr(e["pool"] or "?"))[:22],
                e["confidence"], filters.short_money(e["size_usd"]),
                float(e["net_edge"] or 0) * 100,
            )
        )
    print()
    print("Pemegang LP terbesar:")
    rows = db.top_lp_holders(conn, limit=10)
    if not rows:
        print("  (belum ada; pool Raydium CPMM/v4 dipindai otomatis)")
    for h in rows:
        print(
            "  %-22s %s  %6.2f%%  %-8s rank #%d"
            % (
                (h.get("pool_name") or "?")[:22],
                filters.short_addr(h.get("wallet")),
                float(h.get("pct") or 0) * 100,
                filters.short_money(h.get("value_usd")),
                int(h.get("rank") or 0),
            )
        )
    print()
    print("Pool menang:")
    rows = db.winning_pools(conn, limit=10)
    if not rows:
        print("  (belum ada)")
    for p in rows:
        print(
            "  %-24s %-14s V/TVL %.2fx · fee %.2f%%/hari · skor %.0f"
            % (
                (p.get("name") or "?")[:24], p.get("dex") or "?",
                float(p.get("vol_h24") or 0) / max(1.0, float(p.get("tvl") or 0)),
                float(p.get("fee_yield_daily") or 0) * 100,
                float(p.get("pool_score") or 0),
            )
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tracker harga + skor LP wallet")
    parser.add_argument("--status", action="store_true", help="tampilkan ranking")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    conn = db.open_db()
    try:
        if args.status:
            status(conn)
            return 0
        refresh(conn, verbose=args.verbose)
        status(conn, top=10)
        return 0
    except net.SourceError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
