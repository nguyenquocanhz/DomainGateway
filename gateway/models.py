"""Kieu du lieu chuan hoa cho mot ban ghi ten mien."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Nguong canh bao (ngay) - co the ghi de qua config.json
DEFAULT_WARN_DAYS = 30
DEFAULT_CRITICAL_DAYS = 7

STATUS_ACTIVE = "active"
STATUS_EXPIRING = "expiring"
STATUS_CRITICAL = "critical"
STATUS_EXPIRED = "expired"
STATUS_UNKNOWN = "unknown"

STATUS_LABEL = {
    STATUS_ACTIVE: "Active",
    STATUS_EXPIRING: "Expiring soon",
    STATUS_CRITICAL: "Critical",
    STATUS_EXPIRED: "Expired",
    STATUS_UNKNOWN: "Unknown",
}


# Ky tu dieu khien khong hop le trong XML 1.0 (giu \t \n \r). openpyxl tu
# choi chung bang IllegalCharacterError, con reportlab thi nuot im lang.
_DIEU_KHIEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# JSON long qua sau lam lam_sach de quy sap ngan xep. json.loads cua Python
# 3.14 khong con tu chan do sau, va mot than 7 KB du de long 1.200 tang.
SAU_TOI_DA = 80


def lam_sach(gia_tri, _sau: int = 0):
    """Bo surrogate lac va ky tu dieu khien khoi moi chuoi trong cau truc JSON.

    `json.loads` cua Python NHAN escape `\\ud800` va tra ve mot str chua nua
    cap surrogate. Khong dinh gi cho toi luc encode: sqlite tu choi binding,
    jsonify (ensure_ascii=False) tu choi tra loi, csv/markdown tu choi ghi.
    Loi nem ra o tan tang duoi cung, thanh 500 - hoac te hon, thoat han ra
    ngoai khi dang xuat file va lam hong ca luot xuat.

    Ky tu dieu khien di lot phep thu encode - chung la UTF-8 hop le - nhung
    openpyxl tu choi ghi chung, nen mot ky tu nhu vay trong ghi chu lam MOI
    lan xuat XLSX ve sau deu hong. Cung co che voi surrogate: vao im lang, no
    o tang xuat. Nen don chung o cung mot cua.

    Don ngay tai cua vao thi moi tang duoi khoi phai biet toi chuyen nay.
    Nem ValueError khi long qua sau; nguoi goi doi thanh 400.
    """
    if _sau > SAU_TOI_DA:
        raise ValueError("Dữ liệu JSON lồng quá sâu")
    if isinstance(gia_tri, str):
        # Duong tat: chuoi sach (tuyet dai da so) chi ton mot phep quet
        try:
            gia_tri.encode("utf-8")
        except UnicodeEncodeError:
            gia_tri = gia_tri.encode("utf-8", "replace").decode("utf-8")
        return _DIEU_KHIEN.sub("", gia_tri)
    if isinstance(gia_tri, dict):
        return {lam_sach(k, _sau + 1): lam_sach(v, _sau + 1)
                for k, v in gia_tri.items()}
    if isinstance(gia_tri, list):
        return [lam_sach(x, _sau + 1) for x in gia_tri]
    return gia_tri


# Cac truong do REGISTRY dien. Khong dung cho provider/tags/note - do la
# truong nguoi dung, di duong khac (_than_json / cli import).
_TRUONG_CHUOI = ("domain", "tld", "registrar", "registrant", "source", "error")
_TRUONG_DANH_SACH = ("nameservers", "epp_status")


# Truong tro thanh KHOA CHINH. Phai toi normalize_domain nguyen ven de no
# con tu choi duoc - don am tham la bien mot chuoi rac thanh khoa cua mot ten
# mien KHAC dang co that.
KHOA_CHINH = ("domain", "domains")


def lam_sach_giu_khoa(gia_tri, _sau: int = 0):
    """Nhu lam_sach() nhung giu nguyen moi truong ten trong KHOA_CHINH.

    Dung o CA HAI cua nhan du lieu ngoai: than JSON cua HTTP va file cua
    `cli.py import`. Truoc day chi HTTP co ngoai le nay, nen lo ghi de van con
    nguyen qua duong import - dung cai lo ma commit truoc lay lam tieu de.
    """
    if _sau > SAU_TOI_DA:
        raise ValueError("Dữ liệu JSON lồng quá sâu")
    if isinstance(gia_tri, dict):
        return {lam_sach(k, _sau + 1): (v if k in KHOA_CHINH
                                        else lam_sach_giu_khoa(v, _sau + 1))
                for k, v in gia_tri.items()}
    if isinstance(gia_tri, list):
        return [lam_sach_giu_khoa(x, _sau + 1) for x in gia_tri]
    return lam_sach(gia_tri, _sau)


# Truong tro thanh KHOA CHINH. Phai toi normalize_domain nguyen ven de no
# con tu choi duoc - don am tham la bien mot chuoi rac thanh khoa cua mot ten
# mien KHAC dang co that.
KHOA_CHINH = ("domain", "domains")


def lam_sach_giu_khoa(gia_tri, _sau: int = 0):
    """Nhu lam_sach() nhung giu nguyen moi truong ten trong KHOA_CHINH.

    Dung o CA HAI cua nhan du lieu ngoai: than JSON cua HTTP va file cua
    `cli.py import`. Truoc day chi HTTP co ngoai le nay, nen lo ghi de van con
    nguyen qua duong import - dung cai lo ma commit truoc lay lam tieu de.
    """
    if _sau > SAU_TOI_DA:
        raise ValueError("Dữ liệu JSON lồng quá sâu")
    if isinstance(gia_tri, dict):
        return {lam_sach(k, _sau + 1): (v if k in KHOA_CHINH
                                        else lam_sach_giu_khoa(v, _sau + 1))
                for k, v in gia_tri.items()}
    if isinstance(gia_tri, list):
        return [lam_sach_giu_khoa(x, _sau + 1) for x in gia_tri]
    return lam_sach(gia_tri, _sau)


def lam_sach_ban_ghi(rec):
    """Don mot DomainRecord vua dung tu phan hoi cua may chu ben thu ba.

    RDAP/WHOIS/BKNS deu la du lieu ngoai y het than JSON cua HTTP: `resp.json()`
    nhan escape \\ud800 y het, roi chuoi do di thang vao sqlite va jsonify.
    /api/lookup tra ban ghi ra ma khong luu, nen don o tang store thoi la khong
    du - phai don ngay khi ban ghi vua duoc dung.

    Sua tai cho: ban ghi vua tao xong, chua ai giu tham chieu khac.
    """
    for ten in _TRUONG_CHUOI:
        gt = getattr(rec, ten, None)
        if isinstance(gt, str):
            setattr(rec, ten, lam_sach(gt))
    for ten in _TRUONG_DANH_SACH:
        gt = getattr(rec, ten, None)
        if isinstance(gt, list):
            setattr(rec, ten, [lam_sach(x) if isinstance(x, str) else x for x in gt])
    return rec


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value) -> datetime | None:
    """Nhan ISO-8601 (co/khong Z, co/khong microsecond) hoac datetime -> aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%Y.%m.%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    try:
        # "9999-12-31T23:59:59-01:00" doc duoc, nhung doi sang UTC thi vuot nam
        # 9999: iso() no OverflowError luc ghi kho va ca lan dong bo ra 500. Ngay
        # nhu vay chi den tu du lieu nguoi la (WHMCS, WHOIS) - coi nhu khong co.
        dt.astimezone(timezone.utc)
    except OverflowError:
        return None
    # Windows khong doi duoc sang gio may ngoai 1970..3000 (OSError errno 22),
    # ma tong quan, xuat Markdown/XLSX/PDF va `cli list` deu doi - mot ngay
    # 9999-12-31 trong kho la hong ca ba cho moi lan goi. Ten mien co tu 1985,
    # dang ky toi da 10 nam: ngoai khoang nay chac chan la du lieu rac.
    if not 1971 <= dt.year <= 2999:
        return None
    return dt


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


