"""Doc trang thai zone tu Cloudflare API.

Vi sao them nguon nay, trong khi RDAP da cho ngay het han:

  - RDAP tra loi cau "ten mien nay con han den bao gio". Cloudflare tra loi cau
    "ten mien nay co dang phuc vu gi khong" - hai cau khac han nhau. Mot ten
    mien con han 500 ngay ma zone khong co ban ghi A nao thi website van tat.
  - 7/9 ten mien trong kho dung DNS Cloudflare, nen mot token doc-chi phu duoc
    gan het, trong khi API cua tung nha dang ky moi noi mot kieu va phan lon
    con khong cong khai.

Chi can quyen DOC: Zone:Read va Zone.DNS:Read. Khong bao gio ghi gi len
Cloudflare - moi ham o day deu la GET.
"""

from __future__ import annotations

import requests

API = "https://api.cloudflare.com/client/v4"
USER_AGENT = "DomainGateway/1.0"

# Ban ghi khien mot ten mien thuc su tro toi dau do. TXT/MX/NS khong tinh:
# co MX ma khong co A thi nhan duoc mail nhung web van tat.
LOAI_TRO_TOI = ("A", "AAAA", "CNAME")


class CloudflareError(RuntimeError):
    pass


class CloudflareClient:
    """Client toi thieu, chi doc. Khong phu thuoc SDK nao."""

    def __init__(self, token: str, timeout: int = 20):
        self.token = (token or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token)

    # ---- ha tang ----------------------------------------------------------
    def _get(self, duong_dan: str, params: dict = None) -> dict:
        if not self.token:
            raise CloudflareError("Chưa cấu hình API token Cloudflare")
        try:
            resp = requests.get(
                API + duong_dan,
                headers={"Authorization": f"Bearer {self.token}",
                         "User-Agent": USER_AGENT},
                params=params or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            # Khong dua str(exc) ra ngoai: URL co the keo theo token vao thong bao
            raise CloudflareError(f"Không gọi được Cloudflare: {type(exc).__name__}") from None

        if resp.status_code in (401, 403):
            raise CloudflareError(
                "Token bị từ chối (HTTP %d). Kiểm tra token còn hạn và có đủ "
                "quyền Zone:Read + Zone.DNS:Read." % resp.status_code
            )
        try:
            data = resp.json()
        except ValueError:
            raise CloudflareError(f"Cloudflare trả về HTTP {resp.status_code}, không phải JSON") from None

        if not data.get("success"):
            loi = "; ".join(
                str(e.get("message") or e) for e in (data.get("errors") or [])
            ) or f"HTTP {resp.status_code}"
            raise CloudflareError(f"Cloudflare: {loi}")
        return data

    def _get_all(self, duong_dan: str, params: dict = None, gioi_han: int = 20) -> list:
        """Lay het cac trang. gioi_han chan vong lap chay mai neu API doi kieu."""
        ra, trang = [], 1
        p = dict(params or {})
        p.setdefault("per_page", 50)
        while trang <= gioi_han:
            p["page"] = trang
            data = self._get(duong_dan, p)
            ra.extend(data.get("result") or [])
            info = data.get("result_info") or {}
            if trang >= (info.get("total_pages") or 1):
                break
            trang += 1
        return ra

    # ---- doc du lieu ------------------------------------------------------
    def verify(self) -> dict:
        """Kiem tra token con song. Khong doc du lieu zone nao."""
        data = self._get("/user/tokens/verify")
        return {"ok": True, "status": (data.get("result") or {}).get("status", "?")}

    def zones(self) -> list:
        """Danh sach zone trong tai khoan."""
        return self._get_all("/zones")

    def dns_summary(self, zone_id: str) -> dict:
        """Dem ban ghi tro toi dau do trong mot zone."""
        ban_ghi = self._get_all(f"/zones/{zone_id}/dns_records", {"per_page": 100})
        tro_toi = [r for r in ban_ghi if str(r.get("type", "")).upper() in LOAI_TRO_TOI]
        return {
            "tong": len(ban_ghi),
            "tro_toi": len(tro_toi),
            "proxy": sum(1 for r in tro_toi if r.get("proxied")),
        }

    def quet(self, ten_mien: list[str] = None) -> dict:
        """Tra ve {ten_mien: {...}} cho cac zone doc duoc.

        `ten_mien` de loc bot: chi doc DNS cua zone minh dang theo doi, khong
        goi thua cho ca tram zone khac trong tai khoan.
        """
        loc = {d.lower() for d in ten_mien} if ten_mien else None
        ra = {}
        for z in self.zones():
            ten = str(z.get("name", "")).lower()
            if loc is not None and ten not in loc:
                continue
            muc = {
                "zone_id": z.get("id", ""),
                "status": z.get("status", ""),
                "paused": bool(z.get("paused")),
                "nameservers": sorted(z.get("name_servers") or []),
            }
            try:
                muc.update(self.dns_summary(z["id"]))
            except CloudflareError as exc:
                # Token co Zone:Read ma thieu Zone.DNS:Read -> van biet zone ton
                # tai, chi khong dem duoc ban ghi. Ghi lai ly do thay vi bo qua.
                muc["loi"] = str(exc)
            ra[ten] = muc
        return ra


# Ket luan ngan ("ok" / "khong-ban-ghi" / "tam-dung"...) nam o
# models.DomainRecord.cf_ket_luan, tinh tu cac cot da luu trong DB.
# Truoc day o day co mot ban thu hai ten chan_doan() khong ai goi, va no
# da lech that: nhanh paused cua no khong bao gio chay vi cot cf_paused
# chua ton tai. Mot quy tac thi chi nen co mot cho viet.
