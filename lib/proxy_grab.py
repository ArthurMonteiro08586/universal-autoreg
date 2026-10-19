# -*- coding: utf-8 -*-
"""proxy_grab.py — бесплатный забор прокси из публичных источников → пул.

Источники (без регистрации, без ключей):
- api.proxyscrape.com (http/socks4/socks5)
- raw-листы TheSpeedX (github)
- proxylist.geonode.com API (http)
- openproxylist.xyz

Результат мержится в data/proxies.txt (формат host:port или http://host:port),
дедуп против существующего пула. Проверка живости — lib.proxies.live_pool.
"""
import json
import os
import re
import urllib.request

from . import proxies as P

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

SOURCES = [
    ("proxyscrape-http", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=8000&country=all&ssl=all&anonymity=all", "text"),
    ("proxyscrape-socks5", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=8000&country=all", "text"),
    ("proxyscrape-socks4", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=8000&country=all", "text"),
    ("thespeedx-http", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt", "text"),
    ("thespeedx-socks5", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt", "text"),
    ("geonode-http", "https://proxylist.geonode.com/api/proxy-list?limit=200&page=1&sort_by=lastChecked&sort_type=desc&protocols=http%2Chttps", "json"),
    ("openproxylist", "https://api.openproxylist.xyz/http.txt", "text"),
]

IP_PORT = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})\b")


def _fetch(url, kind, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "ignore")
    out = []
    if kind == "json":
        try:
            data = json.loads(raw)
            for it in data.get("data", []):
                ip, port = it.get("ip"), it.get("port")
                if ip and port:
                    out.append(f"{ip}:{port}")
        except Exception:
            pass
    else:
        out = IP_PORT.findall(raw)
    # нормализация: без схемы, только валидные порты
    norm = []
    for x in out:
        host, _, port = x.rpartition(":")
        if host and port.isdigit() and 1 <= int(port) <= 65535:
            norm.append(f"{host}:{port}")
    return norm


def grab(timeout=20):
    """Забор со всех источников. Возвращает {source: [proxies]}."""
    result = {}
    for name, url, kind in SOURCES:
        try:
            result[name] = _fetch(url, kind, timeout)
        except Exception as e:
            result[name] = f"ERROR {type(e).__name__}: {str(e)[:80]}"
    return result


def merge_to_pool(grabbed, max_total=3000):
    """Слить свежие в data/proxies.txt с дедупом. Возвращает (added, total)."""
    existing = set()
    if os.path.exists(P.TXT):
        for line in open(P.TXT, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                existing.add(line.split("//")[-1].split("@")[-1])
    fresh = []
    seen = set()
    for src, lst in grabbed.items():
        if isinstance(lst, str):
            continue
        for p in lst:
            if p not in existing and p not in seen:
                seen.add(p)
                fresh.append(p)
    with open(P.TXT, "a", encoding="utf-8") as f:
        for p in fresh[:max_total]:
            f.write(p + "\n")
    return len(fresh[:max_total]), len(existing) + len(fresh[:max_total])


def check_pool(limit=200, workers=40):
    """Live-проверка пула (выборка). Возвращает (alive_list, tested)."""
    pool = P.load_pool()
    if not pool:
        return [], 0
    import random
    sample = random.sample(pool, min(limit, len(pool)))
    from concurrent.futures import ThreadPoolExecutor
    good = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for url, ok in ex.map(lambda u: P.check_one(u), sample):
            if ok:
                good.append(url)
    return good, len(sample)


if __name__ == "__main__":
    g = grab()
    for k, v in g.items():
        print(k, len(v) if isinstance(v, list) else v)
    added, total = merge_to_pool(g)
    print(f"added {added}, pool total ~{total}")
