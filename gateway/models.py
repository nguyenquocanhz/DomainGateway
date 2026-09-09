"""Kieu du lieu chuan hoa cho mot ban ghi ten mien."""

from __future__ import annotations

import dataclasses
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
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
    def locked(self) -> bool:
        return any("transferprohibited" in s.lower().replace(" ", "") for s in self.epp_status)

    def to_dict(self, warn: int = DEFAULT_WARN_DAYS, critical: int = DEFAULT_CRITICAL_DAYS) -> dict:
        data = dataclasses.asdict(self)
        for key in ("created_at", "updated_at", "expires_at", "checked_at",
                    "cf_checked_at"):
            data[key] = iso(getattr(self, key))
        data["cf_ket_luan"] = self.cf_ket_luan
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
                    "cf_checked_at"):
            if key in clean:
                clean[key] = parse_dt(clean[key])
        for key in ("nameservers", "epp_status", "tags"):
            if clean.get(key) is None:
                clean[key] = []
        return cls(**clean)
