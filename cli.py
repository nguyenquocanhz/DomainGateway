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
import io
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
from gateway.models import STATUS_LABEL, iso, lam_sach, lam_sach_giu_khoa, utcnow
from gateway.notifier import MultiNotifier, bucket_of, build_message, tu_cau_hinh
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
    # Cung ly do nhu than JSON cua HTTP: json.load nhan escape \ud800, sqlite
    # thi khong. File nay do nguoi dung tu soan nen van la du lieu ben ngoai.
    #
    # PHAI dung ban _giu_khoa: don ca truong `domain` thi normalize_domain thay
    # chuoi da sach, la chan cua no khong bao gio nay, va "a\x01b.com" van ghi
    # de len ban ghi "ab.com" co that - dung lo ma commit truoc di va.
    try:
        raw = lam_sach_giu_khoa(raw)
    except ValueError as e:
        _print(f"File {args.file} khong dung dinh dang: {e}")
        return 1
    if isinstance(raw, dict):
        raw = raw.get("domains", [])
    count = 0
    bo_qua = []
    for item in raw:
        if isinstance(item, str):
            item = {"domain": item}
        if not isinstance(item, dict) or not isinstance(item.get("domain", ""), str):
            bo_qua.append(repr(item)[:40])
            continue
        domain = normalize_domain(item.get("domain", ""))
        # "." not in domain: cung kiem nhu cmd_add. Thieu no thi mot ten miền
        # rac tao ra khoa cut nhu "sur" trong kho.
        if not domain or "." not in domain:
            bo_qua.append(str(item.get("domain", ""))[:40])
            continue
        store.add(
            domain,
            provider=item.get("provider") if isinstance(item.get("provider"), str) else "",
            tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
            note=item.get("note") if isinstance(item.get("note"), str) else "",
            manual_expires_at=item.get("expires_at"),
            auto_renew=item.get("auto_renew"),
            pinned=bool(item.get("pinned")),
        )
        count += 1
    _print(f"[green]Da nap {count} ten mien vao kho.[/green]" if CONSOLE else f"Da nap {count} ten mien.")
    return 0


def cmd_add(args, store: Store, cfg: dict) -> int:
    domain = normalize_domain(args.domain)
    # normalize_domain tra chuoi rong khi dau vao co ky tu khong hop le. Khong
    # chan o day thi kho co mot ban ghi ten "" khong xoa duoc tu giao dien.
    if not domain or "." not in domain:
        _print(f"Ten mien khong hop le: {args.domain!r}")
        return 1
    # argv cung la du lieu ngoai: dan mot doan copy tu terminal vao --note la
    # co the mang theo ky tu dieu khien. normalize_domain da don `domain`.
    store.add(
        domain,
        provider=lam_sach(args.provider or ""),
        tags=[lam_sach(t.strip()) for t in (args.tags or "").split(",") if t.strip()],
        note=lam_sach(args.note or ""),
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
        provider=lam_sach(args.provider) if args.provider is not None else None,
        tags=([lam_sach(t.strip()) for t in args.tags.split(",") if t.strip()]
              if args.tags is not None else None),
        note=lam_sach(args.note) if args.note is not None else None,
        # --expires "" xoa ngay nhap tay; khong truyen --expires thi giu nguyen
        manual_expires_at="__keep__" if args.expires is None else args.expires,
        auto_renew=args.auto_renew if args.auto_renew is not None else "__keep__",
        pinned=args.pin,
    )
    _print(f"Da cap nhat {domain}")
    return 0


def cmd_rm(args, store: Store, cfg: dict) -> int:
    domain = normalize_domain(args.domain)
    # Khong co thi noi thang, dung in "Da xoa " voi ten rong roi tra 0 - giong
    # DELETE /api/domains da sua.
    if not domain or not store.get(domain):
        _print(f"Khong tim thay {args.domain}")
        return 1
    store.delete(domain)
    _print(f"Da xoa {domain}")
    return 0


def cmd_refresh(args, store: Store, cfg: dict) -> int:
    # Loc chuoi rong: normalize_domain tra "" voi ten mien co ky tu khong hop le
    names = ([d for d in (normalize_domain(x) for x in args.domains) if d]
             if args.domains else store.names())
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
    da_luu, khong_luu = 0, []
    for rec in records:
        try:
            store.save_lookup(rec)
            da_luu += 1
        except Exception as exc:                              # noqa: BLE001
            # Bao ve tung ban ghi, giong vong refresh ben app.py.
            khong_luu.append(f"{rec.domain} ({type(exc).__name__})")
    if da_luu:
        store.set_meta("last_refresh", iso(utcnow()))
    ok = sum(1 for r in records if r.expires_at)
    _print(f"Xong: {ok}/{len(records)} tra cuu thanh cong.")
    if khong_luu:
        _print("Khong luu duoc: " + ", ".join(khong_luu))
        return 1
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


