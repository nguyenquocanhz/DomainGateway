"""Dang nhap bang email + mat khau cho dashboard web.

Vi sao app nay can lop dang nhap rieng, trong khi dat sau reverse proxy hay
Cloudflare Access cung chan duoc:

  - Hai cach kia deu doi mot thu ben ngoai repo: mot file htpasswd, hoac mot
    tai khoan Cloudflare va ten mien dung DNS cua ho. Ai chi co mot VPS tran
    thi khong dung duoc cach nao.
  - Lop ngoai chan theo NGUOI, khong theo phien lam viec. Dong tab roi mo lai
    van con quyen cho toi khi het phien cua proxy - khong co nut dang xuat.

Mat khau khong bao gio duoc luu nguyen van. `werkzeug.security` di kem Flask
nen khong them phu thuoc nao: mac dinh cua no la scrypt, co salt rieng cho tung
mat khau, va `check_password_hash` so sanh theo thoi gian hang so.
"""

from __future__ import annotations

import os
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

# Do dai mat khau sinh tu dong. 18 byte url-safe ra 24 ky tu - du de khong ai
# do noi, van ngan de chep tay mot lan roi luu vao trinh quan ly mat khau.
SO_BYTE_MAT_KHAU = 18


def bam(mat_khau: str) -> str:
    """Bam mat khau de cat vao config.json."""
    return generate_password_hash(mat_khau)


def kiem(ban_bam: str, mat_khau: str) -> bool:
    """So mat khau nguoi dung go voi ban bam da luu.

    Tra False khi chua cau hinh gi: khong co tai khoan thi khong ai vao duoc,
    chu khong phai ai cung vao duoc.
    """
    if not ban_bam or not mat_khau:
        return False
    try:
        return check_password_hash(ban_bam, mat_khau)
    except (ValueError, TypeError):
        # Ban bam hong hoac sai dinh dang -> tu choi, dung de no nem exception
        # ra ngoai thanh trang 500 lo ra co gi trong config.
        return False


def sinh_mat_khau() -> str:
    return secrets.token_urlsafe(SO_BYTE_MAT_KHAU)


def sinh_secret_key() -> str:
    """Khoa ky cookie phien. Doi khoa = moi phien dang mo bi dang xuat."""
    return secrets.token_urlsafe(32)


def tai_khoan(cfg: dict) -> tuple:
    """(email, ban_bam) dang co hieu luc. Bien moi truong thang file config.

    Giong moi cho khac trong app: DG_* de dat token qua docker-compose ma khong
    phai mount config.json vao image.
    """
    email = (os.environ.get("DG_ADMIN_EMAIL") or cfg.get("admin_email") or "").strip()
    ban_bam = (os.environ.get("DG_ADMIN_PASSWORD_HASH")
               or cfg.get("admin_password_hash") or "").strip()
    return email, ban_bam


def da_cau_hinh(cfg: dict) -> bool:
    email, ban_bam = tai_khoan(cfg)
    return bool(email and ban_bam)


def so_email(a: str, b: str) -> bool:
    """So email khong phan biet hoa thuong va khoang trang thua.

    Email khong phai bi mat nen khong can so theo thoi gian hang so; phan do
    nam o `kiem()`.
    """
    return (a or "").strip().lower() == (b or "").strip().lower()
