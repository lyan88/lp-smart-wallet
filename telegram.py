#!/usr/bin/env python3
"""Transport Telegram minimal (HTML), dengan pemecahan pesan dan retry."""

import time

import config
import net

_API = "https://api.telegram.org/bot%s/%s"
_lim = net.limiter("telegram", config.TELEGRAM_MIN_INTERVAL_SEC)


def enabled():
    return bool(
        config.TELEGRAM_ENABLED
        and config.TELEGRAM_BOT_TOKEN
        and config.TELEGRAM_CHAT_ID
    )


def _chunks(text, limit=3900):
    text = str(text or "")
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _call(method, payload, retries=3):
    if not config.TELEGRAM_BOT_TOKEN:
        raise net.SourceError("TELEGRAM_BOT_TOKEN belum diisi")
    url = _API % (config.TELEGRAM_BOT_TOKEN, method)
    last = None
    for attempt in range(retries + 1):
        try:
            data = net.post_json(url, payload, lim=_lim, retries=1)
            if isinstance(data, dict) and data.get("ok"):
                return data.get("result")
            last = (data or {}).get("description") if isinstance(data, dict) else data
        except net.SourceError as exc:
            last = exc
        time.sleep(min(2 ** attempt, 8))
    raise net.SourceError("Telegram %s gagal: %s" % (method, last))


def send_message(text, chat_id=None, dry_run=None, silent=False):
    dry_run = config.DRY_RUN if dry_run is None else dry_run
    if dry_run:
        if not silent:
            print("[telegram:dry-run]\n%s\n" % text)
        return True
    if not enabled():
        if not silent:
            print("[telegram] nonaktif: isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID")
        return False
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    ok = True
    for chunk in _chunks(text):
        try:
            _call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except net.SourceError as exc:
            print("[telegram] %s" % exc)
            ok = False
    return ok


def get_me():
    return _call("getMe", {})


def test(chat_id=None, dry_run=None):
    import render

    return send_message(
        render.test_message(), chat_id=chat_id, dry_run=dry_run
    )
