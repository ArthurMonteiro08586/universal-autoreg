"""bank.py — единый банк аккаунтов (JSONL).

Каждый провайдер пишет сюда: {"provider","email","password","token","extra",...,"ts"}
Дедуп по (provider, email). Статистика по провайдерам.
"""
import json, os, time, threading

BANK_FILE = os.environ.get("UA_BANK", os.path.join(os.path.dirname(__file__), "..", "data", "bank.jsonl"))
_lock = threading.Lock()


def _path():
    p = os.path.abspath(BANK_FILE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def add(record: dict) -> bool:
    """Добавить аккаунт. False если дубль (provider,email)."""
    rec = dict(record)
    rec.setdefault("ts", int(time.time()))
    with _lock:
        seen = {(r.get("provider"), r.get("email")) for r in load()}
        key = (rec.get("provider"), rec.get("email"))
        if key in seen:
            return False
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True


def load():
    p = _path()
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def stats():
    per = {}
    for r in load():
        p = r.get("provider", "?")
        per.setdefault(p, {"total": 0, "with_token": 0})
        per[p]["total"] += 1
        if r.get("token") or r.get("api_key"):
            per[p]["with_token"] += 1
    return per


if __name__ == "__main__":
    import sys
    s = stats()
    total = sum(v["total"] for v in s.values())
    print(f"BANK {total} аккаунтов, {len(s)} провайдеров")
    for p, v in sorted(s.items(), key=lambda x: -x[1]["total"]):
        print(f"  {p}: {v['total']} (с токеном: {v['with_token']})")
