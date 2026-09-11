"""Doc so sach ten mien tu WHMCS de doi chieu voi registry. CHI DOC.

WHMCS da luu san ngay het han cho moi ten mien khach mua - nen chi nhap ve thi
khong them duoc gi. Gia tri nam o cho DOI CHIEU: WHMCS ghi mot ngay, registry
ghi ngay khac. Lech la dau hieu dong bo voi nha dang ky hong, ten mien da chuyen
di ma WHMCS con de Active, hoac hoa don ky toi den SAU ngay het han that.

Tai lieu: https://developers.whmcs.com/api-reference/getclientsdomains/
  - POST form toi <url>/includes/api.php, responsetype=json.
  - Xac thuc bang API credential: identifier + secret. Admin role phai co quyen
    "API Access".
  - API mac dinh bi chan theo IP (Setup > General Settings > Security), loi tra
    ve "Invalid IP ...". Access key bo qua duoc lop chan nay.
  - Danh sach nam o domains.domain[], phan trang bang limitstart / limitnum
    (mac dinh 25).
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from .models import lam_sach, parse_dt
from .resolver import normalize_domain

MOI_TRANG = 250     # it request hon mac dinh 25 ma moi trang van nhe
TOI_DA = 20000      # chan vong lap chay mai neu totalresults noi doi

# Khi mot ten mien co nhieu dong trong WHMCS (dong cu da huy + dong moi), giu
# dong nao. So nho hon thang.
_UU_TIEN = {"active": 0, "pending": 1, "pending transfer": 1, "grace": 1, "redemption": 1}


class WhmcsError(Exception):
    """Loi doc duoc cho nguoi dung. KHONG bao gio chua secret."""


def chuan_hoa_url(url: str) -> str:
    """Tra ve URL toi api.php, hoac nem WhmcsError neu khong dung duoc.

    Nhan ca "https://billing.vd.com", ".../whmcs/" lan ".../includes/api.php".
    Bat buoc https: secret di trong than POST, http la gui mat khau quan tri
    WHMCS dang chu tron qua mang.
    """
    u = (url or "").strip()
    if not u:
        raise WhmcsError("Chưa cấu hình URL WHMCS")
    try:
        p = urlparse(u)
        host, _ = p.hostname or "", p.port
    except ValueError:
        # urlparse nem ValueError voi "[" thieu "]", IPv6 sai, ky tu doi nghia sau
        # chuan hoa NFKC; .port nem khi cong khong phai so. De lot ra la 500.
        raise WhmcsError("URL WHMCS không hợp lệ") from None
    if p.scheme != "https" or not p.netloc:
        raise WhmcsError("URL WHMCS phải bắt đầu bằng https:// — secret đi trong nội dung request")
    # "billing..vd.com" hay nhan dai qua 63 ky tu van qua urlparse, toi luc goi
    # moi no. Chan ngay luc luu de nguoi dung biet sai o dau.
    nhan = (host[:-1] if host.endswith(".") else host).split(".")
    if not host or any(not 1 <= len(n) <= 63 for n in nhan):
        raise WhmcsError("Tên máy chủ trong URL WHMCS không hợp lệ")
    duong = p.path.rstrip("/")
    if not duong.endswith("/includes/api.php"):
        duong += "/includes/api.php"
    return f"https://{p.netloc}{duong}"


def _so(gia_tri) -> int | None:
    try:
        return int(gia_tri)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: JSON "1e999" doc ra inf, int(inf) no
        return None


def _danh_sach(domains) -> list:
    """domains.domain[] - nhung WHMCS tra "" khi khong co gi, va co noi tra
    mot dict thay vi danh sach mot phan tu."""
    if isinstance(domains, dict):
        domains = domains.get("domain")
    if isinstance(domains, dict):
        return [domains]
    if isinstance(domains, list):
        return [d for d in domains if isinstance(d, dict)]
    return []


def _muc(d: dict) -> dict | None:
    """Mot dong WHMCS -> muc da chuan hoa, hoac None neu ten mien khong dung duoc.

    `domainname` di THANG vao normalize_domain, KHONG qua lam_sach truoc: don
    truoc thi "a\\x01b.com" thanh "ab.com" - khoa cua mot ten mien KHAC dang co
    trong kho - roi so sach WHMCS ghi de len no. normalize_domain tu choi chuoi
    ban va tra rong. Cac truong con lai moi don.
    """
    ten = normalize_domain(str(d.get("domainname") or ""))
    if not ten or "." not in ten:
        return None
    return {
        "domain": ten,
        # parse_dt tra None cho "0000-00-00" - gia tri WHMCS dung cho ngay trong
        "expiry": parse_dt(lam_sach(str(d.get("expirydate") or ""))),
        "nextdue": parse_dt(lam_sach(str(d.get("nextduedate") or ""))),
        "status": lam_sach(str(d.get("status") or "")),
        "registrar": lam_sach(str(d.get("registrar") or "")),
    }


def _hang(m: dict) -> tuple:
    muon = m["expiry"].timestamp() if m["expiry"] else 0
    return (_UU_TIEN.get(m["status"].lower(), 2), -muon)


class WhmcsClient:
    def __init__(self, url: str, identifier: str, secret: str,
                 accesskey: str = "", timeout: int = 30):
        # str(): config.json sua tay co the ghi so. .strip() tren int no, va vi
        # trang Cai dat cung tao client nen hong ca GET /api/settings.
        self.url = str(url or "").strip()
        self.identifier = str(identifier or "").strip()
        self.secret = str(secret or "").strip()
        self.accesskey = str(accesskey or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.url and self.identifier and self.secret)

    def _goi(self, action: str, **tham_so) -> dict:
        if not self.configured:
            raise WhmcsError("Chưa cấu hình đủ URL, identifier và secret của WHMCS")
        dich = chuan_hoa_url(self.url)
        form = {"action": action, "identifier": self.identifier, "secret": self.secret,
                "responsetype": "json"}
        form.update({k: str(v) for k, v in tham_so.items()})
        if self.accesskey:
            form["accesskey"] = self.accesskey
        try:
            # allow_redirects=False: gap 301/302 thi requests doi POST thanh GET
            # va bo than di - ra mot loi kho hieu. Bao thang de nguoi dung sua URL.
            resp = requests.post(dich, data=form, timeout=self.timeout, allow_redirects=False)
        except (requests.RequestException, ValueError) as exc:
            # ValueError: ten may sai thi urllib3 nem LocationParseError - khong
            # thuoc RequestException, requests cung khong boc lai. URL vao thang
            # tu config.json / DG_WHMCS_URL thi khong qua buoc kiem luc luu.
            # Khong dua str(exc) ra ngoai: mot so loi keo theo ca URL
            raise WhmcsError(f"Không gọi được WHMCS: {type(exc).__name__}") from None
        if 300 <= resp.status_code < 400:
            raise WhmcsError(f"WHMCS chuyển hướng (HTTP {resp.status_code}) — kiểm tra lại URL, "
                             "có thể thiếu hoặc thừa thư mục cài WHMCS")
        try:
            data = resp.json()
        except (ValueError, RecursionError):
            # RecursionError: JSON long qua sau - server la, hoac WHMCS bi chiem
            raise WhmcsError(f"WHMCS trả về HTTP {resp.status_code}, không phải JSON — "
                             "kiểm tra URL có trỏ đúng thư mục cài WHMCS") from None
        if not isinstance(data, dict):
            raise WhmcsError("WHMCS trả về dữ liệu không đúng dạng")
        if data.get("result") != "success":
            thong_bao = lam_sach(str(data.get("message") or f"HTTP {resp.status_code}"))
            if "invalid ip" in thong_bao.lower():
                thong_bao += (" — thêm IP máy chạy Domain Gateway ở Setup > General Settings > "
                              "Security, hoặc điền API access key")
            raise WhmcsError(f"WHMCS: {thong_bao}")
        return data

    def verify(self) -> dict:
        """Goi thu mot trang mot dong. Xac nhan URL, credential va IP deu dung."""
        data = self._goi("GetClientsDomains", limitstart=0, limitnum=1)
        return {"ok": True, "tong": _so(data.get("totalresults"))}

    def tat_ca(self) -> list:
        """Moi ten mien trong WHMCS, da chuan hoa va gop dong trung."""
        ra, bat_dau = [], 0
        while bat_dau < TOI_DA:
            data = self._goi("GetClientsDomains", limitstart=bat_dau, limitnum=MOI_TRANG)
            trang = _danh_sach(data.get("domains"))
            if not trang:
                break
            ra.extend(m for m in (_muc(d) for d in trang) if m)
            bat_dau += len(trang)
            tong = _so(data.get("totalresults"))
            if tong is not None and bat_dau >= tong:
                break
        tot = {}
        for m in ra:
            cu = tot.get(m["domain"])
            if cu is None or _hang(m) < _hang(cu):
                tot[m["domain"]] = m
        return list(tot.values())
