#!/usr/bin/env python
"""Domain Gateway - web dashboard (Flask).

Chay:  python cli.py serve      hoac      python app.py
"""

from __future__ import annotations

import io
import csv
import json
import os
import secrets
import sys
import threading

from flask import Flask, Response, jsonify, render_template, request, url_for

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gateway import config as cfgmod
from gateway import exporters
from gateway.models import iso, lam_sach, lam_sach_giu_khoa, utcnow

# reportlab la phu thuoc TUY CHON (thieu no thi chi rieng xuat PDF bao loi),
# nen khong import thang o dau file. LayoutError chi dung trong except nen
# lop gia nay du: khi khong co reportlab thi khong bao gio co LayoutError.
try:
    from reportlab.platypus.doctemplate import LayoutError
except ImportError:                                  # pragma: no cover
    class LayoutError(Exception):
        pass
from gateway.notifier import (MultiNotifier, ZaloNotifier, bucket_of, build_message,
                              tu_cau_hinh)
from gateway.cloudflare import CloudflareClient, CloudflareError
from gateway.whmcs import WhmcsClient, WhmcsError, chuan_hoa_url
from gateway.resolver import normalize_domain
from gateway.store import Store

ROOT = os.path.dirname(os.path.abspath(__file__))
# assets/ chu KHONG phai data/: data/ la VOLUME trong Docker, ma danh ba nha
# dang ky la du lieu tham chieu di kem ma nguon. De trong volume thi ai
# bind-mount thu muc data se mat han file nay (tab Danh ba chet), va ai nang
# cap image cung khong bao gio nhan duoc ban moi.
REGISTRARS_PATH = os.path.join(ROOT, "assets", "registrars.json")