def _ghi_an_toan(duong_dan: str, noi_dung, nhi_phan: bool = False,
                 ma_hoa: str = "utf-8", xuong_dong_tho: bool = False) -> None:
    """Dung noi dung XONG roi moi cham vao file dich.

    `open(dich, "w")` cat cut file ve 0 byte NGAY LUC MO, truoc khi ham dung
    noi dung kip chay. Ham do nem loi giua chung la ban xuat cu mat sach, doi
    lay mot file rong - do that: 1.900 byte -> 0 byte, khong canh bao gi.

    Ghi ra file tam canh dich roi os.replace: dich chi bi thay khi da co du
    noi dung, va phep thay la nguyen tu tren cung mot o dia.
    """
    tam = duong_dan + ".tmp"
    try:
        if nhi_phan:
            with open(tam, "wb") as fh:
                fh.write(noi_dung)
        else:
            # newline="" CHI can cho CSV (module csv tu quan ly ket dong). Dat
            # cho moi dich thi JSON/Markdown tren Windows doi CRLF thanh LF so
            # voi ban cu.
            with open(tam, "w", encoding=ma_hoa,
                      newline=("" if xuong_dong_tho else None)) as fh:
                fh.write(noi_dung)
        os.replace(tam, duong_dan)
    except OSError as exc:
        try:
            os.remove(tam)
        except OSError:
            pass
        # Tren Windows os.replace doi dich mo kem co FILE_SHARE_DELETE, ma
        # open() cua Python khong cap. Mot trinh soan thao / OneDrive /
        # antivirus dang giu file xuat la du de nem WinError 5. Bao cho ro
        # thay vi de traceback tran roi bo do cac dich con lai.
        raise OSError(f"khong ghi duoc {duong_dan}: {exc}. "
                      f"Dong chuong trinh dang mo file do roi thu lai.") from exc
    except BaseException:
        try:
            os.remove(tam)
        except OSError:
            pass
        raise


