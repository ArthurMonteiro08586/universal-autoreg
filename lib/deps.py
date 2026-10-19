# -*- coding: utf-8 -*-
"""deps.py — dependency doctor: что нужно каждому провайдеру и что уже есть.

Для каждого провайдера из providers.json проверяет:
- python-модули (importlib.util.find_spec — без реального импорта)
- внешние бинарники (playwright chromium, chrome CDP, camoufox)
- сервисы (captcha sidecar)
- файлы данных (пулы в data/)

Возвращает список {provider, ok, missing:[...]} для вкладки DEPS дашборда.
"""
import importlib.util
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# provider_id -> (python modules, data files required, notes)
REQS = {
    "beatapi_oauth":      (["curl_cffi"], ["gh_accounts.json"], "GitHub OAuth через curl_cffi (chrome131 impersonation)"),
    "cleanapis_oauth":    (["requests"], ["gh_accounts.json"], "Laravel OAuth flow"),
    "newapi_panels_oauth": (["requests"], ["gh_accounts.json"], "15 панелей new-api; GH 429 = пэйсинг"),
    "treg_github_grant":  (["requests", "cloakbrowser"], [], "cloakbrowser: pip install cloakbrowser && cloakbrowser fetch"),
    "notion_farm":        (["playwright", "requests"], [], "playwright install chromium; DE-прокси для signup"),
    "conol_register":     (["requests"], [], "free captcha sidecar :9228 (chrome CDP)"),
    "llmtr_turnstile":    (["requests"], ["working_mails.txt"], "turnstile sidecar :8877"),
    "miarouter_farm":     (["requests"], [], "turnstile sidecar :8877"),
    "xkiro_tg_verify":    (["curl_cffi", "telethon"], ["gh_accounts.json", "config.json"], "TG api_id/api_hash в config.json"),
    "aws_builderid":      (["websocket"], ["working_mails.txt"], "websocket-client; CDP chrome :9341"),
    "gname_slider":       (["playwright"], [], "GM_LOCAL/GM_APPPW env или data/gname_creds.py"),
    "omnirush_recaptcha": (["requests"], [], "2captcha ключ в config.json"),
    "type_workos":        (["playwright", "requests"], [], "cloakbrowser желателен; SMS-мост 2no"),
    "vani_turnstile":     (["cloakbrowser"], [], "native turnstile, sidecar-инъекция не принимается"),
    "terrapolis_native":  (["cloakbrowser"], [], "3 акка/IP/30дн — ротация прокси обязательна"),
    "gh_device_flow":     (["pyotp", "requests"], ["gh_accounts.json"], "device approve; акки с verified email"),
    "tokenharbor_claim":  (["websocket", "requests"], ["working_mails.txt"], "CDP chrome :9222 --remote-allow-origins=*"),
}


def _mod_ok(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _bin_ok(name):
    return shutil.which(name) is not None


def check_all():
    out = []
    for pid, (mods, files, notes) in REQS.items():
        missing = []
        for m in mods:
            if not _mod_ok(m):
                missing.append(f"pip install {m}")
        for f in files:
            p = os.path.join(ROOT, "data", f)
            if not os.path.exists(p):
                missing.append(f"data/{f}")
        # playwright chromium binary
        if "playwright" in mods and _mod_ok("playwright"):
            roots = []
            if os.name == "nt":
                roots.append(os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
            elif sys.platform == "darwin":
                roots.append(os.path.expanduser("~/Library/Caches/ms-playwright"))
            else:
                roots.append(os.path.expanduser(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")))
            if not any(os.path.isdir(r) and os.listdir(r) for r in roots):
                missing.append("playwright install chromium")
        if "cloakbrowser" in mods and _mod_ok("cloakbrowser"):
            cb = shutil.which("cloakbrowser")
            if not cb:
                missing.append("cloakbrowser fetch (bin)")
        out.append({
            "provider": pid,
            "ok": not missing,
            "missing": missing,
            "notes": notes,
        })
    return out


def summary():
    rows = check_all()
    return {
        "total": len(rows),
        "ready": sum(1 for r in rows if r["ok"]),
        "rows": rows,
        "python": sys.version.split()[0],
        "platform": os.name,
        "chrome_cdp": _bin_ok("google-chrome") or _bin_ok("chrome") or bool(
            os.path.exists(r"C:\Program Files\Google\Chrome\Application\chrome.exe") if os.name == "nt" else False),
    }


if __name__ == "__main__":
    s = summary()
    print(json.dumps(s, ensure_ascii=False, indent=1))
