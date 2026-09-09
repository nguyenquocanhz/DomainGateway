#!/usr/bin/env python
"""Domain Gateway CLI - quan ly tap trung ten mien tren nhieu nha cung cap.

Vi du:
    python cli.py import data/domains.example.json
    python cli.py refresh
    python cli.py list
    python cli.py add example.com --provider Porkbun --tags shop,prod
    python cli.py set congty-vidu.io.vn --provider iNET --expires 2027-03-01 --pin
    python cli.py export --csv data/domains.csv
    python cli.py export --md bao-cao.md --xlsx bao-cao.xlsx --pdf bao-cao.pdf
    python cli.py notify --dry-run
    python cli.py serve
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Console Windows mac dinh la cp1252 -> vo khi in ten registrar tieng Viet
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

from gateway import config as cfgmod
from gateway.models import STATUS_LABEL, iso, utcnow
from gateway.notifier import TelegramNotifier, bucket_of, build_message
from gateway.resolver import normalize_domain
from gateway.store import Store

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    CONSOLE = Console()
except ImportError:
    CONSOLE = None

STATUS_STYLE = {
    "active": "green",
    "expiring": "yellow",
    "critical": "bold red",
    "expired": "bold white on red",
    "unknown": "dim",
}


def _print(msg: str) -> None:
    if CONSOLE:
        CONSOLE.print(msg)
    else:
        print(msg)


# --------------------------------------------------------------------------- #
def cmd_import(args, store: Store, cfg: dict) -> int:
    with open(args.file, "r", encoding="utf-8") as fh:
        raw = json.load(fh) if args.file.endswith(".json") else [
            {"domain": line.strip()} for line in fh if line.strip() and not line.startswith("#")
        ]
    if isinstance(raw, dict):
        raw = raw.get("domains", [])
    count = 0
    for item in raw:
        if isinstance(item, str):
            item = {"domain": item}
        domain = normalize_domain(item.get("domain", ""))
        if not domain:
            continue
        store.add(
            domain,
            provider=item.get("provider", ""),
            tags=item.get("tags") or [],
            note=item.get("note", ""),
            manual_expires_at=item.get("expires_at"),
            auto_renew=item.get("auto_renew"),
            pinned=bool(item.get("pinned")),
        )
        count += 1
    _print(f"[green]Da nap {count} ten mien vao kho.[/green]" if CONSOLE else f"Da nap {count} ten mien.")
    return 0


def cmd_add(args, store: Store, cfg: dict) -> int:
    domain = normalize_domain(args.domain)
    store.add(
        domain,
        provider=args.provider or "",
        tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
        note=args.note or "",
        manual_expires_at=args.expires,
        auto_renew=None if args.auto_renew is None else args.auto_renew,
        pinned=bool(args.pin),
    )
    _print(f"Da them: {domain}")
    if not args.no_lookup:
        resolver = cfgmod.make_resolver(cfg)
        rec = resolver.lookup(domain)
        store.save_lookup(rec)
        _print(f"  -> {rec.source or 'khong ro'} | het han: {iso(rec.expires_at) or 'khong doc duoc'}"
               + (f" | {rec.error}" if rec.error else ""))
    return 0


def cmd_set(args, store: Store, cfg: dict) -> int:
    domain = normalize_domain(args.domain)
    if not store.get(domain):
        _print(f"Khong tim thay {domain} trong kho.")
        return 1
    store.update_user_fields(
        domain,
        provider=args.provider,
        tags=[t.strip() for t in args.tags.split(",") if t.strip()] if args.tags is not None else None,
        note=args.note,
        manual_expires_at=args.expires,
        auto_renew=args.auto_renew if args.auto_renew is not None else "__keep__",
        pinned=args.pin,
    )
    _print(f"Da cap nhat {domain}")
    return 0


def cmd_rm(args, store: Store, cfg: dict) -> int:
    domain = normalize_domain(args.domain)
    store.delete(domain)
    _print(f"Da xoa {domain}")
    return 0


def cmd_refresh(args, store: Store, cfg: dict) -> int:
    names = [normalize_domain(d) for d in args.domains] if args.domains else store.names()
    if not names:
        _print("Kho trong. Chay: python cli.py import data/domains.example.json")
        return 1

    if not args.all:
        ttl = int(cfg.get("cache_ttl_hours", 12)) * 3600
        fresh = []
        for name in names:
            rec = store.get(name)
            if rec and rec.checked_at and (utcnow() - rec.checked_at).total_seconds() < ttl and not rec.error:
                continue
            fresh.append(name)
        skipped = len(names) - len(fresh)
        if skipped:
            _print(f"Bo qua {skipped} ten mien con trong cache ({cfg.get('cache_ttl_hours')}h). Dung --all de ep tra cuu lai.")
        names = fresh
    if not names:
        _print("Tat ca deu con moi, khong can tra cuu.")
        return 0

    resolver = cfgmod.make_resolver(cfg)
    _print(f"Dang tra cuu {len(names)} ten mien...")

    def progress(done, total, rec):
        mark = "OK " if rec.expires_at else "!! "
        detail = iso(rec.expires_at)[:10] if rec.expires_at else (rec.error or "khong ro")
        _print(f"  [{done}/{total}] {mark}{rec.domain:28} {detail}")

    records = resolver.lookup_many(names, progress=progress)
    for rec in records:
        store.save_lookup(rec)
    store.set_meta("last_refresh", iso(utcnow()))
    ok = sum(1 for r in records if r.expires_at)
    _print(f"Xong: {ok}/{len(records)} tra cuu thanh cong.")
    return 0


def cmd_list(args, store: Store, cfg: dict) -> int:
    warn, crit = int(cfg["warn_days"]), int(cfg["critical_days"])
    records = store.all()
    if args.expiring:
        records = [r for r in records if r.days_left is not None and r.days_left <= warn]
    if args.provider:
        needle = args.provider.lower()
        records = [r for r in records if needle in (r.provider or r.registrar or "").lower()]
    records.sort(key=lambda r: (r.days_left is None, r.days_left if r.days_left is not None else 0))

    if not records:
        _print("Khong co ten mien nao khop dieu kien.")
        return 0

    if CONSOLE:
        table = Table(box=box.SIMPLE_HEAVY, header_style="bold", title="Domain Gateway", title_style="bold")
        table.add_column("Domain", no_wrap=True)
        table.add_column("Trang thai", no_wrap=True)
        table.add_column("Con lai", justify="right", no_wrap=True)
        table.add_column("Het han", no_wrap=True)
        table.add_column("Nha cung cap", no_wrap=True)
        table.add_column("Registrar", max_width=26, overflow="ellipsis")
        table.add_column("Nguon", no_wrap=True)
        for rec in records:
            status = rec.status(warn, crit)
            days = "-" if rec.days_left is None else f"{rec.days_left}d"
            table.add_row(
                rec.domain,
                f"[{STATUS_STYLE[status]}]{STATUS_LABEL[status]}[/{STATUS_STYLE[status]}]",
                days,
                rec.expires_at.astimezone().strftime("%d/%m/%Y") if rec.expires_at else "-",
                rec.provider or "-",
                (rec.registrar or "-")[:28],
                rec.source or "-",
            )
        CONSOLE.print(table)
    else:
        for rec in records:
            print(f"{rec.domain:30} {rec.status(warn, crit):10} "
                  f"{rec.expires_at.astimezone().strftime('%d/%m/%Y') if rec.expires_at else '-':12} "
                  f"{rec.provider or rec.registrar or '-'}")

    summary = store.summary(warn, crit)
    _print(f"\nTong {summary['total']} | sap het han {summary['expiring'] + summary['critical']} "
           f"| da het han {summary['expired']} | chua ro {summary['unknown']}")
    if summary["next_expiry"]:
        nxt = summary["next_expiry"]
        _print(f"Gan nhat: {nxt['domain']} - {nxt['date']} (con {nxt['days_left']} ngay)")
    return 0


def cmd_export(args, store: Store, cfg: dict) -> int:
    warn, crit = int(cfg["warn_days"]), int(cfg["critical_days"])
    rows = [r.to_dict(warn, crit) for r in store.all()]
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        _print(f"Da xuat JSON: {args.json}")
    if args.csv:
        cols = ["domain", "status", "days_left", "expires_at", "provider", "registrar",
                "created_at", "nameservers", "auto_renew", "tags", "source", "note", "error"]
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                row = dict(row)
                row["nameservers"] = ", ".join(row.get("nameservers") or [])
                row["tags"] = ", ".join(row.get("tags") or [])
                writer.writerow(row)
        _print(f"Da xuat CSV: {args.csv}")
    records = store.all()

    if args.md:
        from gateway import exporters
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(exporters.to_markdown(records, warn, crit))
        _print(f"Da xuat Markdown: {args.md}")

    if args.xlsx:
        try:
            from gateway import exporters
            with open(args.xlsx, "wb") as fh:
                fh.write(exporters.to_xlsx(records, warn, crit))
            _print(f"Da xuat Excel: {args.xlsx}")
        except ImportError:
            _print("Thieu thu vien openpyxl. Chay: pip install openpyxl")
            return 1

    if args.pdf:
        try:
            from gateway import exporters
            with open(args.pdf, "wb") as fh:
                fh.write(exporters.to_pdf(records, warn, crit))
            _print(f"Da xuat PDF: {args.pdf}")
        except ImportError:
            _print("Thieu thu vien reportlab. Chay: pip install reportlab")
            return 1

    if not any((args.json, args.csv, args.md, args.xlsx, args.pdf)):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _make_notifier(cfg: dict) -> TelegramNotifier:
    notify = cfg.get("notify") or {}
    return TelegramNotifier(notify.get("telegram_bot_token", ""),
                            notify.get("telegram_chat_id", ""))


def cmd_notify(args, store: Store, cfg: dict) -> int:
    warn, crit = int(cfg["warn_days"]), int(cfg["critical_days"])
    bot = _make_notifier(cfg)

    if args.reset:
        removed = store.clear_alerts()
        _print(f"Da xoa {removed} ban ghi chong trung. Lan chay toi se bao lai tu dau.")
        return 0

    if args.test:
        if not bot.configured:
            _print("Chua cau hinh telegram_bot_token / telegram_chat_id trong config.json.")
            return 1
        result = bot.send_test()
        if result.get("ok"):
            _print(f"Da gui tin nhan kiem tra toi @{result.get('bot')}. Kiem tra Telegram cua ban.")
            return 0
        _print(f"That bai: {result.get('description')}")
        return 1

    # Loc theo nguong, roi bo nhung cai da bao roi (tru khi --force)
    due = []
    for rec in store.all():
        bucket = bucket_of(rec, warn, crit)
        if not bucket:
            continue
        if not args.force and store.was_alerted(rec.domain, bucket, rec.expires_at):
            continue
        due.append((rec, bucket))

    if not due:
        _print("Khong co canh bao moi. Dung --force de gui lai ca nhung cai da bao.")
        return 0

    message = build_message([r for r, _ in due], warn, crit)

    if args.dry_run:
        print(message)
        _print(f"\n({len(due)} ten mien - dry run, chua gui gi.)")
        return 0

    if not bot.configured:
        _print("Chua cau hinh telegram_bot_token / telegram_chat_id trong config.json.")
        print("\n" + message)
        return 1

    result = bot.send(message)
    if not result.get("ok"):
        _print(f"Gui that bai: {result.get('description')}")
        return 1

    for rec, bucket in due:
        store.mark_alerted(rec.domain, bucket, rec.expires_at)
    store.set_meta("last_notify", iso(utcnow()))
    _print(f"Da gui canh bao cho {len(due)} ten mien.")
    return 0


def cmd_cloudflare(args, store: Store, cfg: dict) -> int:
    """Doc trang thai zone tu Cloudflare cho toan bo kho.

    Tra loi cau hoi ma RDAP khong tra loi duoc: ten mien nay co dang phuc vu gi
    khong. Con han 500 ngay ma zone khong co ban ghi A nao thi website van tat.
    """
    from gateway.cloudflare import CloudflareClient, CloudflareError

    cf = CloudflareClient(cfg.get("cloudflare_api_token", ""))
    if not cf.configured:
        print("Chua cau hinh API token Cloudflare.")
        print("Tao token doc-chi (Zone:Read + Zone.DNS:Read) tai:")
        print("  https://dash.cloudflare.com/profile/api-tokens")
        print("Roi dat vao config.json khoa \"cloudflare_api_token\",")
        print("hoac bien moi truong DG_CF_TOKEN, hoac trang Cai dat cua dashboard.")
        return 2

    ten_mien = store.names()
    if not ten_mien:
        print("Kho trong.")
        return 1

    try:
        if args.verify:
            print("Token:", cf.verify())
            return 0
        quet = cf.quet(ten_mien)
    except CloudflareError as exc:
        print("Loi:", exc)
        return 1

    for d in ten_mien:
        store.save_cloudflare(d, quet.get(d.lower()) or {})
    store.set_meta("last_cf_sync", iso(utcnow()))

    NHAN = {
        "ok": "OK",
        "khong-ban-ghi": "KHONG CO BAN GHI  <-- ten mien song, website tat",
        "khong-thay": "khong co trong tai khoan Cloudflare",
        "tam-dung": "zone dang tam dung",
        "thieu-quyen-dns": "thieu quyen Zone.DNS:Read",
    }
    print(f"{'Ten mien':24} {'Zone':10} {'Ban ghi':>8}  Ket luan")
    print("-" * 78)
    canh_bao = 0
    for r in sorted(store.all(), key=lambda x: x.domain):
        kl = r.cf_ket_luan
        if kl == "khong-ban-ghi":
            canh_bao += 1
        bg = "-" if r.cf_records is None else str(r.cf_records)
        print(f"{r.domain:24} {r.cf_status or '-':10} {bg:>8}  "
              f"{NHAN.get(kl, kl)}")
    print()
    print(f"Doc duoc {len(quet)} zone / {len(ten_mien)} ten mien.", end="")
    print(f"  CANH BAO: {canh_bao} ten mien khong co ban ghi nao." if canh_bao else "")
    return 0


def cmd_serve(args, store: Store, cfg: dict) -> int:
    from app import create_app
    app = create_app(cfg, store)
    host = args.host or cfg.get("host", "127.0.0.1")
    port = args.port or int(cfg.get("port", 8787))
    _print(f"Domain Gateway dang chay tai http://{host}:{port}")
    app.run(host=host, port=port, debug=args.debug, use_reloader=False)
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="domain-gateway", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="Duong dan config.json tuy chon")
    parser.add_argument("--db", help="Duong dan file SQLite tuy chon")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="Nap danh sach ten mien tu file JSON/TXT")
    p.add_argument("file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("add", help="Them mot ten mien")
    p.add_argument("domain")
    p.add_argument("--provider", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--note", default="")
    p.add_argument("--expires", help="Ngay het han nhap tay (YYYY-MM-DD)")
    p.add_argument("--pin", action="store_true", help="Uu tien ngay nhap tay hon du lieu registry")
    p.add_argument("--auto-renew", dest="auto_renew", type=lambda v: v.lower() in ("1", "true", "yes", "co"))
    p.add_argument("--no-lookup", action="store_true", help="Chi them, khong tra cuu ngay")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("set", help="Sua thong tin do nguoi dung quan ly")
    p.add_argument("domain")
    p.add_argument("--provider")
    p.add_argument("--tags")
    p.add_argument("--note")
    p.add_argument("--expires")
    p.add_argument("--pin", action="store_true", default=None)
    p.add_argument("--auto-renew", dest="auto_renew", type=lambda v: v.lower() in ("1", "true", "yes", "co"))
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("rm", help="Xoa ten mien khoi kho")
    p.add_argument("domain")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("refresh", help="Tra cuu lai tu registry")
    p.add_argument("domains", nargs="*", help="De trong = tat ca")
    p.add_argument("--all", action="store_true", help="Bo qua cache, ep tra cuu lai het")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("list", help="Liet ke ten mien")
    p.add_argument("--expiring", action="store_true", help="Chi hien cai sap het han")
    p.add_argument("--provider", help="Loc theo nha cung cap")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("export", help="Xuat CSV / JSON / Markdown / Excel / PDF")
    p.add_argument("--csv", help="Bang tinh co ban")
    p.add_argument("--json", help="Du lieu tho")
    p.add_argument("--md", help="Markdown kem ngu canh, soan cho AI agent doc")
    p.add_argument("--xlsx", help="Excel: ngay thang kieu date, co loc va sheet tom tat")
    p.add_argument("--pdf", help="PDF de in hoac gui di")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("notify", help="Gui canh bao het han qua Telegram")
    p.add_argument("--dry-run", action="store_true", help="Chi in ra man hinh, khong gui")
    p.add_argument("--test", action="store_true", help="Gui tin nhan kiem tra ket noi")
    p.add_argument("--force", action="store_true", help="Gui lai ca nhung canh bao da bao roi")
    p.add_argument("--reset", action="store_true", help="Xoa lich su chong trung")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("cloudflare", help="Doc trang thai zone tu Cloudflare")
    p.add_argument("--verify", action="store_true",
                   help="Chi kiem tra token, khong doc zone nao")
    p.set_defaults(func=cmd_cloudflare)

    p = sub.add_parser("serve", help="Chay dashboard web")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfgmod.load(args.config)
    store = Store(args.db) if args.db else Store()
    return args.func(args, store, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