class RefreshJob:
    """Trang thai mot lan tra cuu nen. Chi cho phep 1 job chay tai mot thoi diem."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.done = 0
        self.total = 0
        self.current = ""
        self.finished_at = None
        self.ok = 0
        self.failed = []

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "done": self.done,
                "total": self.total,
                "current": self.current,
                "ok": self.ok,
                "failed": list(self.failed),
                "finished_at": self.finished_at,
            }


def _giau_phien_ban_werkzeug() -> None:
    """Bo phien ban Werkzeug/Python khoi header Server.

    Mac dinh no khai `Werkzeug/3.1.8 Python/3.14.6`. Thong tin do khong giup gi
    nguoi dung, chi giup nguoi khac tra dung CVE hop voi ban dang chay. Phai doi
    o lop request handler chu khong phai trong after_request: Werkzeug gan header
    nay o tang WSGI, sau khi Flask da xong, nen dat trong after_request chi sinh
    ra header thu hai.
    """
    try:
        from werkzeug.serving import WSGIRequestHandler
        # version_string() ghep hai truong bang mot dau cach, nen tach doi ra
        # cho khong bi thua khoang trang o cuoi header.
        WSGIRequestHandler.server_version = "Domain"
        WSGIRequestHandler.sys_version = "Gateway"
    except Exception:
        pass        # chay sau WSGI server khac thi header do server do quyet dinh


_giau_phien_ban_werkzeug()


def _van_ban(gia_tri: object, khi_vang=None):
    """Ep mot truong van ban ve chuoi. Kieu phuc hop coi nhu khong gui.

    Client gui gi cung duoc, nen truong van ban co the la dict hay list. Dua
    thang xuong sqlite la ProgrammingError -> 500; con str(dict) thi ghi nguyen
    chuoi "{'a': 1}" vao DB, im lang va ban hon. Ca hai deu sai, nen coi nhu
    khong gui truong do.

    So va bool duoc nhan vi nguoi dung go "2027" vao o van ban la chuyen thuong.
    """
    if gia_tri is None:
        return khi_vang
    if isinstance(gia_tri, str):
        return gia_tri
    if isinstance(gia_tri, (int, float)) and not isinstance(gia_tri, bool):
        return str(gia_tri)
    return khi_vang


def _than_json() -> dict:
    """Than request duoi dang dict, luon luon.

    `request.get_json(silent=True) or {}` chi do duoc `null` va than rong.
    Than la mang, so hay chuoi JSON - `[1,2]`, `5`, `"abc"` - deu la JSON HOP LE
    nen Flask tra ve list/int/str, roi `.get()` tren do la AttributeError -> 500
    cho mot loi cua nguoi gui. Ep ve dict rong: cac handler tu tra 400 vi thieu
    truong bat buoc.
    """
    than = request.get_json(silent=True)
    if not isinstance(than, dict):
        return {}
    try:
        # Don moi truong TRU khoa chinh - xem lam_sach_giu_khoa().
        return lam_sach_giu_khoa(than)
    except ValueError:
        # Long qua sau. Tra dict rong -> handler tu bao thieu truong bat buoc,
        # dung nhu truoc khi co lam_sach (400 chu khong phai 500).
        return {}


def _danh_ba_hop_le(sections: object) -> bool:
    """Kiem hinh dang payload danh ba truoc khi dan PDF.

    registry_pdf() goi sec.get(...), item.get(...) va " ".join(tlds) tren noi
    dung nguoi gui, nen mot phan tu sai kieu la TypeError -> 500. Loi cua nguoi
    gui phai tra 400.

    Phai kiem KIEU cua ca danh sach truoc khi lap qua no: `all(... for x in 5)`
    tu no da nem TypeError. Ban kiem dau tien o day dinh dung cai bay do.
    """
    if not isinstance(sections, list):
        return False
    for sec in sections:
        if not isinstance(sec, dict):
            return False
        tlds = sec.get("tlds") or []
        items = sec.get("items") or []
        if not isinstance(tlds, list) or not isinstance(items, list):
            return False
        if any(not isinstance(x, str) for x in tlds):
            return False
        if any(not isinstance(x, dict) for x in items):
            return False
    return True


def _che_token(token: str) -> str:
    """Che bot token, chi de lo phan von cong khai.

    Token Telegram co dang <bot_id>:<35 ky tu bi mat>; token Zalo Bot cung
    <so>:<bi mat>. Phan truoc dau hai cham lo ra khong sao - ai chat voi bot cung
    thay duoc. Phan sau thi che sach.
    """
    if not token:
        return ""
    bot_id, sep, _ = token.partition(":")
    return f"{bot_id}:{'*' * 8}" if sep else "đã lưu"


def create_app(cfg: dict = None, store: Store = None) -> Flask:
    cfg = cfg or cfgmod.load()
    store = store or Store()
    job = RefreshJob()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.json.ensure_ascii = False
    # Jinja giu ban template da bien dich trong bo nho suot vong doi tien trinh;
    # bat auto_reload de sua index.html khong phai khoi dong lai server.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    # Chan than request khong lo. Flask mac dinh khong gioi han, tuc mot POST
    # vai tram MB duoc doc thang vao bo nho truoc khi co ai kip tu choi.
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

    def thresholds():
        """Doc lai tu cfg moi lan goi - nguong co the doi khi luu cai dat tu web."""
        return int(cfg.get("warn_days", 30)), int(cfg.get("critical_days", 7))

    def notifier() -> MultiNotifier:
        # Moi kenh da cau hinh (Telegram, Zalo) nhan mot ban. Doc lai cfg moi lan
        # goi vi token co the vua doi tu trang Cai dat.
        return tu_cau_hinh(cfg.get("notify"))

    def serialize(rec):
        warn, crit = thresholds()
        return rec.to_dict(warn, crit)

    @app.context_processor
    def _asset_helper():
        """Gan mtime vao URL static de trinh duyet khong dung ban cu sau khi cap nhat."""
        def asset(filename):
            try:
                stamp = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
            except OSError:
                stamp = 0
            return url_for("static", filename=filename) + "?v=" + str(stamp)
        return {"asset": asset}

    # ---- chan request cheo trang (CSRF) -----------------------------------
    # App nghe o localhost nhung trinh duyet van gui request tu MOI trang web toi
    # localhost duoc. POST khong can than JSON (refresh, notify/send, notify/reset)
    # la "simple request", khong bi CORS preflight chan, nen bat ky trang nao nguoi
    # dung mo trong luc app chay cung kich hoat duoc. Ke tan cong khong doc duoc
    # phan hoi nhung tac dung phu van xay ra: xoa lich su chong trung canh bao, ep
    # gui Telegram, dot quota BKNS.
    #
    # Sec-Fetch-Site do CHINH trinh duyet dat, trang web khong ghi de duoc. Client
    # khong phai trinh duyet (cli.py, curl, script) khong gui header nay -> cho qua,
    # nen cong cu dong lenh van chay binh thuong.
    DOI_TRANG_THAI = {"POST", "PUT", "PATCH", "DELETE"}
    NGUON_TIN_CAY = {"same-origin", "same-site", "none"}

    @app.before_request
    def chan_cheo_trang():
        if request.method not in DOI_TRANG_THAI:
            return None
        nguon = request.headers.get("Sec-Fetch-Site")
        if nguon and nguon not in NGUON_TIN_CAY:
            return jsonify({
                "error": "Từ chối request chéo trang. Thao tác này chỉ gọi được từ "
                         "chính giao diện Domain Gateway."
            }), 403
        return None

    # ---- header bao mat ---------------------------------------------------
    # CSP cho API: JSON thuan, khong duoc phep tai gi het.
    CSP_API = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

    @app.after_request
    def them_header_bao_mat(resp):
        # setdefault: trang HTML tu dat CSP rieng (co nonce) o index(), dung de bi de.
        resp.headers.setdefault("Content-Security-Policy", CSP_API)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        # KHONG dat Server o day: Werkzeug gan header cua no o tang WSGI, sau
        # after_request, nen dat o day chi tao ra header thu hai va client doc
        # thanh "Werkzeug/3.1.8 Python/3.14.6, Domain Gateway". Phai doi o lop
        # request handler - xem _giau_phien_ban_werkzeug().
        return resp

    # ---- trang ------------------------------------------------------------
    @app.route("/")
    def index():
        warn, crit = thresholds()
        # Nonce cho doan script inline chay truoc khi ve trang (chong FOUC). Dung
        # nonce chu khong phai 'unsafe-inline': chi dung mot doan duy nhat cua
        # minh duoc chay, chu khong mo cua cho moi doan script inline.
        nonce = secrets.token_urlsafe(16)
        page = render_template("index.html", warn_days=warn, critical_days=crit,
                               csp_nonce=nonce)
        resp = Response(page, mimetype="text/html")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            # style-src phai co 'unsafe-inline': thanh "con lai" va vi tri menu
            # dat qua thuoc tinh style=, ma thuoc tinh style thi nonce khong voi toi.
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "          # favicon la data:image/svg+xml
            "connect-src 'self'; "
            "form-action 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; object-src 'none'"
        )
        # Trang nhung nguong canh bao vao the <body>; neu trinh duyet cache lai thi
        # sau khi doi nguong se hien so cu. Trang nay nhe nen khong can cache.
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- doc du lieu ------------------------------------------------------
    @app.get("/api/domains")
    def api_domains():
        records = [serialize(r) for r in store.all()]
        records.sort(key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 0))
        return jsonify(records)

    @app.get("/api/summary")
    def api_summary():
        warn, crit = thresholds()
        data = store.summary(warn, crit)
        data["warn_days"] = warn
        data["critical_days"] = crit
        data["notify_configured"] = notifier().configured
        data["last_notify"] = store.get_meta("last_notify")
        return jsonify(data)

    @app.get("/api/domains/<domain>/history")
    def api_history(domain):
        return jsonify(store.history(normalize_domain(domain)))

    @app.get("/api/lookup")
    def api_lookup():
        """Tra cuu mot lan, khong ghi vao kho - dung cho tab Tra cuu nhanh."""
        domain = normalize_domain(request.args.get("domain", ""))
        if not domain or "." not in domain:
            return jsonify({"error": "Tên miền không hợp lệ"}), 400
        rec = cfgmod.make_resolver(cfg).lookup(domain)
        return jsonify(serialize(rec))

    @app.get("/api/registrars")
    def api_registrars():
        try:
            with open(REGISTRARS_PATH, "r", encoding="utf-8") as fh:
                return jsonify(json.load(fh))
        except (OSError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 500

    # ---- ghi du lieu ------------------------------------------------------
    @app.post("/api/domains")
    def api_add():
        payload = _than_json()
        raw = payload.get("domain", "")
        added, skipped = [], []
        # Chan so luong moi lan them. Khong phai lo bi tan cong - CSRF da chan
        # duong do - ma vi dan nham ca file vao o nhap thi kho phinh ra hang van
        # dong, roi refresh chay het quota BKNS va treo hang gio.
        MAX_MOI_LAN = 500
        # Cho phep dan nhieu ten mien cung luc, cach nhau bang xuong dong/phay/space
        if not isinstance(raw, str):
            return jsonify({"error": "Tên miền phải là chuỗi"}), 400
        khoi = raw.replace(",", "\n").replace(" ", "\n").splitlines()
        if len(khoi) > MAX_MOI_LAN:
            return jsonify({
                "error": f"Quá {MAX_MOI_LAN} dòng trong một lần thêm. "
                         "Chia nhỏ ra, hoặc dùng `python cli.py import` cho danh sách dài."
            }), 400
        for chunk in khoi:
            domain = normalize_domain(chunk)
            if not domain or "." not in domain:
                # lam_sach o day chu khong o _than_json: `domain` co y giu tho
                # de normalize_domain con tu choi, nhung doi nguyen chuoi tho
                # nguoc ve client thi jsonify (ensure_ascii=False) nghen ngay
                # voi surrogate lac -> 500 SAU KHI mot phan danh sach da ghi
                # vao kho. Nguoi dung thay that bai trong khi du lieu da vao.
                if chunk.strip():
                    skipped.append(lam_sach(chunk).strip())
                continue
            store.add(
                domain,
                provider=_van_ban(payload.get("provider"), ""),
                tags=[t for t in (payload.get("tags") or []) if isinstance(t, str)],
                note=_van_ban(payload.get("note"), ""),
                manual_expires_at=payload.get("expires_at"),
                auto_renew=payload.get("auto_renew"),
                pinned=bool(payload.get("pinned")),
            )
            added.append(domain)
        if not added:
            return jsonify({"error": "Không có tên miền hợp lệ", "skipped": skipped}), 400
        if payload.get("lookup", True):
            _spawn_refresh(added)
        return jsonify({"added": added, "skipped": skipped}), 201

    @app.patch("/api/domains/<domain>")
    def api_update(domain):
        domain = normalize_domain(domain)
        if not store.get(domain):
            return jsonify({"error": "Không tìm thấy tên miền"}), 404
        payload = _than_json()
        store.update_user_fields(
            domain,
            provider=_van_ban(payload.get("provider")),
            # Giu list rong (= xoa het tag) nhung loai phan tu khong phai chuoi
            tags=([t for t in payload["tags"] if isinstance(t, str)]
                  if isinstance(payload.get("tags"), list) else None),
            note=_van_ban(payload.get("note")),
            # Co mat khoa "expires_at" voi gia tri null = XOA ngay nhap tay
            # (hop nhap moi dung "de trong de xoa"). Vang mat khoa = giu nguyen.
            manual_expires_at=(payload["expires_at"] if "expires_at" in payload
                               else "__keep__"),
            auto_renew=payload["auto_renew"] if "auto_renew" in payload else "__keep__",
            pinned=payload.get("pinned"),
        )
        return jsonify(serialize(store.get(domain)))

    @app.delete("/api/domains/<domain>")
    def api_delete(domain):
        chuan = normalize_domain(domain)
        # Khong co thi noi thang. Truoc day luon tra {"deleted": ...} ke ca khi
        # khong xoa gi - cung loai "man hinh noi doi": nguoi dung tuong da xoa.
        if not chuan or not store.get(chuan):
            return jsonify({"error": "Không tìm thấy tên miền"}), 404
        store.delete(chuan)
        return jsonify({"deleted": chuan})

    # ---- tra cuu nen ------------------------------------------------------
    def _worker(names):
        resolver = cfgmod.make_resolver(cfg)

        def progress(done, total, rec):
            with job.lock:
                job.done = done
                job.total = total
                job.current = rec.domain
                if rec.expires_at:
                    job.ok += 1
                else:
                    job.failed.append({"domain": rec.domain, "error": rec.error})

        da_luu = 0
        try:
            for rec in resolver.lookup_many(names, progress=progress):
                try:
                    store.save_lookup(rec)
                    da_luu += 1
                except Exception as exc:                     # noqa: BLE001
                    # Bao ve TUNG ban ghi. Truoc day mot loi o day thoat ra
                    # khoi vong for, nen moi ten mien phia sau khong bao gio
                    # duoc ghi - trong khi `finally` van dat finished_at va
                    # progress() da dem chung vao job.ok. Man hinh bao
                    # "xong 9/9, loi 0" trong khi kho chi co 3 ban ghi moi.
                    with job.lock:
                        if rec.expires_at:
                            # Tra cuu duoc nhung khong luu duoc: rut khoi ok,
                            # them vao failed.
                            job.ok = max(0, job.ok - 1)
                            job.failed.append({
                                "domain": rec.domain,
                                "error": "không lưu được: " + type(exc).__name__,
                            })
                        else:
                            # Tra cuu da hong san -> progress() DA them vao
                            # failed roi. Them lan nua la mot ten mien dem hai
                            # lan, toast in "6 chua doc duoc" cho mot job 4 ten
                            # mien. Chi ghi them ly do.
                            for m in job.failed:
                                if m.get("domain") == rec.domain:
                                    m["error"] = ((m.get("error") or "")
                                                  + " · không lưu được: "
                                                  + type(exc).__name__).strip(" ·")
                                    break
            # Chi danh dau da tra cuu khi THAT SU co ban ghi xuong dia. Truoc
            # day loi save_lookup thoat ra khoi vong nen dong nay bi bo qua;
            # gio loi bi bat, neu van dat moc thi giao dien in "Tra cuu vua
            # xong" trong khi khong co gi duoc luu.
            if da_luu:
                store.set_meta("last_refresh", iso(utcnow()))
        finally:
            with job.lock:
                job.running = False
                job.current = ""
                job.finished_at = iso(utcnow())

    def _spawn_refresh(names) -> bool:
        with job.lock:
            if job.running:
                return False
            job.running = True
            job.done = 0
            job.ok = 0
            job.failed = []
            job.total = len(names)
            job.current = ""
            job.finished_at = None
        threading.Thread(target=_worker, args=(names,), daemon=True).start()
        return True

    @app.post("/api/refresh")
    def api_refresh():
        payload = _than_json()
        xin = payload.get("domains")
        # Phai kiem KIEU chu khong chi truthy. Mot chuoi cung lap duoc:
        # {"domains": "abc.com"} tung tra 200 roi tra cuu that cho 'a','b','c',
        # 'c','o','m' - sau luot goi mang, dot quota BKNS, ma nguoi gui tuong
        # minh vua lam dung.
        if xin is None or xin == []:
            names = store.names()
        elif isinstance(xin, list):
            names = [n for n in xin if isinstance(n, str)]
            if not names:
                return jsonify({"error": "Danh sách tên miền phải là chuỗi"}), 400
        else:
            return jsonify({"error": "`domains` phải là danh sách tên miền"}), 400
        # Loc dau RA chu khong chi dau VAO: normalize_domain tra chuoi rong
        # khi ten mien co ky tu khong hop le, va `if n` khong bat duoc no. De
        # lot thi job khoi dong voi total=1, khong tra cuu gi, roi giao dien
        # toast "Da cap nhat 0 ten mien" - dung loai man hinh noi doi.
        xin_bao_nhieu = len(names)
        names = [d for d in (normalize_domain(n) for n in names) if d]
        if not names:
            return jsonify({
                "error": "Kho trống" if not xin_bao_nhieu else "Không có tên miền hợp lệ"
            }), 400

        if not payload.get("force"):
            ttl = int(cfg.get("cache_ttl_hours", 12)) * 3600
            stale = []
            for name in names:
                rec = store.get(name)
                if rec and rec.checked_at and not rec.error \
                        and (utcnow() - rec.checked_at).total_seconds() < ttl:
                    continue
                stale.append(name)
            names = stale
            if not names:
                return jsonify({"started": False, "reason": "cache", "total": 0})

        if not _spawn_refresh(names):
            return jsonify({"started": False, "reason": "busy"}), 409
        return jsonify({"started": True, "total": len(names)})

    @app.get("/api/refresh/status")
    def api_refresh_status():
        return jsonify(job.snapshot())

    # ---- cai dat & canh bao Telegram --------------------------------------
    @app.get("/api/settings")
    def api_settings_get():
        warn, crit = thresholds()
        notify = cfg.get("notify") or {}
        token = notify.get("telegram_bot_token", "")
        return jsonify({
            "warn_days": warn,
            "critical_days": crit,
            "cache_ttl_hours": int(cfg.get("cache_ttl_hours", 12)),
            # Token Telegram co dang <bot_id>:<35 ky tu bi mat>. Chi hien bot_id -
            # phan do von cong khai, du de nhan ra minh da luu bot nao. Cach cu giu
            # 4 ky tu cuoi la giu 4 ky tu cua chinh phan bi mat, khong duoc loi gi.
            "telegram_token_masked": _che_token(token),
            "telegram_token_set": bool(token),
            "telegram_chat_id": notify.get("telegram_chat_id", ""),
            "zalo_token_masked": _che_token(notify.get("zalo_bot_token", "")),
            "zalo_token_set": bool(notify.get("zalo_bot_token")),
            "zalo_chat_id": notify.get("zalo_chat_id", ""),
            # Moi the kenh co badge rieng; notify_configured la "it nhat mot kenh"
            "telegram_configured": notifier().kenh("telegram").configured,
            "zalo_configured": notifier().kenh("zalo").configured,
            "notify_configured": notifier().configured,
            # Token Cloudflare la chuoi doi khong co cau truc cong khai nao,
            # nen khong he lo ky tu nao ca - bai hoc tu cach che token Telegram.
            "cloudflare_token_set": bool(cfg.get("cloudflare_api_token")),
            "last_cf_sync": store.get_meta("last_cf_sync"),
            # identifier / secret / accesskey KHONG bao gio tra ve, ke ca che mot
            # phan: chung la chuoi ngau nhien, lo mot nua cung chang de nhan ra gi.
            "whmcs_url": cfg.get("whmcs_url", ""),
            "whmcs_configured": whmcs_client().configured,
            "whmcs_accesskey_set": bool(cfg.get("whmcs_accesskey")),
            "last_whmcs_sync": store.get_meta("last_whmcs_sync"),
            "last_notify": store.get_meta("last_notify"),
            "cron_command": (
                'schtasks /create /tn "DomainGateway" /tr '
                f'"cmd /c cd /d {ROOT} && python cli.py refresh && python cli.py notify" '
                '/sc daily /st 08:00'
            ),
        })

    @app.post("/api/settings")
    def api_settings_post():
        payload = _than_json()
        changes, notify_changes = {}, {}

        for key in ("warn_days", "critical_days", "cache_ttl_hours"):
            if key in payload and str(payload[key]).strip() != "":
                try:
                    changes[key] = max(0, int(payload[key]))
                except (TypeError, ValueError):
                    return jsonify({"error": f"Giá trị không hợp lệ cho {key}"}), 400

        # Token rong nghia la "giu nguyen cai dang co", khong phai "xoa di"
        token = (_van_ban(payload.get("telegram_bot_token"), "") or "").strip()
        if token:
            notify_changes["telegram_bot_token"] = token
        if "telegram_chat_id" in payload:
            # _van_ban chu khong phai str(): str({"a":1}) ghi nguyen chuoi
            # "{'a': 1}" vao config.json, im lang.
            notify_changes["telegram_chat_id"] = (
                _van_ban(payload["telegram_chat_id"], "") or "").strip()
        # Zalo: cung quy uoc voi Telegram - token rong la giu nguyen
        zalo_token = (_van_ban(payload.get("zalo_bot_token"), "") or "").strip()
        if zalo_token:
            notify_changes["zalo_bot_token"] = zalo_token
        if "zalo_chat_id" in payload:
            notify_changes["zalo_chat_id"] = (
                _van_ban(payload["zalo_chat_id"], "") or "").strip()
        if notify_changes:
            changes["notify"] = notify_changes

        # Rong = giu nguyen cai dang co, giong token Telegram
        cf_token = (_van_ban(payload.get("cloudflare_api_token"), "") or "").strip()
        if cf_token:
            changes["cloudflare_api_token"] = cf_token

        # WHMCS: kiem URL ngay luc luu, de loi "phai https" hien o day chu khong
        # phai moi lan bam dong bo. Truong bi mat rong la giu nguyen, giong token.
        if "whmcs_url" in payload:
            url = (_van_ban(payload["whmcs_url"], "") or "").strip()
            if url:
                try:
                    chuan_hoa_url(url)
                except WhmcsError as exc:
                    return jsonify({"error": str(exc)}), 400
            changes["whmcs_url"] = url
        for key in ("whmcs_identifier", "whmcs_secret", "whmcs_accesskey"):
            gia_tri = (_van_ban(payload.get(key), "") or "").strip()
            if gia_tri:
                changes[key] = gia_tri

        if not changes:
            return jsonify({"error": "Không có gì để lưu"}), 400

        try:
            cfgmod.save(changes)
        except OSError as exc:
            return jsonify({"error": f"Không ghi được config.json: {exc}"}), 500

        # Cap nhat luon cfg dang chay de khong phai khoi dong lai server
        for key, value in changes.items():
            if key == "notify":
                merged = dict(cfg.get("notify") or {})
                merged.update(value)
                cfg["notify"] = merged
            else:
                cfg[key] = value
        return api_settings_get()

    # ---- Cloudflare -------------------------------------------------------
    def cf_client() -> CloudflareClient:
        return CloudflareClient(cfg.get("cloudflare_api_token", ""))

    @app.post("/api/cloudflare/verify")
    def api_cf_verify():
        cf = cf_client()
        if not cf.configured:
            return jsonify({"error": "Chưa cấu hình API token Cloudflare"}), 400
        try:
            return jsonify(cf.verify())
        except CloudflareError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/cloudflare/sync")
    def api_cf_sync():
        """Doc trang thai zone cho toan bo ten mien trong kho.

        Chi doc, khong bao gio ghi len Cloudflare. Ten mien khong co zone trong
        tai khoan van duoc ghi lai (voi status rong) de phan biet "da quet,
        khong thay" voi "chua quet bao gio".
        """
        cf = cf_client()
        if not cf.configured:
            return jsonify({"error": "Chưa cấu hình API token Cloudflare"}), 400
        ten_mien = store.names()
        if not ten_mien:
            return jsonify({"error": "Kho trống"}), 400
        try:
            quet = cf.quet(ten_mien)
        except CloudflareError as exc:
            return jsonify({"error": str(exc)}), 400

        for d in ten_mien:
            store.save_cloudflare(d, quet.get(d.lower()) or {})
        store.set_meta("last_cf_sync", iso(utcnow()))

        recs = store.all()
        dem = {}
        for r in recs:
            dem[r.cf_ket_luan] = dem.get(r.cf_ket_luan, 0) + 1
        return jsonify({
            "ok": True,
            "zone_doc_duoc": len(quet),
            "tong": len(ten_mien),
            "theo_ket_luan": dem,
            "canh_bao": [r.domain for r in recs if r.cf_ket_luan == "khong-ban-ghi"],
        })

    # ---- WHMCS ------------------------------------------------------------
    def whmcs_client() -> WhmcsClient:
        return WhmcsClient(cfg.get("whmcs_url", ""), cfg.get("whmcs_identifier", ""),
                           cfg.get("whmcs_secret", ""), cfg.get("whmcs_accesskey", ""))

    @app.post("/api/whmcs/verify")
    def api_whmcs_verify():
        wc = whmcs_client()
        if not wc.configured:
            return jsonify({"error": "Chưa cấu hình đủ URL, identifier và secret của WHMCS"}), 400
        try:
            return jsonify(wc.verify())
        except WhmcsError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/whmcs/sync")
    def api_whmcs_sync():
        """Nhap ten mien tu WHMCS va ghi so sach cua no de doi chieu. CHI DOC WHMCS.

        Ten mien moi duoc them vao kho kem tag "whmcs" roi tra cuu registry. Ten
        mien da co thi KHONG dung toi tag / nha cung cap / ghi chu cua nguoi dung
        - chi ghi nhom cot whmcs_*. Goi add() cho ten da co la ghi de tag cua no.
        """
        wc = whmcs_client()
        if not wc.configured:
            return jsonify({"error": "Chưa cấu hình đủ URL, identifier và secret của WHMCS"}), 400
        try:
            ds = wc.tat_ca()
        except WhmcsError as exc:
            return jsonify({"error": str(exc)}), 400

        co_san = set(store.names())
        theo_ten = {m["domain"]: m for m in ds}
        moi = sorted(d for d in theo_ten if d not in co_san)
        for d in moi:
            store.add(d, tags=["whmcs"])
        for d, m in theo_ten.items():
            store.save_whmcs(d, m)
        # Co trong kho ma khong co trong WHMCS: van ghi "da dong bo, khong thay"
        for d in co_san - set(theo_ten):
            store.save_whmcs(d, {})
        store.set_meta("last_whmcs_sync", iso(utcnow()))

        # Ten moi chua co du lieu registry thi chua doi chieu duoc gi
        dang_tra_cuu = _spawn_refresh(moi) if moi else False
        recs = store.all()
        dem = {}
        for r in recs:
            dem[r.whmcs_ket_luan] = dem.get(r.whmcs_ket_luan, 0) + 1
        return jsonify({
            "ok": True,
            "tong_whmcs": len(theo_ten),
            "moi_them": len(moi),
            "dang_tra_cuu": bool(dang_tra_cuu),
            "theo_ket_luan": dem,
            "canh_bao": [r.domain for r in recs
                         if r.whmcs_ket_luan in ("het-ma-active", "hoa-don-tre")],
        })

    @app.post("/api/notify/test")
    def api_notify_test():
        """Gui tin thu. `kenh` = "telegram" / "zalo" de thu rieng mot kenh."""
        payload = _than_json()
        chi = payload.get("kenh") if payload.get("kenh") in ("telegram", "zalo") else None
        bot = notifier()
        if chi and not bot.kenh(chi).configured:
            return jsonify({"error": "Kênh này chưa có bot token hoặc chat id"}), 400
        if not bot.configured:
            return jsonify({"error": "Chưa cấu hình kênh cảnh báo nào"}), 400
        result = bot.send_test(chi)
        if result.get("ok"):
            return jsonify({"ok": True, "kenh": result["kenh"]})
        return jsonify({"error": result.get("description") or "Gửi thất bại",
                        "kenh": result.get("kenh")}), 502

    @app.post("/api/notify/zalo/chat-id")
    def api_zalo_chat_id():
        """Bat chat_id tu tin nhan gan nhat gui toi bot Zalo. Chi doc, khong gui.

        Zalo khong hien chat_id o dau ca, ke ca trong trinh tao bot - cach duy
        nhat la doc no tu mot tin nhan den. Thieu endpoint nay thi nguoi dung
        phai tu goi getUpdates bang curl, ma getUpdates lai la POST nen khong mo
        thang tren trinh duyet duoc.
        """
        notify = cfg.get("notify") or {}
        bot = ZaloNotifier(notify.get("zalo_bot_token", ""), "")
        if not bot.token:
            return jsonify({"error": "Chưa lưu bot token Zalo"}), 400
        r = bot.tim_chat_id()
        if not r.get("ok"):
            return jsonify({"error": r.get("description") or "Không đọc được tin nhắn"}), 502
        return jsonify({"ok": True, "chat": r["chat"]})

    @app.get("/api/notify/pending")
    def api_notify_pending():
        """Nhung canh bao se duoc gui neu bam gui ngay bay gio."""
        warn, crit = thresholds()
        pending = []
        for rec in store.all():
            bucket = bucket_of(rec, warn, crit)
            if not bucket:
                continue
            pending.append({
                "domain": rec.domain,
                "bucket": bucket,
                "days_left": rec.days_left,
                "already_sent": store.was_alerted(rec.domain, bucket, rec.expires_at),
            })
        pending.sort(key=lambda p: p["days_left"])
        return jsonify({
            "pending": pending,
            "new_count": sum(1 for p in pending if not p["already_sent"]),
            "total": len(pending),
        })

    @app.post("/api/notify/send")
    def api_notify_send():
        payload = _than_json()
        force = bool(payload.get("force"))
        warn, crit = thresholds()
        bot = notifier()
        if not bot.configured:
            return jsonify({"error": "Chưa cấu hình bot token hoặc chat id"}), 400

        due = []
        for rec in store.all():
            bucket = bucket_of(rec, warn, crit)
            if not bucket:
                continue
            if not force and store.was_alerted(rec.domain, bucket, rec.expires_at):
                continue
            due.append((rec, bucket))

        if not due:
            return jsonify({"ok": True, "sent": 0, "reason": "no-new"})

        result = bot.send(build_message([r for r, _ in due], warn, crit))
        if not result.get("ok"):
            return jsonify({"error": result.get("description") or "Gửi thất bại"}), 502

        for rec, bucket in due:
            store.mark_alerted(rec.domain, bucket, rec.expires_at)
        store.set_meta("last_notify", iso(utcnow()))
        return jsonify({"ok": True, "sent": len(due)})

    @app.post("/api/notify/reset")
    def api_notify_reset():
        return jsonify({"ok": True, "removed": store.clear_alerts()})

    # ---- xuat file --------------------------------------------------------
    def _export_name(ext: str) -> str:
        return "domain-gateway-" + utcnow().astimezone().strftime("%Y%m%d") + "." + ext

    def _attachment(payload, mimetype: str, ext: str) -> Response:
        return Response(payload, mimetype=mimetype, headers={
            "Content-Disposition": 'attachment; filename="' + _export_name(ext) + '"',
            "Cache-Control": "no-store",
        })

    @app.get("/api/export.md")
    def api_export_md():
        """Markdown soan cho AI agent: kem ngu canh va bay nghiep vu, khong chi la bang."""
        warn, crit = thresholds()
        text = exporters.to_markdown(store.all(), warn, crit)
        # inline chu khong attachment: agent va nguoi dung deu doc thang tren trinh duyet
        return Response(text, mimetype="text/markdown")

    @app.get("/api/export.xlsx")
    def api_export_xlsx():
        warn, crit = thresholds()
        try:
            data = exporters.to_xlsx(store.all(), warn, crit)
        except ImportError:
            return jsonify({"error": "Thiếu thư viện openpyxl. Chạy: pip install openpyxl"}), 501
        return _attachment(
            data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx")

    @app.get("/api/export.pdf")
    def api_export_pdf():
        warn, crit = thresholds()
        try:
            data = exporters.to_pdf(store.all(), warn, crit)
        except ImportError:
            return jsonify({"error": "Thiếu thư viện reportlab. Chạy: pip install reportlab"}), 501
        except LayoutError:
            # Luoi do cuoi: ban cat theo be rong cot la uoc luong, con chieu cao
            # that thi phu thuoc font va chu viet. Doan sai thi tra loi doc duoc
            # chu khong phai trang 500 tran.
            return jsonify({
                "error": "Có ô dữ liệu quá dài để dàn vào trang PDF. "
                         "Rút gọn ghi chú hoặc tên nhà cung cấp rồi thử lại."
            }), 400
        return _attachment(data, "application/pdf", "pdf")

    @app.post("/api/registrars.pdf")
    def api_registrars_pdf():
        """Client gui phan danh ba DA LOC len, server chi lo dan trang PDF.

        Bo loc nam o JavaScript; neu server loc lai thi phai viet lan thu hai
        bang Python va hai ban chac chan se lech nhau."""
        payload = _than_json()
        if not _danh_ba_hop_le(payload.get("sections")):
            return jsonify({"error": "Thiếu danh sách khu vực, hoặc sai định dạng"}), 400
        try:
            data = exporters.registry_pdf(payload)
        except ImportError:
            return jsonify({"error": "Thiếu thư viện reportlab. Chạy: pip install reportlab"}), 501
        except LayoutError:
            return jsonify({
                "error": "Có ô dữ liệu quá dài để dàn vào trang PDF."
            }), 400
        name = "danh-ba-nha-dang-ky-" + utcnow().astimezone().strftime("%Y%m%d") + ".pdf"
        return Response(data, mimetype="application/pdf", headers={
            "Content-Disposition": 'attachment; filename="' + name + '"',
            "Cache-Control": "no-store",
        })

    @app.get("/api/export.csv")
    def api_export_csv():
        cols = ["domain", "status", "days_left", "expires_at", "provider", "registrar",
                "created_at", "nameservers", "auto_renew", "tags", "source", "note", "error"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for rec in store.all():
            row = serialize(rec)
            row["nameservers"] = ", ".join(row.get("nameservers") or [])
            row["tags"] = ", ".join(row.get("tags") or [])
            # Rao cong thuc: ten registrar den tu WHOIS cua ben thu ba, o bat dau
            # bang "=" thi Excel chay nó nhu cong thuc luc mo file.
            writer.writerow({k: exporters.rao_cong_thuc(v) for k, v in row.items()})
        return Response(
            "﻿" + buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=domain-gateway.csv"},
        )

    return app


if __name__ == "__main__":
    _cfg = cfgmod.load()
    create_app(_cfg).run(
        host=_cfg.get("host", "127.0.0.1"),
        port=int(_cfg.get("port", 8787)),
        debug=False,
    )
