#!/usr/bin/env python3
"""Laporan harian Top LP Smart Wallet Finder."""

import argparse
import sys

import config
import db
import net
import render
import telegram
import track


def build(conn, top=10, lang=None):
    wallets = db.top_wallets(conn, limit=top)
    pools = db.winning_pools(conn, limit=10)
    stats = db.summary(conn)
    return render.daily_report(wallets, pools, stats, lang=lang)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Laporan harian wallet LP")
    parser.add_argument("--telegram", action="store_true", help="kirim ke Telegram")
    parser.add_argument("--dry-run", action="store_true", help="cetak saja")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--refresh", action="store_true", help="segarkan harga dulu")
    parser.add_argument("--lang", choices=("id", "en", "both"))
    args = parser.parse_args(argv)

    conn = db.open_db()
    try:
        if args.refresh:
            try:
                track.refresh(conn)
            except net.SourceError as exc:
                print("[report] refresh gagal: %s" % exc, file=sys.stderr)
        text = build(conn, top=args.top, lang=args.lang)
        if args.telegram:
            ok = telegram.send_message(
                text, dry_run=(args.dry_run or config.DRY_RUN)
            )
            return 0 if ok else 1
        print(text)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
