"""Doc config.json (fallback ve config.example.json va bien moi truong)."""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DG_CONFIG doi cho dat config.json. Can cho container: ma nguon nam o /app do
# root so huu, tien trinh chay bang tai khoan thuong -> save() khong tao noi file
# .tmp trong /app, luu token tu trang Cai dat se hong. Tro sang volume du lieu
# thi vua ghi duoc, vua khong mat token moi lan dung lai image.
CONFIG_PATH = os.environ.get("DG_CONFIG") or os.path.join(ROOT, "config.json")
EXAMPLE_PATH = os.path.join(ROOT, "config.example.json")
DATA_DIR = os.path.join(ROOT, "data")

DEFAULTS = {
    "bkns_api_key": "",
    "bkns_rpm": 2,
    "rdap_rpm": 0,
    "timeout": 25,
    "workers": 6,
    "cache_ttl_hours": 12,
    "warn_days": 30,
    "critical_days": 7,
    "host": "127.0.0.1",
    "port": 8787,
    "notify": {"telegram_bot_token": "", "telegram_chat_id": ""},
    "cloudflare_api_token": "",
}


def load(path: str = None) -> dict:
    cfg = dict(DEFAULTS)
    for candidate in ([path] if path else [CONFIG_PATH, EXAMPLE_PATH]):
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                cfg.update({k: v for k, v in raw.items() if not k.startswith("_")})
            except (ValueError, OSError):
                pass
            break

    # Bien moi truong ghi de config file - tien cho CI/docker
    env_map = {
        "DG_BKNS_KEY": ("bkns_api_key", str),
        "DG_WARN_DAYS": ("warn_days", int),
        "DG_CRITICAL_DAYS": ("critical_days", int),
        "DG_PORT": ("port", int),
        "DG_HOST": ("host", str),
        "DG_CF_TOKEN": ("cloudflare_api_token", str),
    }
    for env, (key, cast) in env_map.items():
        if os.environ.get(env):
            try:
                cfg[key] = cast(os.environ[env])
            except ValueError:
                pass

    notify = dict(DEFAULTS["notify"])
    notify.update({k: v for k, v in (cfg.get("notify") or {}).items() if not k.startswith("_")})
    if os.environ.get("DG_TELEGRAM_TOKEN"):
        notify["telegram_bot_token"] = os.environ["DG_TELEGRAM_TOKEN"]
    if os.environ.get("DG_TELEGRAM_CHAT"):
        notify["telegram_chat_id"] = os.environ["DG_TELEGRAM_CHAT"]
    cfg["notify"] = notify
    return cfg


def save(changes: dict, path: str = CONFIG_PATH) -> dict:
    """Ghi mot so khoa vao config.json, giu nguyen moi khoa khac (ke ca chu thich `_...`).

    `notify` duoc gop long nhau chu khong thay the ca cum.
    """
    raw = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (ValueError, OSError):
            raw = {}

    for key, value in changes.items():
        if key == "notify" and isinstance(value, dict):
            merged = dict(raw.get("notify") or {})
            merged.update(value)
            raw["notify"] = merged
        else:
            raw[key] = value

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    # File nay giu bot token Telegram. Umask mac dinh tren Linux/macOS thuong ra
    # 0644, tuc moi tai khoan khac tren may deu doc duoc. Siet TRUOC khi replace
    # de khong co khoang nao file nam do voi quyen rong. Tren Windows chmod chi
    # bat/tat co chi-doc nen la no-op, khong sao.
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)  # ghi nguyen tu, tranh hong file khi bi ngat giua chung
    return raw


def make_resolver(cfg: dict):
    from .resolver import Resolver
    return Resolver(
        bkns_api_key=cfg.get("bkns_api_key", ""),
        bkns_rpm=int(cfg.get("bkns_rpm", 2)),
        rdap_rpm=int(cfg.get("rdap_rpm", 0)),
        timeout=int(cfg.get("timeout", 25)),
        workers=int(cfg.get("workers", 6)),
    )
