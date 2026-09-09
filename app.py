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
from gateway.models import iso, utcnow
from gateway.notifier import TelegramNotifier, bucket_of, build_message
from gateway.cloudflare import CloudflareClient, CloudflareError
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


def _che_token(token: str) -> str:
    """Che bot token, chi de lo phan von cong khai.

    Token Telegram co dang <bot_id>:<35 ky tu bi mat>. bot_id lo ra khong sao -
    ai chat voi bot cung thay duoc. Phan sau dau hai cham thi che sach.
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

    def notifier() -> TelegramNotifier:
        notify = cfg.get("notify") or {}
        return TelegramNotifier(notify.get("telegram_bot_token", ""),
                                notify.get("telegram_chat_id", ""))

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
        payload = request.get_json(silent=True) or {}
        raw = payload.get("domain", "")
        added, skipped = [], []
        # Chan so luong moi lan them. Khong phai lo bi tan cong - CSRF da chan
        # duong do - ma vi dan nham ca file vao o nhap thi kho phinh ra hang van
        # dong, roi refresh chay het quota BKNS va treo hang gio.
        MAX_MOI_LAN = 500
        # Cho phep dan nhieu ten mien cung luc, cach nhau bang xuong dong/phay/space
        khoi = str(raw).replace(",", "\n").replace(" ", "\n").splitlines()
        if len(khoi) > MAX_MOI_LAN:
            return jsonify({
                "error": f"Quá {MAX_MOI_LAN} dòng trong một lần thêm. "
                         "Chia nhỏ ra, hoặc dùng `python cli.py import` cho danh sách dài."
            }), 400
        for chunk in khoi:
            domain = normalize_domain(chunk)
            if not domain or "." not in domain:
                if chunk.strip():
                    skipped.append(chunk.strip())
                continue
            store.add(
                domain,
                provider=payload.get("provider", ""),
                tags=payload.get("tags") or [],
                note=payload.get("note", ""),
                manual_expires_at=payload.get("expires_at"),
                auto_renew=payload.get("auto_renew"),
                pinned=bool(payload.get("pinned")),
            )
            added.append(domain)
        if not added:
            return jsonify({"error": "Khong co tên miền hợp lệ", "skipped": skipped}), 400
        if payload.get("lookup", True):
            _spawn_refresh(added)
        return jsonify({"added": added, "skipped": skipped}), 201

    @app.patch("/api/domains/<domain>")
    def api_update(domain):
        domain = normalize_domain(domain)
        if not store.get(domain):
            return jsonify({"error": "Không tìm thấy tên miền"}), 404
        payload = request.get_json(silent=True) or {}
        store.update_user_fields(
            domain,
            provider=payload.get("provider"),
            tags=payload.get("tags"),
            note=payload.get("note"),
            manual_expires_at=payload.get("expires_at"),
            auto_renew=payload["auto_renew"] if "auto_renew" in payload else "__keep__",
            pinned=payload.get("pinned"),
        )
        return jsonify(serialize(store.get(domain)))

    @app.delete("/api/domains/<domain>")
    def api_delete(domain):
        store.delete(normalize_domain(domain))
        return jsonify({"deleted": domain})

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

        try:
            for rec in resolver.lookup_many(names, progress=progress):
                store.save_lookup(rec)
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
        payload = request.get_json(silent=True) or {}
        names = payload.get("domains") or store.names()
        names = [normalize_domain(n) for n in names if n]
        if not names:
            return jsonify({"error": "Kho trống"}), 400

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
            "notify_configured": notifier().configured,
            # Token Cloudflare la chuoi doi khong co cau truc cong khai nao,
            # nen khong he lo ky tu nao ca - bai hoc tu cach che token Telegram.
            "cloudflare_token_set": bool(cfg.get("cloudflare_api_token")),
            "last_cf_sync": store.get_meta("last_cf_sync"),
            "last_notify": store.get_meta("last_notify"),
            "cron_command": (
                'schtasks /create /tn "DomainGateway" /tr '
                f'"cmd /c cd /d {ROOT} && python cli.py refresh && python cli.py notify" '
                '/sc daily /st 08:00'
            ),
        })

    @app.post("/api/settings")
    def api_settings_post():
        payload = request.get_json(silent=True) or {}
        changes, notify_changes = {}, {}

        for key in ("warn_days", "critical_days", "cache_ttl_hours"):
            if key in payload and str(payload[key]).strip() != "":
                try:
                    changes[key] = max(0, int(payload[key]))
                except (TypeError, ValueError):
                    return jsonify({"error": f"Giá trị không hợp lệ cho {key}"}), 400

        # Token rong nghia la "giu nguyen cai dang co", khong phai "xoa di"
        token = (payload.get("telegram_bot_token") or "").strip()
        if token:
            notify_changes["telegram_bot_token"] = token
        if "telegram_chat_id" in payload:
            notify_changes["telegram_chat_id"] = str(payload["telegram_chat_id"]).strip()
        if notify_changes:
            changes["notify"] = notify_changes

        # Rong = giu nguyen cai dang co, giong token Telegram
        cf_token = (payload.get("cloudflare_api_token") or "").strip()
        if cf_token:
            changes["cloudflare_api_token"] = cf_token

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

    @app.post("/api/notify/test")
    def api_notify_test():
        bot = notifier()
        if not bot.configured:
            return jsonify({"error": "Chưa cấu hình bot token hoặc chat id"}), 400
        result = bot.send_test()
        if result.get("ok"):
            return jsonify({"ok": True, "bot": result.get("bot")})
        return jsonify({"error": result.get("description") or "Gửi thất bại"}), 502

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
        payload = request.get_json(silent=True) or {}
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
        return _attachment(data, "application/pdf", "pdf")

    @app.post("/api/registrars.pdf")
    def api_registrars_pdf():
        """Client gui phan danh ba DA LOC len, server chi lo dan trang PDF.

        Bo loc nam o JavaScript; neu server loc lai thi phai viet lan thu hai
        bang Python va hai ban chac chan se lech nhau."""
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload.get("sections"), list):
            return jsonify({"error": "Thiếu danh sách khu vực"}), 400
        try:
            data = exporters.registry_pdf(payload)
        except ImportError:
            return jsonify({"error": "Thiếu thư viện reportlab. Chạy: pip install reportlab"}), 501
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
