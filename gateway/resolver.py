"""Engine tra cuu ten mien da nguon.

Thu tu uu tien (fallback chain):
  1. manual   - nguoi dung tu nhap ngay het han (uu tien tuyet doi neu pinned)
  2. bkns     - API WHOIS Viet Nam cho .vn / .io.vn / .id.vn / .com.vn ...
  3. rdap     - chuan RDAP cho gTLD va phan lon ccTLD (mien phi, khong can key)
  4. whois43  - WHOIS thuan port 43, tu kham pha server qua whois.iana.org

Moi nguon deu tra ve DomainRecord da chuan hoa nen tang tren khong can biet
du lieu den tu dau.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .models import DomainRecord, lam_sach, lam_sach_ban_ghi, parse_dt, utcnow

USER_AGENT = "DomainGateway/1.0"
RDAP_BOOTSTRAP = "https://rdap.org/domain/{domain}"
BKNS_ENDPOINT = "https://whois.bkns.vn/api/v1/whois"
IANA_WHOIS = "whois.iana.org"

# Cac hau to do VNNIC quan ly -> bat buoc di duong BKNS/VNNIC
VN_SUFFIXES = (
    ".vn", ".com.vn", ".net.vn", ".org.vn", ".edu.vn", ".gov.vn", ".biz.vn",
    ".info.vn", ".pro.vn", ".health.vn", ".name.vn", ".io.vn", ".id.vn", ".ai.vn",
)

# WHOIS server cho cac TLD ma IANA khong khai bao hoac tra ve rong
WHOIS_OVERRIDES = {
    "vn": "whois.vnnic.vn",
    "eu": "whois.eu",
    "mm": "whois.registry.gov.mm",
    "com.mm": "whois.registry.gov.mm",
}


class RateLimiter:
    """Token bucket don gian, thread-safe. rpm <= 0 nghia la khong gioi han."""

    def __init__(self, rpm: int, safety: float = 1.0):
        # safety > 1 giu khoang cach rong hon muc toi thieu, tranh dinh 429 o bien cua so
        self.interval = (60.0 / rpm) * safety if rpm and rpm > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next = now + self.interval


# --------------------------------------------------------------------------- #
# Tien ich chung
# --------------------------------------------------------------------------- #
def split_tld(domain: str) -> str:
    """Tra ve hau to dai nhat khop VN_SUFFIXES, nguoc lai lay label cuoi cung."""
    d = domain.lower().strip().rstrip(".")
    for suffix in sorted(VN_SUFFIXES, key=len, reverse=True):
        if d.endswith(suffix):
            return suffix.lstrip(".")
    return d.rsplit(".", 1)[-1] if "." in d else d


def is_vn_domain(domain: str) -> bool:
    d = domain.lower().strip().rstrip(".")
    return any(d.endswith(s) for s in VN_SUFFIXES)


def normalize_domain(raw: str) -> str:
    """Chap nhan URL day du, dau cham cuoi, IDN tieng Viet -> tra ve punycode.

    Day la cua chung cua MOI chuoi ten mien: query string cua /api/lookup, than
    JSON, tham so dong lenh, file .txt cua `cli.py import`. Nen don o day thi
    khong con duong nao dua surrogate lac hay ky tu dieu khien vao khoa chinh
    cua bang - `.encode("idna")` ben duoi nem UnicodeError voi chung roi bi bat
    im, nen chung di lot nguyen ven.
    """
    d = lam_sach((raw or "")).strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/")[0].split("?")[0].split("#")[0]
    d = d.split("@")[-1].split(":")[0]
    d = d.rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    try:
        d = d.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        pass
    return d


# --------------------------------------------------------------------------- #
# Nguon 1: RDAP
# --------------------------------------------------------------------------- #
def _rdap_entities(payload: dict) -> tuple[str, str]:
    """Boc ten registrar va registrant tu mang entities/vcardArray."""
    registrar = registrant = ""
    for entity in payload.get("entities") or []:
        roles = [str(r).lower() for r in entity.get("roles") or []]
        vcard = entity.get("vcardArray") or [None, []]
        name = ""
        fields = vcard[1] if len(vcard) > 1 and isinstance(vcard[1], list) else []
        for item in fields:
            if isinstance(item, list) and len(item) >= 4 and item[0] == "fn":
                name = str(item[3])
        if "registrar" in roles and not registrar:
            registrar = name
        if "registrant" in roles and not registrant:
            registrant = name
    return registrar, registrant


MAX_CHUYEN_HUONG = 5


def _get_kiem_tung_chang(url: str, headers: dict, timeout: int):
    """GET nhung tu di theo chuyen huong, kiem dia chi truoc MOI chang.

    rdap.org la dich vu bootstrap: no chuyen huong sang may chu RDAP cua tung
    registry, nen bat buoc phai di theo. Nhung `allow_redirects=True` nghia la
    dat tron niem tin vao no - noi do bi chiem thi no chi duoc ung dung di toi
    dau tuy y, ke ca 127.0.0.1 hay 169.254.169.254. Tu di thi chan duoc.
    """
    for _ in range(MAX_CHUYEN_HUONG + 1):
        chu = urlparse(url)
        if chu.scheme not in ("http", "https"):
            raise requests.RequestException(f"scheme khong duoc phep: {chu.scheme}")
        if _tro_vao_mang_noi_bo(chu.hostname or "", chu.port or (443 if chu.scheme == "https" else 80)):
            raise requests.RequestException(f"dia chi noi bo: {chu.hostname}")

        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        tiep = resp.headers.get("Location")
        if not tiep:
            return resp
        url = urljoin(url, tiep)
    raise requests.RequestException(f"qua {MAX_CHUYEN_HUONG} lan chuyen huong")


def fetch_rdap(domain: str, timeout: int = 25) -> DomainRecord:
    rec = DomainRecord(domain=domain, tld=split_tld(domain), source="rdap", checked_at=utcnow())
    try:
        resp = _get_kiem_tung_chang(
            RDAP_BOOTSTRAP.format(domain=domain),
            {"Accept": "application/rdap+json", "User-Agent": USER_AGENT},
            timeout,
        )
    except requests.RequestException as exc:
        rec.error = "rdap: " + (str(exc) if str(exc) else type(exc).__name__)
        return rec

    if resp.status_code == 404:
        rec.error = "rdap: khong tim thay (co the ten mien chua duoc dang ky)"
        return rec
    if resp.status_code != 200:
        rec.error = "rdap: HTTP " + str(resp.status_code)
        return rec
    try:
        payload = resp.json()
    except ValueError:
        rec.error = "rdap: phan hoi khong phai JSON"
        return rec

    events = {
        str(e.get("eventAction", "")).lower(): e.get("eventDate")
        for e in payload.get("events") or []
    }
    rec.created_at = parse_dt(events.get("registration"))
    rec.updated_at = parse_dt(events.get("last changed"))
    rec.expires_at = parse_dt(events.get("expiration"))
    rec.registrar, rec.registrant = _rdap_entities(payload)
    rec.nameservers = sorted(
        {str(ns.get("ldhName", "")).lower() for ns in payload.get("nameservers") or [] if ns.get("ldhName")}
    )
    rec.epp_status = [str(s) for s in payload.get("status") or []]
    secure = payload.get("secureDNS") or {}
    rec.dnssec = bool(secure.get("delegationSigned") or secure.get("zoneSigned"))
    if not rec.expires_at:
        rec.error = "rdap: thieu truong expiration"
    return rec


# --------------------------------------------------------------------------- #
# Nguon 2: BKNS WHOIS API (Viet Nam / VNNIC)
# --------------------------------------------------------------------------- #
def fetch_bkns(domain: str, api_key: str, timeout: int = 30, retries: int = 2,
               backoff: float = 32.0) -> DomainRecord:
    rec = DomainRecord(domain=domain, tld=split_tld(domain), source="bkns", checked_at=utcnow())
    if not api_key:
        rec.error = "bkns: chua cau hinh api key"
        return rec

    resp = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                BKNS_ENDPOINT,
                params={"domain": domain},
                headers={"X-API-Key": api_key, "User-Agent": USER_AGENT},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            rec.error = "bkns: " + type(exc).__name__
            return rec
        if resp.status_code != 429 or attempt == retries:
            break
        # Ton trong Retry-After neu server co gui, nguoc lai lui theo cap so nhan
        try:
            wait = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            wait = backoff * (attempt + 1)
        time.sleep(min(max(wait, 1.0), 120.0))

    if resp.status_code == 429:
        rec.error = "bkns: vuot rate limit (demo key = 2 req/phut, 200 req/ngay)"
        return rec
    if resp.status_code != 200:
        rec.error = "bkns: HTTP " + str(resp.status_code)
        return rec
    try:
        payload = resp.json()
    except ValueError:
        rec.error = "bkns: phan hoi khong phai JSON"
        return rec

    if payload.get("status") == "available":
        rec.error = "bkns: ten mien chua duoc dang ky"
        return rec

    data = payload.get("data") or {}
    dates = data.get("dates") or {}
    rec.created_at = parse_dt(dates.get("created"))
    rec.updated_at = parse_dt(dates.get("updated"))
    rec.expires_at = parse_dt(dates.get("expiry") or dates.get("renewalDeadline"))
    rec.registrar = ((data.get("registrar") or {}).get("name") or "").strip()
    rec.registrant = ((data.get("registrant") or {}).get("name") or "").strip()
    rec.nameservers = sorted({str(n).lower() for n in data.get("nameservers") or []})
    rec.epp_status = [str(s) for s in data.get("domainStatus") or []]
    rec.dnssec = bool(data.get("dnssec"))
    if not rec.expires_at:
        rec.error = "bkns: thieu ngay het han"
    return rec


# --------------------------------------------------------------------------- #
# Nguon 3: WHOIS port 43
# --------------------------------------------------------------------------- #
def _tro_vao_mang_noi_bo(host: str, cong: int = 43) -> bool:
    """Ten may nay co phan giai ra dia chi noi bo khong?

    Can vi may chu WHOIS ke tiep duoc LAY TU chinh phan hoi WHOIS truoc do (dong
    "Registrar WHOIS Server:"). Ai dang ky ten mien cung anh huong duoc gia tri
    do, va WHOIS cong 43 chay plaintext nen nguoi dung giua cung sua duoc. Khong
    chan thi thanh ra quet cong 43 trong mang noi bo tu may nguoi dung, roi noi
    dung may chu noi bo tra ve chay tiep vao giao dien lan file xuat.
    """
    try:
        thong_tin = socket.getaddrinfo(host, cong, proto=socket.IPPROTO_TCP)
    except OSError:
        return True                 # khong phan giai duoc thi coi nhu khong an toan
    for *_, addr in thong_tin:
        try:
            ip = ipaddress.ip_address(addr[0])
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def whois_query(server: str, query: str, timeout: int = 20, limit: int = 200000) -> str:
    if _tro_vao_mang_noi_bo(server):
        raise OSError(f"whois: tu choi may chu tro vao mang noi bo: {server}")
    sock = socket.create_connection((server, 43), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall((query + "\r\n").encode("utf-8", "ignore"))
        chunks = []
        total = 0
        while True:
            data = sock.recv(8192)
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if total > limit:
                break
        return b"".join(chunks).decode("utf-8", "replace")
    finally:
        sock.close()


def discover_whois_server(tld: str, timeout: int = 20) -> str:
    if tld in WHOIS_OVERRIDES:
        return WHOIS_OVERRIDES[tld]
    root = tld.rsplit(".", 1)[-1]
    if root in WHOIS_OVERRIDES:
        return WHOIS_OVERRIDES[root]
    try:
        text = whois_query(IANA_WHOIS, root, timeout=timeout)
    except OSError:
        return ""
    match = re.search(r"^whois:\s*(\S+)\s*$", text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


_WHOIS_FIELDS = {
    "expires_at": [
        r"registry expiry date", r"registrar registration expiration date",
        r"expiration date", r"expiry date", r"expire date", r"expires on",
        r"expires", r"paid-till", r"renewal date",
    ],
    "created_at": [r"creation date", r"created on", r"created", r"registered on", r"registration time"],
    "updated_at": [r"updated date", r"last updated", r"last modified", r"changed"],
    "registrar": [r"sponsoring registrar", r"registrar name", r"registrar"],
}


def _whois_grab(text: str, patterns: list) -> str:
    # Dung [ \t] chu khong dung \s: \s an ca xuong dong nen mot nhan rong
    # ("Registrar:") se nuot gia tri cua dong ke tiep.
    for pattern in patterns:
        match = re.search(r"^[ \t]*" + pattern + r"[ \t]*[:\.][ \t]*(.+?)[ \t]*$",
                          text, re.MULTILINE | re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return ""


def parse_whois(text: str) -> dict:
    out = {}
    for field, patterns in _WHOIS_FIELDS.items():
        value = _whois_grab(text, patterns)
        if not value:
            continue
        out[field] = parse_dt(value) if field.endswith("_at") else value
    # EURid (.eu) va vai registry khac ghi registrar theo khoi thut le:
    #   Registrar:
    #           Name: Ten Cong Ty
    if not out.get("registrar"):
        block = re.search(r"^[ \t]*registrar[ \t]*:[ \t]*$\s*^[ \t]+name[ \t]*:[ \t]*(.+?)[ \t]*$",
                          text, re.MULTILINE | re.IGNORECASE)
        if block:
            out["registrar"] = block.group(1).strip()

    ns = re.findall(r"^[ \t]*(?:name server|nserver|nameserver)[ \t]*[:\.][ \t]*(\S+)",
                    text, re.MULTILINE | re.IGNORECASE)
    out["nameservers"] = sorted({n.lower().rstrip(".") for n in ns})
    status = re.findall(r"^[ \t]*(?:domain status|status)[ \t]*[:\.][ \t]*(\S+)",
                        text, re.MULTILINE | re.IGNORECASE)
    filtered = [s for s in status if s.lower() not in ("ok", "active", "connect")]
    out["epp_status"] = filtered or status
    out["dnssec"] = bool(re.search(r"^[ \t]*dnssec[ \t]*[:\.][ \t]*(signed|yes|true)",
                                   text, re.MULTILINE | re.IGNORECASE))
    return out


def fetch_whois43(domain: str, timeout: int = 20) -> DomainRecord:
    tld = split_tld(domain)
    rec = DomainRecord(domain=domain, tld=tld, source="whois43", checked_at=utcnow())
    server = discover_whois_server(tld, timeout=timeout)
    if not server:
        rec.error = "whois43: khong tim duoc whois server cho ." + tld
        return rec
    try:
        text = whois_query(server, domain, timeout=timeout)
    except OSError as exc:
        rec.error = "whois43: " + type(exc).__name__ + " khi noi toi " + server
        return rec

    # Mot so registry chi tra ve con tro toi WHOIS cua registrar -> di them 1 hop
    referral = re.search(r"^\s*registrar whois server\s*[:\.]\s*(\S+)",
                         text, re.MULTILINE | re.IGNORECASE)
    if referral:
        try:
            deep = whois_query(referral.group(1).strip(), domain, timeout=timeout)
            if len(deep) > 120:
                text = deep + "\n" + text
        except OSError:
            pass

    parsed = parse_whois(text)
    rec.created_at = parsed.get("created_at")
    rec.updated_at = parsed.get("updated_at")
    rec.expires_at = parsed.get("expires_at")
    rec.registrar = parsed.get("registrar", "")
    rec.nameservers = parsed.get("nameservers", [])
    rec.epp_status = parsed.get("epp_status", [])
    rec.dnssec = parsed.get("dnssec", False)
    if not rec.expires_at:
        rec.error = "whois43: khong doc duoc ngay het han tu " + server
    return rec


# --------------------------------------------------------------------------- #
# Bo dieu phoi
# --------------------------------------------------------------------------- #
class Resolver:
    """Chay chuoi fallback va gop ket qua tot nhat cho tung ten mien."""

    def __init__(self, bkns_api_key: str = "", bkns_rpm: int = 2,
                 rdap_rpm: int = 0, timeout: int = 25, workers: int = 8):
        self.bkns_api_key = bkns_api_key
        self.timeout = timeout
        self.workers = max(1, workers)
        self._limits = {
            "bkns": RateLimiter(bkns_rpm, safety=1.2),
            "rdap": RateLimiter(rdap_rpm),
            "whois43": RateLimiter(0),
        }

    def _chain(self, domain: str) -> list:
        if is_vn_domain(domain):
            return ["bkns", "whois43", "rdap"]
        return ["rdap", "whois43"]

    def _run(self, source: str, domain: str) -> DomainRecord:
        """Cua duy nhat ma du lieu tu ca ba nguon mang di qua.

        Don o day chu khong o tung ham fetch_*: mot nguon moi them sau nay se
        tu duoc don ma khong phai nho.
        """
        self._limits[source].acquire()
        if source == "bkns":
            rec = fetch_bkns(domain, self.bkns_api_key, timeout=self.timeout)
        elif source == "rdap":
            rec = fetch_rdap(domain, timeout=self.timeout)
        else:
            rec = fetch_whois43(domain, timeout=self.timeout)
        return lam_sach_ban_ghi(rec)

    def lookup(self, domain: str) -> DomainRecord:
        """Tra ve ban ghi dau tien co expires_at. Neu tat ca that bai thi tra ve
        ban ghi giau thong tin nhat kem thong bao loi gop lai."""
        domain = normalize_domain(domain)
        attempts = []
        for source in self._chain(domain):
            rec = self._run(source, domain)
            attempts.append(rec)
            if rec.expires_at:
                return rec
        if attempts:
            best = max(attempts, key=lambda r: (bool(r.registrar), len(r.nameservers)))
        else:
            best = DomainRecord(domain=domain, tld=split_tld(domain), checked_at=utcnow())
        best.error = " | ".join(dict.fromkeys(a.error for a in attempts if a.error))
        return best

    def lookup_many(self, domains, progress=None) -> list:
        """Tra cuu song song. progress(done, total, record) goi sau moi ten mien."""
        targets = [normalize_domain(d) for d in domains if d and str(d).strip()]
        results = []
        total = len(targets)
        if not total:
            return results
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.lookup, d): d for d in targets}
            for done, future in enumerate(as_completed(futures), start=1):
                name = futures[future]
                try:
                    rec = future.result()
                except Exception as exc:  # mot ten mien loi khong lam hong ca me
                    rec = DomainRecord(domain=name, tld=split_tld(name), checked_at=utcnow(),
                                       error="resolver: " + type(exc).__name__ + ": " + str(exc))
                results.append(rec)
                if progress:
                    progress(done, total, rec)
        order = {d: i for i, d in enumerate(targets)}
        results.sort(key=lambda r: order.get(r.domain, 999999))
        return results
