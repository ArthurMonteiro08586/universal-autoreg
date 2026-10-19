#!/usr/bin/env python3
"""UNIVERSAL AUTOREG DASHBOARD v2 — единый пульт, Windows + Linux.

Stdlib-only сервер (http.server), порт 8900. Запуск: python dashboard.py
UI: http://127.0.0.1:8900  (вкладки: Провайдеры / Прокси / Почта / Банк / Настройки / Зависимости)

Возможности:
- 17 провайдеров авторега из providers.json: RUN/STOP волны, live-лог
- Прокси: бесплатный забор из 7 публичных источников, live-проверка, пул
- Почта: t-online IMAP-пул, voidash temp-inbox, gmail app-password
- Банк аккаунтов: поиск, статистика, CSV/JSONL экспорт (секреты маскируются в UI)
- Настройки: config.json из UI (значения-секреты маскируются при отдаче)
- Dependency doctor: чего не хватает каждому провайдеру
"""
import csv
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PORT = int(os.environ.get("UA_PORT", "8900"))
HOST = os.environ.get("UA_HOST", "127.0.0.1")
PYTHON = os.environ.get("UA_PYTHON", sys.executable)
LOGS = os.path.join(HERE, "logs")
DATA = os.environ.get("UA_HOME") or os.path.join(HERE, "data")
os.makedirs(LOGS, exist_ok=True)
os.makedirs(DATA, exist_ok=True)

from lib import bank, deps, proxy_grab  # noqa: E402
from lib import captcha, proxies  # noqa: E402

RUNNING = {}  # provider -> {"proc": Popen, "started": ts, "count": N}
RUN_LOCK = threading.Lock()
HISTORY = []  # {"provider","count","started","ended","exit"}
HIST_FILE = os.path.join(LOGS, "history.json")
PRUNE = {"running": False, "tested": 0, "alive": 0, "total": 0}
SENSITIVE = ("password", "passwd", "token", "key", "secret", "totp", "seed",
             "recovery", "session", "app_pw", "apppw", "refresh")


def _load_history():
    global HISTORY
    if os.path.exists(HIST_FILE):
        try:
            HISTORY = json.load(open(HIST_FILE, encoding="utf-8"))[-200:]
        except Exception:
            HISTORY = []


def _save_history():
    try:
        tmp = HIST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(HISTORY[-200:], f)
        os.replace(tmp, HIST_FILE)
    except Exception:
        pass


def _watcher():
    """Фоновый тред: фиксирует завершение волн в историю."""
    while True:
        with RUN_LOCK:
            for prov, r in list(RUNNING.items()):
                code = r["proc"].poll()
                if code is not None:
                    HISTORY.append({"provider": prov, "count": r["count"],
                                    "started": r["started"], "ended": time.time(),
                                    "exit": code})
                    _save_history()
                    del RUNNING[prov]
        time.sleep(2)


def _prune_worker():
    """Live-проверка всего пула, перезапись proxies.txt живыми."""
    from concurrent.futures import ThreadPoolExecutor
    PRUNE.update(running=True, tested=0, alive=0)
    try:
        pool = proxies.load_pool()
        PRUNE["total"] = len(pool)
        good = []
        with ThreadPoolExecutor(max_workers=60) as ex:
            for url, ok in ex.map(lambda u: proxies.check_one(u), pool):
                PRUNE["tested"] += 1
                if ok:
                    good.append(url)
                    PRUNE["alive"] = len(good)
        with open(proxies.TXT, "w", encoding="utf-8") as f:
            for g in good:
                f.write(g + "\n")
    except Exception as e:
        PRUNE["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        PRUNE["running"] = False


def load_manifest():
    with open(os.path.join(HERE, "providers.json"), encoding="utf-8") as f:
        return json.load(f)


def mask_rec(rec):
    out = {}
    for k, v in rec.items():
        if isinstance(v, str) and any(s in k.lower() for s in SENSITIVE) and v:
            out[k] = v[:2] + "***" + v[-2:] if len(v) > 8 else "***"
        else:
            out[k] = v
    return out


def infra_health():
    h = {"sidecar": None, "proxies": {"total": 0}, "emails": {"pool": 0}, "twocaptcha": None}
    try:
        h["sidecar"] = captcha.sidecar_health()
    except Exception as e:
        h["sidecar"] = {"ok": False, "error": str(e)[:100]}
    try:
        h["proxies"]["total"] = len(proxies.load_pool())
    except Exception as e:
        h["proxies"]["error"] = str(e)[:100]
    mails = os.path.join(DATA, "working_mails.txt")
    if os.path.exists(mails):
        with open(mails, encoding="utf-8", errors="ignore") as f:
            h["emails"]["pool"] = sum(1 for _ in f)
    try:
        h["twocaptcha"] = captcha.balance_2captcha()
    except Exception:
        pass
    return h


def run_wave(provider, count):
    with RUN_LOCK:
        r = RUNNING.get(provider)
        if r and r["proc"].poll() is None:
            return {"error": "already running", "pid": r["proc"].pid}
        script = os.path.join(HERE, "providers", provider + ".py")
        if not os.path.exists(script):
            return {"error": "no such provider script: %s" % script}
        logf = open(os.path.join(LOGS, provider + ".log"), "a", encoding="utf-8")
        logf.write("\n===== wave %s started %s =====\n" % (count, time.strftime("%F %T")))
        logf.flush()
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONPATH", "")
        kw = {}
        if os.name != "nt":
            kw["start_new_session"] = True
        proc = subprocess.Popen([PYTHON, script, str(count)], cwd=HERE,
                                stdout=logf, stderr=subprocess.STDOUT, env=env, **kw)
        RUNNING[provider] = {"proc": proc, "started": time.time(), "count": count}
        return {"ok": True, "pid": proc.pid}


def stop_wave(provider):
    with RUN_LOCK:
        r = RUNNING.get(provider)
        if r and r["proc"].poll() is None:
            pid = r["proc"].pid
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True)
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except Exception:
                    r["proc"].terminate()
            return {"ok": True, "terminated": pid}
    return {"ok": False}


