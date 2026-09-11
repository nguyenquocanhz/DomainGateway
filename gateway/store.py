"""Luu tru SQLite cho Domain Gateway.

Nguyen tac quan trong: refresh tu registry KHONG duoc ghi de cac truong do
nguoi dung tu nhap (provider, tags, note, auto_renew, manual_expires_at).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from .models import DomainRecord, iso, parse_dt, utcnow

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "gateway.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    domain            TEXT PRIMARY KEY,
    tld               TEXT DEFAULT '',
    registrar         TEXT DEFAULT '',
    provider          TEXT DEFAULT '',
    created_at        TEXT,
    updated_at        TEXT,
    expires_at        TEXT,
    manual_expires_at TEXT,
    pinned            INTEGER DEFAULT 0,
    nameservers       TEXT DEFAULT '[]',
    epp_status        TEXT DEFAULT '[]',
    dnssec            INTEGER DEFAULT 0,
    registrant        TEXT DEFAULT '',
    auto_renew        INTEGER,
    tags              TEXT DEFAULT '[]',
    note              TEXT DEFAULT '',
    source            TEXT DEFAULT '',
    checked_at        TEXT,
    error             TEXT DEFAULT '',
    added_at          TEXT,
    cf_status         TEXT DEFAULT '',
    cf_records        INTEGER,
    cf_proxied        INTEGER,
    cf_paused         INTEGER DEFAULT 0,
    cf_checked_at     TEXT,
    whmcs_expiry      TEXT,
    whmcs_nextdue     TEXT,
    whmcs_status      TEXT DEFAULT '',
    whmcs_registrar   TEXT DEFAULT '',
    whmcs_checked_at  TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    domain     TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    expires_at TEXT,
    source     TEXT DEFAULT '',
    error      TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_history_domain ON history(domain, checked_at DESC);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Chong gui trung canh bao: mot ten mien chi bao mot lan cho moi muc do khan.
-- expires_at nam trong khoa chinh nen khi gia han xong, chu ky canh bao tu reset.
CREATE TABLE IF NOT EXISTS alerts (
    domain     TEXT NOT NULL,
    bucket     TEXT NOT NULL,
    expires_at TEXT NOT NULL DEFAULT '',
    sent_at    TEXT NOT NULL,
    PRIMARY KEY (domain, bucket, expires_at)
);
"""

# Cac truong thuoc ve nguoi dung, refresh khong duoc dung toi
USER_FIELDS = ("provider", "tags", "note", "auto_renew", "manual_expires_at", "pinned")

# Trang thai zone Cloudflare. Cung nguyen tac nhu USER_FIELDS nhung nguoc
# chieu: save_lookup (registry) khong duoc cham vao day, va save_cloudflare
# khong duoc cham vao du lieu registry. Hai nguon tra loi hai cau hoi khac
# nhau, ghi de nhau la mat mot nua thong tin.
CF_FIELDS = ("cf_status", "cf_records", "cf_proxied", "cf_paused", "cf_checked_at")

# So sach WHMCS. Nhom thu ba, cung luat: save_lookup, save_cloudflare va truong
# nguoi dung khong duoc cham vao day, save_whmcs khong duoc cham ra ngoai. Ghi
# de nhau la mat chinh cai ta dang muon so sanh.
WHMCS_FIELDS = ("whmcs_expiry", "whmcs_nextdue", "whmcs_status", "whmcs_registrar",
                "whmcs_checked_at")


