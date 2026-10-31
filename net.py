#!/usr/bin/env python3
"""
HTTP + JSON-RPC kecil dengan rate limit per host, retry 429/5xx, failover
RPC, dan anggaran panggilan per proses.

Tanpa dependensi luar; hanya urllib. Dipakai untuk GeckoTerminal (GET) dan
RPC Solana (POST JSON-RPC).
"""

import contextlib
import fcntl
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config


class SourceError(RuntimeError):
    pass


class BudgetExceeded(SourceError):
    pass


class HostLimiter:
    """Jeda minimum antar panggilan ke satu host, adaptif saat kena 429."""

    def __init__(self, name, min_interval):
        self.name = name
        self.base = float(min_interval)
        self.interval = float(min_interval)
        self.last = 0.0
        self.state_path = config.DATA_DIR / ("rate_%s.json" % name)
        self.lock_path = config.DATA_DIR / ("rate_%s.lock" % name)
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.interval = max(self.base, float(raw.get("interval", self.base)))
            self.last = float(raw.get("last", 0.0))
        except (OSError, ValueError, TypeError):
            pass

    def _save(self):
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"interval": round(self.interval, 3), "last": self.last}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def wait(self):
        gap = self.interval - (time.time() - self.last)
        if gap > 0:
            time.sleep(min(gap, 60.0))
        self.last = time.time()
        self._save()

    @contextlib.contextmanager
    def guard(self):
        """Kunci antar-proses supaya scanner/tracker tidak menembak bersamaan."""
        handle = None
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            handle = open(self.lock_path, "a+")
            fcntl.flock(handle, fcntl.LOCK_EX)
        except OSError:
            handle = None
        try:
            self._load()
            self.wait()
            yield
        finally:
            if handle is not None:
                try:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                finally:
                    handle.close()

    def penalize(self, retry_after=None):
        if retry_after and retry_after > 0:
            self.interval = max(self.interval * 1.5, min(retry_after, 60.0))
        else:
            self.interval = min(max(self.interval * 2, 10.0), 60.0)
        self._save()

    def reward(self):
        if self.interval > self.base:
            self.interval = max(self.base, self.interval * 0.92)
            self._save()


_LIMITERS = {}


def limiter(name, min_interval):
    if name not in _LIMITERS:
        _LIMITERS[name] = HostLimiter(name, min_interval)
    return _LIMITERS[name]


def host_of(url):
    try:
        return urllib.parse.urlparse(url).netloc.replace(":", "_")
    except ValueError:
        return "host"


class Budget:
    def __init__(self, limit):
        self.limit = int(limit)
        self.used = 0

    def spend(self, n=1):
        if self.limit and self.used + n > self.limit:
            raise BudgetExceeded(
                "anggaran RPC habis (%d/%d)" % (self.used, self.limit)
            )
        self.used += n

    def remaining(self):
        if not self.limit:
            return 10 ** 9
        return max(0, self.limit - self.used)


_BUDGET = Budget(config.RPC_BUDGET_PER_RUN)


def budget():
    return _BUDGET


def reset_budget(limit=None):
    global _BUDGET
    _BUDGET = Budget(config.RPC_BUDGET_PER_RUN if limit is None else limit)
    return _BUDGET


def _request(url, payload=None, lim=None, timeout=None, retries=None):
    timeout = config.HTTP_TIMEOUT if timeout is None else timeout
    retries = config.HTTP_RETRIES if retries is None else retries
    headers = {
        "Accept": "application/json",
        "User-Agent": config.USER_AGENT,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    last_error = None
    for attempt in range(retries + 1):
        fatal = False
        guard = lim.guard() if lim is not None else contextlib.nullcontext()
        try:
            with guard:
                req = urllib.request.Request(url, data=data, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        body = resp.read()
                        if lim is not None:
                            lim.reward()
                        return json.loads(body.decode("utf-8", "replace"))
                except urllib.error.HTTPError as exc:
                    last_error = exc
                    if lim is not None and exc.code == 429:
                        retry_after = None
                        try:
                            retry_after = float(exc.headers.get("Retry-After", ""))
                        except (TypeError, ValueError):
                            pass
                        lim.penalize(retry_after)
                    fatal = exc.code in (400, 401, 403, 404)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if fatal:
            raise SourceError("%s: HTTP %s" % (url, getattr(last_error, "code", "?")))
        if attempt >= retries:
            break
        time.sleep(min(2 ** attempt, 10))
    raise SourceError("%s: %s" % (url, last_error))


def get_json(url, lim=None, timeout=None, retries=None):
    return _request(url, None, lim=lim, timeout=timeout, retries=retries)


def post_json(url, payload, lim=None, timeout=None, retries=None):
    return _request(url, payload, lim=lim, timeout=timeout, retries=retries)


def rpc_call(method, params, timeout=None, retries=None, budgeted=True):
    """Panggil JSON-RPC dengan failover antar endpoint dan anggaran proses."""
    if budgeted:
        _BUDGET.spend(1)
    timeout = config.RPC_TIMEOUT if timeout is None else timeout
    retries = config.RPC_RETRIES if retries is None else retries
    endpoints = [config.RPC_URL] + [u for u in config.RPC_FALLBACKS if u != config.RPC_URL]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    errors = []
    for url in endpoints:
        lim = limiter("rpc_" + host_of(url), config.RPC_MIN_INTERVAL)
        try:
            body = _request(
                url,
                payload,
                lim=lim,
                timeout=timeout,
                retries=min(retries, 1),
            )
        except SourceError as exc:
            errors.append(str(exc))
            continue
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            code = err.get("code") if isinstance(err, dict) else None
            # Endpoint tertentu menolak transaksi versi baru; bukan alasan
            # memindah host, biarkan pemanggil menyesuaikan parameter.
            raise SourceError("RPC %s: %s (%s)" % (method, msg, code))
        if isinstance(body, dict) and "result" in body:
            return body["result"]
        errors.append("RPC %s tanpa result" % method)
    raise SourceError("; ".join(errors) or "RPC gagal")


def quote(value):
    return urllib.parse.quote(str(value), safe="")


class TtlCache:
    """Cache kecil di memori, cukup untuk satu proses cron."""

    def __init__(self):
        self._data = {}

    def get(self, key, max_age):
        item = self._data.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > max_age:
            return None
        return value

    def put(self, key, value):
        self._data[key] = (time.time(), value)
        if len(self._data) > 500:
            cutoff = time.time() - 3600
            self._data = {k: v for k, v in self._data.items() if v[0] >= cutoff}
