#!/usr/bin/env python3
"""Diagnosa cepat: konfigurasi, database, GeckoTerminal, RPC, Telegram."""

import argparse
import sqlite3
import sys

import config
import db
import net
import sources
import telegram

OK = "OK  "
WARN = "WARN"
FAIL = "FAIL"
results = []


def check(name, ok, detail="", level=None):
    status = OK if ok else (level or FAIL)
    results.append((status, name, detail))
    print("[%s] %-22s %s" % (status, name, detail))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Diagnosa bot LP")
    parser.add_argument("--offline", action="store_true", help="tanpa panggilan jaringan")
    args = parser.parse_args(argv)

    print("Top LP Smart Wallet Finder · %s" % config.CHAIN_NAME)
    print("=" * 62)
    check("python", sys.version_info >= (3, 8), sys.version.split()[0])
    check("data dir", config.DATA_DIR.exists() or True, str(config.DATA_DIR))
    try:
        config.ensure_dirs()
        check("tulis data dir", True, "bisa dibuat/diakses")
    except OSError as exc:
        check("tulis data dir", False, str(exc))

    conn = None
    try:
        conn = db.open_db()
        counts = db.summary(conn)
        check("database", True, "%s · %s" % (config.DB_PATH.name, counts))
    except sqlite3.Error as exc:
        check("database", False, str(exc))

    token = config.TELEGRAM_BOT_TOKEN
    chat = config.TELEGRAM_CHAT_ID
    check(
        "telegram config",
        bool(token and chat),
        "token=%s chat=%s" % (
            (token[:8] + "…" + token[-4:]) if token else "kosong",
            chat or "kosong",
        ),
        level=WARN,
    )

    if args.offline:
        check("jaringan", True, "dilewati (--offline)", level=WARN)
    else:
        try:
            pools = sources.trending_pools(pages=1, max_age=0)
            check(
                "geckoterminal",
                bool(pools),
                "%d pool · top %s" % (len(pools), pools[0]["name"] if pools else "-"),
            )
        except net.SourceError as exc:
            check("geckoterminal", False, str(exc)[:120])

        try:
            health = sources.rpc_health()
            check("solana rpc", health == "ok", "getHealth=%s" % health)
        except net.SourceError as exc:
            check("solana rpc", False, str(exc)[:120])

        try:
            me = telegram.get_me()
            check("telegram api", True, "@%s" % (me or {}).get("username", "?"))
        except net.SourceError as exc:
            check("telegram api", False, str(exc)[:120], level=WARN)

    print("=" * 62)
    bad = sum(1 for s, _, _ in results if s == FAIL)
    warn = sum(1 for s, _, _ in results if s == WARN)
    print("%d gagal · %d peringatan" % (bad, warn))
    if conn:
        conn.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