def _dumps(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _loads(value):
    try:
        out = json.loads(value or "[]")
        return out if isinstance(out, list) else []
    except (ValueError, TypeError):
        return []


class Store:
    def __init__(self, path: str = DEFAULT_DB):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            self._nang_cap(conn)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def _nang_cap(self, conn) -> None:
        """Them cot con thieu vao DB da ton tai.

        SCHEMA dung CREATE TABLE IF NOT EXISTS nen bang cu khong tu moc them
        cot. Doc cot hien co roi ALTER tung cai - re va chay duoc nhieu lan.
        """
        co = {r["name"] for r in conn.execute("PRAGMA table_info(domains)")}
        for ten, kieu in (("cf_status", "TEXT DEFAULT ''"), ("cf_records", "INTEGER"),
                          ("cf_proxied", "INTEGER"), ("cf_paused", "INTEGER DEFAULT 0"),
                          ("cf_checked_at", "TEXT"),
                          ("whmcs_expiry", "TEXT"), ("whmcs_nextdue", "TEXT"),
                          ("whmcs_status", "TEXT DEFAULT ''"),
                          ("whmcs_registrar", "TEXT DEFAULT ''"),
                          ("whmcs_checked_at", "TEXT")):
            if ten not in co:
                conn.execute(f"ALTER TABLE domains ADD COLUMN {ten} {kieu}")

    def save_cloudflare(self, domain: str, muc: dict) -> None:
        """Ghi trang thai zone. KHONG dung toi bat ky truong registry nao.

        `muc` rong nghia la khong tim thay zone trong tai khoan - van ghi lai
        cf_checked_at de phan biet "da quet, khong thay" voi "chua quet bao gio".
        """
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO domains (domain, added_at) VALUES (?, ?)",
                (domain, iso(utcnow())),
            )
            conn.execute(
                "UPDATE domains SET cf_status = ?, cf_records = ?, cf_proxied = ?, "
                "cf_paused = ?, cf_checked_at = ? WHERE domain = ?",
                (
                    (muc or {}).get("status", ""),
                    (muc or {}).get("tro_toi"),
                    (muc or {}).get("proxy"),
                    int(bool((muc or {}).get("paused"))),
                    iso(utcnow()),
                    domain,
                ),
            )

    def save_whmcs(self, domain: str, muc: dict) -> None:
        """Ghi so sach WHMCS cua mot ten mien. CHI dung toi WHMCS_FIELDS.

        `muc` rong = da dong bo nhung khong co trong WHMCS. Van ghi
        whmcs_checked_at de phan biet voi "chua dong bo bao gio".
        """
        m = muc or {}
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO domains (domain, added_at) VALUES (?, ?)",
                (domain, iso(utcnow())),
            )
            conn.execute(
                "UPDATE domains SET whmcs_expiry = ?, whmcs_nextdue = ?, whmcs_status = ?, "
                "whmcs_registrar = ?, whmcs_checked_at = ? WHERE domain = ?",
                (iso(m.get("expiry")), iso(m.get("nextdue")), m.get("status", "") or "",
                 m.get("registrar", "") or "", iso(utcnow()), domain),
            )

    # ---- doc ---------------------------------------------------------------
    def _row_to_record(self, row: sqlite3.Row) -> DomainRecord:
        rec = DomainRecord(
            domain=row["domain"],
            tld=row["tld"] or "",
            registrar=row["registrar"] or "",
            provider=row["provider"] or "",
            created_at=parse_dt(row["created_at"]),
            updated_at=parse_dt(row["updated_at"]),
            expires_at=parse_dt(row["expires_at"]),
            nameservers=_loads(row["nameservers"]),
            epp_status=_loads(row["epp_status"]),
            dnssec=bool(row["dnssec"]),
            registrant=row["registrant"] or "",
            auto_renew=None if row["auto_renew"] is None else bool(row["auto_renew"]),
            tags=_loads(row["tags"]),
            note=row["note"] or "",
            source=row["source"] or "",
            checked_at=parse_dt(row["checked_at"]),
            error=row["error"] or "",
            cf_status=row["cf_status"] or "",
            cf_records=row["cf_records"],
            cf_proxied=row["cf_proxied"],
            cf_paused=bool(row["cf_paused"]),
            cf_checked_at=parse_dt(row["cf_checked_at"]),
            whmcs_expiry=parse_dt(row["whmcs_expiry"]),
            whmcs_nextdue=parse_dt(row["whmcs_nextdue"]),
            whmcs_status=row["whmcs_status"] or "",
            whmcs_registrar=row["whmcs_registrar"] or "",
            whmcs_checked_at=parse_dt(row["whmcs_checked_at"]),
        )
        # Ngay het han nhap tay duoc uu tien khi pinned hoac khi registry im lang
        manual = parse_dt(row["manual_expires_at"])
        if manual and (row["pinned"] or not rec.expires_at):
            rec.expires_at = manual
            rec.source = (rec.source + "+manual").strip("+") if rec.source else "manual"
        return rec

    def all(self) -> list:
        rows = self._conn().execute("SELECT * FROM domains ORDER BY domain").fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, domain: str) -> DomainRecord | None:
        row = self._conn().execute("SELECT * FROM domains WHERE domain = ?", (domain,)).fetchone()
        return self._row_to_record(row) if row else None

    def raw(self, domain: str) -> dict | None:
        row = self._conn().execute("SELECT * FROM domains WHERE domain = ?", (domain,)).fetchone()
        return dict(row) if row else None

    def names(self) -> list:
        return [r["domain"] for r in self._conn().execute("SELECT domain FROM domains ORDER BY domain")]

    def history(self, domain: str, limit: int = 50) -> list:
        rows = self._conn().execute(
            "SELECT * FROM history WHERE domain = ? ORDER BY checked_at DESC LIMIT ?",
            (domain, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- ghi ---------------------------------------------------------------
    def add(self, domain: str, provider: str = "", tags=None, note: str = "",
            manual_expires_at=None, auto_renew=None, pinned: bool = False) -> None:
        """Them ten mien moi. Neu da ton tai thi chi cap nhat truong nguoi dung."""
        conn = self._conn()
        with conn:
            exists = conn.execute("SELECT 1 FROM domains WHERE domain = ?", (domain,)).fetchone()
            if exists:
                # update_user_fields dung quy uoc "None = giu nguyen", tru
                # auto_renew va manual_expires_at: hai truong do dung sentinel
                # "__keep__" vi None la mot GIA TRI that voi chung ("khong ro"
                # va "xoa ngay nhap tay"). add() phai DICH cac mac dinh cua
                # minh sang quy uoc do.
                #
                # Truoc day chi dich provider va note; tags/pinned/auto_renew
                # truyen thang [] / False / None nen deu la lenh GHI DE. Them
                # lai mot ten mien da co - bam "Them vao kho quan ly" o tab Tra
                # cuu nhanh, dan danh sach nhieu dong co lan ten da theo doi,
                # hay chay `cli.py import` lan hai - la xoa sach tag, bo ghim,
                # xoa auto_renew, khong mot dong canh bao.
                #
                # Mat ghim la nang nhat: _row_to_record cho manual_expires_at
                # thang khi pinned bat HOAC khi registry im lang. Bo ghim mot ten
                # mien DA co ngay tu registry la ngay nhap tay lang le ngung co
                # tac dung, trong khi cot do van con nguyen trong DB - nhin thang
                # vao du lieu khong thay gi bat thuong.
                #
                # Muon XOA tag hay BO ghim thi dung PATCH /api/domains/<domain>,
                # duong do gui gia tri tuong minh nen van lam duoc.
                self.update_user_fields(
                    domain,
                    provider=provider or None,
                    tags=tags or None,
                    note=note or None,
                    # `not x` chu khong phai `x is None`: cli.py import voi
                    # {"expires_at": ""} phai la "giu nguyen", khong phai "xoa".
                    manual_expires_at=manual_expires_at or "__keep__",
                    auto_renew="__keep__" if auto_renew is None else auto_renew,
                    pinned=pinned or None,
                )
                return
            conn.execute(
                """INSERT INTO domains
                   (domain, provider, tags, note, manual_expires_at, auto_renew, pinned, added_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    domain, provider, _dumps(tags), note,
                    iso(parse_dt(manual_expires_at)),
                    None if auto_renew is None else int(bool(auto_renew)),
                    int(bool(pinned)), iso(utcnow()),
                ),
            )

    def delete(self, domain: str) -> None:
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM domains WHERE domain = ?", (domain,))
            conn.execute("DELETE FROM history WHERE domain = ?", (domain,))

    def update_user_fields(self, domain: str, provider=None, tags=None, note=None,
                           manual_expires_at="__keep__", auto_renew="__keep__",
                           pinned=None) -> None:
        """None = giu nguyen, tru manual_expires_at va auto_renew.

        Hai truong do dung sentinel "__keep__" vi None la mot GIA TRI that voi
        chung: "xoa ngay nhap tay" va "khong ro co tu dong gia han khong".
        Dung None lam "giu nguyen" thi khong con cach nao xoa - hop nhap ngay
        het han moi nguoi dung "de trong de xoa" ma khong xoa duoc gi.
        """
        sets, args = [], []
        if provider is not None:
            sets.append("provider = ?"); args.append(provider)
        if tags is not None:
            sets.append("tags = ?"); args.append(_dumps(tags))
        if note is not None:
            sets.append("note = ?"); args.append(note)
        if manual_expires_at != "__keep__":
            sets.append("manual_expires_at = ?"); args.append(iso(parse_dt(manual_expires_at)))
        if auto_renew != "__keep__":
            sets.append("auto_renew = ?")
            args.append(None if auto_renew is None else int(bool(auto_renew)))
        if pinned is not None:
            sets.append("pinned = ?"); args.append(int(bool(pinned)))
        if not sets:
            return
        args.append(domain)
        conn = self._conn()
        with conn:
            conn.execute("UPDATE domains SET " + ", ".join(sets) + " WHERE domain = ?", args)

    def save_lookup(self, rec: DomainRecord) -> None:
        """Ghi ket qua tra cuu tu registry, giu nguyen cac truong cua nguoi dung."""
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO domains (domain, added_at) VALUES (?, ?)",
                (rec.domain, iso(utcnow())),
            )
            conn.execute(
                """UPDATE domains SET
                     tld = ?, registrar = ?, created_at = ?, updated_at = ?, expires_at = ?,
                     nameservers = ?, epp_status = ?, dnssec = ?, registrant = ?,
                     source = ?, checked_at = ?, error = ?
                   WHERE domain = ?""",
                (
                    rec.tld, rec.registrar, iso(rec.created_at), iso(rec.updated_at),
                    iso(rec.expires_at), _dumps(rec.nameservers), _dumps(rec.epp_status),
                    int(rec.dnssec), rec.registrant, rec.source, iso(rec.checked_at or utcnow()),
                    rec.error, rec.domain,
                ),
            )
            conn.execute(
                "INSERT INTO history (domain, checked_at, expires_at, source, error) VALUES (?,?,?,?,?)",
                (rec.domain, iso(rec.checked_at or utcnow()), iso(rec.expires_at), rec.source, rec.error),
            )

    # ---- chong gui trung canh bao -------------------------------------------
    def was_alerted(self, domain: str, bucket: str, expires_at) -> bool:
        row = self._conn().execute(
            "SELECT 1 FROM alerts WHERE domain = ? AND bucket = ? AND expires_at = ?",
            (domain, bucket, iso(parse_dt(expires_at)) or ""),
        ).fetchone()
        return row is not None

    def mark_alerted(self, domain: str, bucket: str, expires_at) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO alerts (domain, bucket, expires_at, sent_at) VALUES (?,?,?,?)",
                (domain, bucket, iso(parse_dt(expires_at)) or "", iso(utcnow())),
            )

    def clear_alerts(self, domain: str = None) -> int:
        conn = self._conn()
        with conn:
            cur = (conn.execute("DELETE FROM alerts WHERE domain = ?", (domain,)) if domain
                   else conn.execute("DELETE FROM alerts"))
        return cur.rowcount

    def alert_log(self, limit: int = 50) -> list:
        rows = self._conn().execute(
            "SELECT * FROM alerts ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- meta --------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        conn = self._conn()
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, str(value)))

    def get_meta(self, key: str, default=None):
        row = self._conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # ---- thong ke ----------------------------------------------------------
    def summary(self, warn: int = 30, critical: int = 7) -> dict:
        records = self.all()
        buckets = {"total": len(records), "active": 0, "expiring": 0, "critical": 0,
                   "expired": 0, "unknown": 0}
        for rec in records:
            buckets[rec.status(warn, critical)] += 1
        providers = {r.provider or r.registrar or "Khong ro" for r in records}
        soonest = [r for r in records if r.expires_at]
        soonest.sort(key=lambda r: r.expires_at)
        buckets["providers"] = len(providers)
        # Hien thi theo gio may chu (= gio nguoi dung) de khop voi bang o frontend
        buckets["next_expiry"] = (
            {"domain": soonest[0].domain,
             "date": soonest[0].expires_at.astimezone().strftime("%d/%m/%Y"),
             "date_iso": iso(soonest[0].expires_at),
             "days_left": soonest[0].days_left}
            if soonest else None
        )
        buckets["last_refresh"] = self.get_meta("last_refresh")
        return buckets