def config_load():
    p = os.path.join(HERE, "config.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def config_masked():
    def walk(o):
        if isinstance(o, dict):
            return {k: ("***" if isinstance(v, str) and v and any(s in k.lower() for s in SENSITIVE) else walk(v))
                    for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o
    return walk(config_load())


def config_update(patch):
    cfg = config_load()

    def merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            elif v != "***" and v != "":
                dst[k] = v
    merge(cfg, patch)
    tmp = os.path.join(HERE, "config.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(HERE, "config.json"))
    return config_masked()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            p = os.path.join(HERE, "web", "index.html")
            if os.path.exists(p):
                return self._send(200, open(p, "rb").read(), "text/html")
            return self._send(200, "<h1>web/index.html missing</h1>", "text/html")
        if u.path == "/api/manifest":
            m = load_manifest()
            with RUN_LOCK:
                for p in m["providers"]:
                    r = RUNNING.get(p["id"])
                    p["running"] = bool(r and r["proc"].poll() is None)
                    p["pid"] = r["proc"].pid if p["running"] else None
            return self._send(200, m)
        if u.path == "/api/health":
            return self._send(200, infra_health())
        if u.path == "/api/bank":
            return self._send(200, bank.stats())
        if u.path == "/api/bank/list":
            prov = q.get("provider", [""])[0]
            needle = q.get("q", [""])[0].lower()
            limit = min(int(q.get("limit", ["200"])[0]), 1000)
            offset = int(q.get("offset", ["0"])[0])
            rows = bank.load()
            if prov:
                rows = [r for r in rows if r.get("provider") == prov]
            if needle:
                rows = [r for r in rows if needle in json.dumps(r, ensure_ascii=False).lower()]
            total = len(rows)
            rows = [mask_rec(r) for r in rows[offset:offset + limit]]
            return self._send(200, {"total": total, "offset": offset, "rows": rows})
        if u.path == "/api/bank/export":
            fmt = q.get("fmt", ["jsonl"])[0]
            rows = bank.load()
            if fmt == "csv" and rows:
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=sorted({k for r in rows for k in r}))
                w.writeheader()
                for r in rows:
                    w.writerow(r)
                data = buf.getvalue().encode("utf-8")
                ctype = "text/csv"
                fn = "bank.csv"
            else:
                data = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)).encode("utf-8")
                ctype = "application/x-ndjson"
                fn = "bank.jsonl"
            return self._send(200, data, ctype,
                              {"Content-Disposition": 'attachment; filename="%s"' % fn})
        if u.path == "/api/proxies":
            pool = proxies.load_pool()
            return self._send(200, {
                "total": len(pool),
                "sample": pool[:30],
                "txt_file": os.path.abspath(proxies.TXT),
                "sources": [s[0] for s in proxy_grab.SOURCES],
            })
        if u.path == "/api/emails":
            from lib import emails
            mails = os.path.join(DATA, "working_mails.txt")
            total = 0
            if os.path.exists(mails):
                with open(mails, encoding="utf-8", errors="ignore") as f:
                    total = sum(1 for _ in f)
            used = 0
            sf = os.path.join(DATA, "email_state.json")
            if os.path.exists(sf):
                try:
                    used = len(json.load(open(sf, encoding="utf-8")).get("used", []))
                except Exception:
                    pass
            cfg = config_load()
            return self._send(200, {
                "tonline": {"total": total, "used": used, "free": max(total - used, 0),
                            "configured": bool(cfg.get("tonline_imap", {}).get("password"))},
                "gmail": {"configured": bool(cfg.get("gmail_imap", {}).get("password")),
                          "user": cfg.get("gmail_imap", {}).get("user", "")},
                "voidash_domains": ["voidash.bond", "govno.eu.cc", "musor.eu.cc", "pomoi.eu.cc"],
            })
        if u.path == "/api/config":
            return self._send(200, config_masked())
        if u.path == "/api/deps":
            return self._send(200, deps.summary())
        if u.path == "/api/history":
            with RUN_LOCK:
                running = [{"provider": p, "pid": r["proc"].pid, "count": r["count"],
                            "elapsed": round(time.time() - r["started"])}
                           for p, r in RUNNING.items() if r["proc"].poll() is None]
            return self._send(200, {"history": HISTORY[-100:][::-1], "running": running,
                                    "prune": PRUNE})
        if u.path == "/api/proxies/prune_status":
            return self._send(200, PRUNE)
        if u.path == "/api/log":
            prov = q.get("provider", [""])[0]
            off = int(q.get("offset", ["0"])[0])
            path = os.path.join(LOGS, prov + ".log")
            if not os.path.exists(path):
                return self._send(200, {"text": "", "offset": off})
            with open(path, encoding="utf-8", errors="replace") as f:
                f.seek(off)
                text = f.read(64 * 1024)
                newoff = f.tell()
            return self._send(200, {"text": text, "offset": newoff})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        if u.path == "/api/run":
            return self._send(200, run_wave(b.get("provider", ""), int(b.get("count", 1))))
        if u.path == "/api/stop":
            return self._send(200, stop_wave(b.get("provider", "")))
        if u.path == "/api/proxies/grab":
            try:
                grabbed = proxy_grab.grab(timeout=int(b.get("timeout", 20)))
                per_src = {k: (len(v) if isinstance(v, list) else v) for k, v in grabbed.items()}
                added, total = proxy_grab.merge_to_pool(grabbed)
                return self._send(200, {"ok": True, "per_source": per_src, "added": added, "pool_total": total})
            except Exception as e:
                return self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        if u.path == "/api/proxies/check":
            try:
                alive, tested = proxy_grab.check_pool(limit=int(b.get("limit", 150)))
                return self._send(200, {"ok": True, "alive": len(alive), "tested": tested,
                                        "sample": alive[:20]})
            except Exception as e:
                return self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        if u.path == "/api/emails/voidash":
            try:
                from lib import emails
                addr, key = emails.voidash_create(b.get("domain", "voidash.bond"))
                return self._send(200, {"ok": True, "address": addr, "session_key_tail": key[-6:]})
            except Exception as e:
                return self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        if u.path == "/api/proxies/add":
            try:
                raw = b.get("text", "")
                added = 0
                existing = set(proxies.load_pool())
                with open(proxies.TXT, "a", encoding="utf-8") as f:
                    for line in raw.replace(",", "\n").replace(";", "\n").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        norm = line.split("//")[-1].split("@")[-1] if "//" not in line else line
                        if line in existing or (norm in existing and "//" not in line):
                            continue
                        existing.add(line)
                        f.write(line + "\n")
                        added += 1
                return self._send(200, {"ok": True, "added": added, "total": len(proxies.load_pool())})
            except Exception as e:
                return self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        if u.path == "/api/proxies/prune":
            if PRUNE.get("running"):
                return self._send(200, {"ok": False, "error": "prune уже идёт"})
            threading.Thread(target=_prune_worker, daemon=True).start()
            return self._send(200, {"ok": True, "started": True})
        if u.path == "/api/config":
            try:
                return self._send(200, {"ok": True, "config": config_update(b)})
            except Exception as e:
                return self._send(200, {"ok": False, "error": str(e)[:200]})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    _load_history()
    threading.Thread(target=_watcher, daemon=True).start()
    print("UNIVERSAL AUTOREG DASHBOARD v2: http://%s:%d  (python %s, %s)" % (
        HOST, PORT, sys.version.split()[0], os.name))
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