def cmd_export(args, store: Store, cfg: dict) -> int:
    """Xuat ra nhieu dinh dang mot lan.

    Moi dich duoc boc rieng: mot dich hong - thieu thu vien, file dang bi mo,
    thu muc chi doc - khong duoc lam cac dich con lai khong bao gio duoc ghi.
    Truoc day mot OSError o dich thu hai la traceback tran va hai dich sau im
    lang bien mat.
    """
    warn, crit = int(cfg["warn_days"]), int(cfg["critical_days"])
    rows = [r.to_dict(warn, crit) for r in store.all()]
    records = store.all()
    hong = []

    def thu(nhan, duong_dan, dung, thieu_thu_vien, **kw):
        """dung() dung noi dung; chi cham vao file dich khi da dung xong."""
        try:
            _ghi_an_toan(duong_dan, dung(), **kw)
            _print(f"Da xuat {nhan}: {duong_dan}")
        except ImportError:
            _print(thieu_thu_vien)
            hong.append(nhan)
        except Exception as exc:                          # noqa: BLE001
            # Bat rong co chu y: LayoutError cua reportlab va
            # IllegalCharacterError cua openpyxl deu la ung vien that, va muc
            # dich cua ham nay la mot dich hong khong duoc giet cac dich sau.
            _print(f"Loi khi xuat {nhan}: {type(exc).__name__}: {exc}")
            hong.append(nhan)

    if args.json:
        thu("JSON", args.json,
            lambda: json.dumps(rows, ensure_ascii=False, indent=2), "")

    if args.csv:
        cols = ["domain", "status", "days_left", "expires_at", "provider", "registrar",
                "created_at", "nameservers", "auto_renew", "tags", "source", "note", "error"]

        def dung_csv():
            dem = io.StringIO()
            writer = csv.DictWriter(dem, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                row = dict(row)
                row["nameservers"] = ", ".join(row.get("nameservers") or [])
                row["tags"] = ", ".join(row.get("tags") or [])
                writer.writerow(row)
            return dem.getvalue()

        thu("CSV", args.csv, dung_csv, "", ma_hoa="utf-8-sig", xuong_dong_tho=True)

    if args.md:
        from gateway import exporters
        thu("Markdown", args.md,
            lambda: exporters.to_markdown(records, warn, crit), "")

    if args.xlsx:
        from gateway import exporters
        thu("Excel", args.xlsx,
            lambda: exporters.to_xlsx(records, warn, crit),
            "Thieu thu vien openpyxl. Chay: pip install openpyxl", nhi_phan=True)

    if args.pdf:
        from gateway import exporters
        thu("PDF", args.pdf,
            lambda: exporters.to_pdf(records, warn, crit),
            "Thieu thu vien reportlab. Chay: pip install reportlab", nhi_phan=True)

    if not any((args.json, args.csv, args.md, args.xlsx, args.pdf)):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    if hong:
        _print("Khong xuat duoc: " + ", ".join(hong))
        return 1
    return 0


def _make_notifier(cfg: dict) -> MultiNotifier:
    return tu_cau_hinh(cfg.get("notify"))


def cmd_notify(args, store: Store, cfg: dict) -> int:
    warn, crit = int(cfg["warn_days"]), int(cfg["critical_days"])
    bot = _make_notifier(cfg)

    if args.reset:
        removed = store.clear_alerts()
        _print(f"Da xoa {removed} ban ghi chong trung. Lan chay toi se bao lai tu dau.")
        return 0

    if args.test:
        if not bot.configured:
            _print("Chua cau hinh kenh nao (Telegram hoac Zalo) trong config.json.")
            return 1
        result = bot.send_test()
        for k in result.get("kenh") or []:
            if k["ok"]:
                _print(f"  {k['kenh']}: da gui tin thu qua {k.get('bot') or 'bot'}")
            else:
                _print(f"  {k['kenh']}: THAT BAI - {k.get('description')}")
        return 0 if result.get("ok") else 1

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
        _print("Chua cau hinh kenh nao (Telegram hoac Zalo) trong config.json.")
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


def cmd_matkhau(args, store: Store, cfg: dict) -> int:
    """Tao hoac doi tai khoan dang nhap dashboard.

    Mat khau khong bao gio di qua tham so dong lenh: `ps` tren may nhieu nguoi
    dung se thay no, va shell con ghi lai vao lich su. Hoi qua getpass, hoac
    sinh ngau nhien voi --sinh.
    """
    import getpass

    from gateway import auth

    email = (args.email or "").strip()
    if "@" not in email:
        _print("Email khong hop le.")
        return 2

    if args.sinh:
        mat_khau = auth.sinh_mat_khau()
    else:
        try:
            mat_khau = getpass.getpass("Mat khau moi: ")
            lai = getpass.getpass("Nhap lai:     ")
        except (EOFError, KeyboardInterrupt):
            _print("")
            _print("Da huy.")
            return 1
        if mat_khau != lai:
            _print("Hai lan nhap khong khop.")
            return 1
        if len(mat_khau) < 10:
            _print("Mat khau phai tu 10 ky tu tro len.")
            return 1

    duong_dan = args.config or cfgmod.CONFIG_PATH
    cfgmod.save({"admin_email": email, "admin_password_hash": auth.bam(mat_khau)},
                duong_dan)
    _print(f"Da dat tai khoan: {email}")
    if args.sinh:
        # In mot lan duy nhat. Ban bam la mot chieu, khong doc nguoc ra duoc.
        _print(f"Mat khau: {mat_khau}")
        _print("Chep ngay - khong xem lai duoc.")
    _print("Nhung phien dang mo van con hieu luc; dung `--dang-xuat-het` de cat het.")
    if args.dang_xuat_het:
        cfgmod.save({"secret_key": auth.sinh_secret_key()}, duong_dan)
        _print("Da doi khoa phien: moi nguoi phai dang nhap lai.")
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

    p = sub.add_parser("notify", help="Gui canh bao het han qua Telegram / Zalo")
    p.add_argument("--dry-run", action="store_true", help="Chi in ra man hinh, khong gui")
    p.add_argument("--test", action="store_true", help="Gui tin nhan kiem tra ket noi")
    p.add_argument("--force", action="store_true", help="Gui lai ca nhung canh bao da bao roi")
    p.add_argument("--reset", action="store_true", help="Xoa lich su chong trung")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("cloudflare", help="Doc trang thai zone tu Cloudflare")
    p.add_argument("--verify", action="store_true",
                   help="Chi kiem tra token, khong doc zone nao")
    p.set_defaults(func=cmd_cloudflare)

    p = sub.add_parser("matkhau", help="Tao / doi tai khoan dang nhap dashboard")
    p.add_argument("email", help="Email dung de dang nhap")
    p.add_argument("--sinh", action="store_true",
                   help="Sinh mat khau ngau nhien va in ra mot lan")
    p.add_argument("--dang-xuat-het", action="store_true",
                   help="Doi luon khoa phien: moi phien dang mo bi cat")
    p.set_defaults(func=cmd_matkhau)

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