@dataclass
class DomainRecord:
    """Mot ten mien sau khi da chuan hoa tu bat ky nguon nao (RDAP/WHOIS/API/manual)."""

    domain: str
    tld: str = ""
    registrar: str = ""          # nha dang ky thuc te doc tu registry
    provider: str = ""           # nha cung cap ban mua (co the khac registrar)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    nameservers: list[str] = field(default_factory=list)
    epp_status: list[str] = field(default_factory=list)
    dnssec: bool = False
    registrant: str = ""
    auto_renew: bool | None = None
    tags: list[str] = field(default_factory=list)
    note: str = ""
    source: str = ""             # rdap | bkns | whois43 | manual | provider-api
    checked_at: datetime | None = None
    error: str = ""

    # ---- trang thai zone o Cloudflare ---------------------------------------
    # Nhom rieng vi tra loi mot cau khac han registry: khong phai "con han den
    # bao gio" ma "co dang phuc vu gi khong". Refresh registry khong duoc dung
    # toi, va nguoc lai (xem CF_FIELDS trong store.py).
    cf_status: str = ""          # active | pending | moved | "" neu khong thay
    cf_records: int | None = None    # so ban ghi A/AAAA/CNAME
    cf_proxied: int | None = None    # trong so do, bao nhieu cai bat proxy
    cf_paused: bool = False      # zone bat nhung chu so huu da tam dung
    cf_checked_at: datetime | None = None

    # ---- doi chieu voi WHMCS -------------------------------------------------
    # Nhom thu ba, cung nguyen tac: WHMCS la so sach cua nguoi ban, registry la
    # su that. Luu rieng de doi chieu, khong ben nao ghi de len ben nao (xem
    # WHMCS_FIELDS trong store.py).
    whmcs_expiry: datetime | None = None
    whmcs_nextdue: datetime | None = None
    whmcs_status: str = ""           # Active | Expired | Transferred Away... | "" neu khong co
    whmcs_registrar: str = ""
    whmcs_checked_at: datetime | None = None

    # ---- thuoc tinh dan xuat -------------------------------------------------
    @property
    def days_left(self) -> int | None:
        if not self.expires_at:
            return None
        delta = self.expires_at - utcnow()
        return int(delta.total_seconds() // 86400)

    def status(self, warn: int = DEFAULT_WARN_DAYS, critical: int = DEFAULT_CRITICAL_DAYS) -> str:
        d = self.days_left
        if d is None:
            return STATUS_UNKNOWN
        if d < 0:
            return STATUS_EXPIRED
        if d <= critical:
            return STATUS_CRITICAL
        if d <= warn:
            return STATUS_EXPIRING
        return STATUS_ACTIVE

    @property
    def cf_ket_luan(self) -> str:
        """Ket luan ngan ve zone. "" nghia la chua quet Cloudflare bao gio.

        Gia tri dang chu y nhat la "khong-ban-ghi": ten mien con han, zone
        bat, nhung khong co ban ghi nao tro toi dau ca -> website tat.
        """
        if not self.cf_checked_at:
            return ""
        if not self.cf_status:
            return "khong-thay"
        if self.cf_status != "active":
            return "zone-" + self.cf_status
        # Zone tam dung: DNS van tra loi nhung Cloudflare khong dung truoc
        # nua. Khac han "khong co ban ghi", nen phai la ket luan rieng.
        if self.cf_paused:
            return "tam-dung"
        if self.cf_records is None:
            return "thieu-quyen-dns"
        return "ok" if self.cf_records else "khong-ban-ghi"

    @property
    def whmcs_lech_ngay(self) -> int | None:
        """Ngay het han registry tru WHMCS. Am: registry het han SOM hon WHMCS nghi."""
        if not self.whmcs_expiry or not self.expires_at:
            return None
        return (self.expires_at.date() - self.whmcs_expiry.date()).days

    @property
    def whmcs_ket_luan(self) -> str:
        """Ket luan doi chieu WHMCS voi registry. "" = chua dong bo bao gio.

        Theo muc do dang lo:
          het-ma-active  WHMCS con de Active nhung registry da het han
          hoa-don-tre    hoa don gia han den SAU ngay het han that - ten mien het
                         truoc khi khach kip bi thu tien
          lech           hai ngay het han lech nhau qua 1 ngay
          chua-ro        mot ben chua co ngay het han
          khong-thay     da dong bo, ten mien khong co trong WHMCS
          khop           lech trong vong 1 ngay - WHMCS luu NGAY khong kem gio,
                         registry luu gio UTC, nen lech mot ngay la mui gio
        """
        if not self.whmcs_checked_at:
            return ""
        if not self.whmcs_status:
            return "khong-thay"
        dang_active = self.whmcs_status.lower() == "active"
        con = self.days_left
        if dang_active and con is not None and con < 0:
            return "het-ma-active"
        # "That" la ngay cua registry. Nhung hoa don nam tren lich WHMCS (ngay
        # tran, gio dia phuong) con registry la gio UTC - so thang thi phai doan
        # mui gio: bao nham khi WHMCS o VN, lot khi WHMCS o My.
        # Khi hai ngay het han KHOP (lech <= 1 ngay chinh la mui gio), ngay het
        # han cua WHMCS dai dien cho ngay that tren lich WHMCS - so hoa don voi
        # no, khong phai doan gi. Khi chung LECH, ngay WHMCS khong con dai dien
        # duoc: WHMCS chua cap nhat lan gia han thi hoa don sau ngay WHMCS la
        # binh thuong, WHMCS tuong con han lau hon thi hoa don truoc ngay WHMCS
        # van co the sau ngay that. Luc do phai so voi registry, dung sai 1 ngay.
        if dang_active and self.whmcs_nextdue:
            lech = self.whmcs_lech_ngay
            if lech is not None and abs(lech) <= 1:
                tre = self.whmcs_nextdue.date() > self.whmcs_expiry.date()
            elif self.expires_at:
                tre = (self.whmcs_nextdue.date() - self.expires_at.date()).days > 1
            else:
                tre = False
            if tre:
                return "hoa-don-tre"
        lech = self.whmcs_lech_ngay
        if lech is None:
            return "chua-ro"
        return "khop" if abs(lech) <= 1 else "lech"

    @property
    def locked(self) -> bool:
        return any("transferprohibited" in s.lower().replace(" ", "") for s in self.epp_status)

    def to_dict(self, warn: int = DEFAULT_WARN_DAYS, critical: int = DEFAULT_CRITICAL_DAYS) -> dict:
        data = dataclasses.asdict(self)
        for key in ("created_at", "updated_at", "expires_at", "checked_at",
                    "cf_checked_at", "whmcs_expiry", "whmcs_nextdue", "whmcs_checked_at"):
            data[key] = iso(getattr(self, key))
        data["cf_ket_luan"] = self.cf_ket_luan
        data["whmcs_ket_luan"] = self.whmcs_ket_luan
        data["whmcs_lech_ngay"] = self.whmcs_lech_ngay
        data["days_left"] = self.days_left
        data["status"] = self.status(warn, critical)
        data["status_label"] = STATUS_LABEL[data["status"]]
        data["locked"] = self.locked
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "DomainRecord":
        known = {f.name for f in dataclasses.fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        for key in ("created_at", "updated_at", "expires_at", "checked_at",
                    "cf_checked_at", "whmcs_expiry", "whmcs_nextdue", "whmcs_checked_at"):
            if key in clean:
                clean[key] = parse_dt(clean[key])
        for key in ("nameservers", "epp_status", "tags"):
            if clean.get(key) is None:
                clean[key] = []
        return cls(**clean)
