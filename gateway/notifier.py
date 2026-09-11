"""Gui canh bao het han qua Telegram va Zalo Bot.

Hai nen tang giong nhau o KHUNG - token nam trong duong dan, POST JSON, phan
hoi {"ok", "result"} - nen dung chung mot lop cha. Nhung khac o ba cho that, da
doc tai lieu Zalo chu khong doan:

  * Zalo gioi han 2000 ky tu moi tin (tinh ca ky tu danh dau); Telegram 4096.
  * getMe cua Zalo tra `account_name`, khong co `username` / `first_name`.
  * parse_mode "html" cua Zalo khong nhan <code>, va tai lieu KHONG noi ky tu
    xuong dong co duoc giu trong che do do hay khong. Nen tin Zalo gui van ban
    tron: chac chan xuong dong quan trong hon chu dam.

Telegram van dung parse_mode=HTML thay vi MarkdownV2: ten mien chua dau cham va
gach duoi, MarkdownV2 bat buoc escape hang chuc ky tu nen rat de sinh loi 400.
HTML chi can escape ba ky tu & < >.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import requests

from .models import lam_sach

ICON = {"expired": "⚫", "critical": "\U0001f534", "expiring": "\U0001f7e1"}
BUCKET_LABEL = {
    "expired": "ĐÃ HẾT HẠN",
    "critical": "NGUY CẤP",
    "expiring": "SẮP HẾT HẠN",
}

# Ten nha cung cap do nguoi dung tu go nen dai bao nhieu cung duoc. Cat o day de
# mot dong khong bao gio vuot gioi han mot tin - khong thi phai cat giua dong, ma
# cat giua mot dong HTML la xe doi the (bai hoc tu ban xuat PDF).
TOI_DA_TEN = 200

_THE_HTML = re.compile(r"<[^>]+>")


def esc(text) -> str:
    return (str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def html_sang_chu(text: str) -> str:
    """Bo the HTML va giai ma &amp; &lt; &gt; - noi dung cho kenh van ban tron."""
    return html.unescape(_THE_HTML.sub("", text or ""))


class _BotNotifier:
    """Khung chung cho bot kieu Telegram: token trong duong dan, POST JSON."""

    MA = ""             # "telegram" / "zalo" - dung trong API va cau hinh
    TEN = ""            # ten hien cho nguoi dung
    API = ""            # mau URL co {token} va {method}
    MAX_LEN = 4096
    PARSE_MODE = None   # None = van ban tron

    def __init__(self, token: str, chat_id: str, timeout: int = 20):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, payload: dict = None, timeout: int = None) -> dict:
        if not self.token:
            return {"ok": False, "description": "Chưa cấu hình bot token"}
        try:
            resp = requests.post(
                self.API.format(token=self.token, method=method),
                json=payload or {},
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            # Khong dua str(exc) ra ngoai: URL chua token, va thong bao loi cua
            # requests keo nguyen URL theo.
            return {"ok": False, "description": f"Không gọi được {self.TEN}: {type(exc).__name__}"}
        try:
            data = resp.json()
        except ValueError:
            return {"ok": False, "description": f"{self.TEN} trả về HTTP {resp.status_code}"}
        try:
            # Du lieu mang ben ngoai: don surrogate lac va ky tu dieu khien truoc
            # khi no toi jsonify - cung cua voi RDAP va Cloudflare.
            data = lam_sach(data)
        except ValueError:
            return {"ok": False, "description": f"{self.TEN} trả về dữ liệu lồng quá sâu"}
        if not isinstance(data, dict):
            return {"ok": False, "description": f"{self.TEN} trả về dữ liệu không đúng dạng"}
        if not data.get("ok"):
            data["description"] = self._mo_ta_loi(data, resp.status_code)
        return data

    def _mo_ta_loi(self, data: dict, ma_http: int) -> str:
        # Telegram tra loi rat ro rang o truong description, giu nguyen de doc
        return str(data.get("description") or f"HTTP {ma_http}")

    def _ten_bot(self, result: dict) -> str:
        raise NotImplementedError

    def _dinh_dang(self, text_html: str) -> str:
        """Noi dung duoc viet bang HTML kieu Telegram; doi sang dinh dang cua kenh."""
        return text_html if self.PARSE_MODE == "HTML" else html_sang_chu(text_html)

    def _goi_tin(self, chunk: str, parse_mode) -> dict:
        p = {"chat_id": self.chat_id, "text": chunk}
        if parse_mode:
            p["parse_mode"] = parse_mode
        return p

    def _do_dai(self, s: str) -> int:
        """Do dai theo CACH NEN TANG DEM. Zalo noi "ky tu" nen dem ky tu."""
        return len(s)

    def _cat_dau(self, s: str, gioi_han: int) -> tuple:
        """Phan dau dai toi da `gioi_han` (theo _do_dai) va phan con lai.

        Khong cat bang s[:gioi_han]: do la cat theo ky tu, ma voi Telegram mot
        emoji ton hai don vi - cat theo ky tu van vuot gioi han.
        """
        dem = 0
        for i, ch in enumerate(s):
            dem += self._do_dai(ch)
            if dem > gioi_han:
                return s[:i], s[i:]
        return s, ""

    def _chia(self, text: str) -> list:
        """Cat tin theo dong, khong cat giua mot dong.

        Mot dong dai hon ca gioi han thi buoc phai cat cung giua dong. Chi an toan
        voi van ban tron - send() da lui kenh HTML ve van ban tron truoc khi toi
        day, de khong bao gio xe doi mot the.
        """
        gioi_han = self.MAX_LEN - 32
        chunks, current = [], ""
        for line in text.split("\n"):
            while self._do_dai(line) > gioi_han:
                if current:
                    chunks.append(current)
                    current = ""
                dau, line = self._cat_dau(line, gioi_han)
                chunks.append(dau)
            if current and self._do_dai(current) + self._do_dai(line) + 1 > gioi_han:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        # Zalo tu choi tin rong ("tu 1 den 2000 ky tu")
        return [c for c in chunks if c.strip()]

    def check(self) -> dict:
        """Xac thuc token va tra ve ten bot. Khong gui tin nhan nao."""
        if not self.token:
            return {"ok": False, "description": "Chưa cấu hình bot token"}
        data = self._call("getMe")
        if data.get("ok"):
            return {"ok": True, "bot": self._ten_bot(data.get("result") or {})}
        return {"ok": False, "description": data.get("description")}

    def send(self, text: str) -> dict:
        if not self.configured:
            return {"ok": False, "description": "Chưa cấu hình bot token hoặc chat id"}
        noi_dung, parse_mode = self._dinh_dang(text), self.PARSE_MODE
        if parse_mode and any(self._do_dai(d) > self.MAX_LEN - 32
                              for d in noi_dung.split("\n")):
            # Mot dong dai hon ca mot tin thi phai cat giua dong, ma cat giua dong
            # HTML la xe doi the -> nen tang tu choi ca tin. Lui ve van ban tron
            # cho rieng lan gui nay.
            noi_dung, parse_mode = html_sang_chu(noi_dung), None

        last = {"ok": True}
        for chunk in self._chia(noi_dung):
            last = self._call("sendMessage", self._goi_tin(chunk, parse_mode))
            if not last.get("ok"):
                return last
        return last

    def send_test(self) -> dict:
        info = self.check()
        if not info.get("ok"):
            return info
        now = datetime.now(timezone.utc).astimezone().strftime("%H:%M %d/%m/%Y")
        text = (
            "✅ <b>Domain Gateway đã kết nối</b>\n\n"
            f"Kênh: {esc(self.TEN)}\n"
            f"Bot: <code>{esc(info.get('bot'))}</code>\n"
            f"Thời điểm: {esc(now)}\n\n"
            "Từ giờ cảnh báo hết hạn tên miền sẽ được gửi vào đây."
        )
        result = self.send(text)
        if result.get("ok"):
            result["bot"] = info.get("bot")
        return result


class TelegramNotifier(_BotNotifier):
    MA = "telegram"
    TEN = "Telegram"
    API = "https://api.telegram.org/bot{token}/{method}"
    MAX_LEN = 4096
    PARSE_MODE = "HTML"

    def _do_dai(self, s: str) -> int:
        # Telegram dem gioi han 4096 theo don vi UTF-16: emoji ngoai BMP (phan
        # lon emoji) ton HAI don vi. Dem bang len() thi tin nhieu emoji van vuot,
        # bi tu choi, va vi gui hong nen canh bao khong bao gio duoc danh dau -
        # lan chay nao cung hong lai.
        return len(s.encode("utf-16-le", "surrogatepass")) // 2

    def _ten_bot(self, result: dict) -> str:
        u = result.get("username")
        return f"@{u}" if u else str(result.get("first_name") or "bot")

    def _goi_tin(self, chunk: str, parse_mode) -> dict:
        p = super()._goi_tin(chunk, parse_mode)
        p["disable_web_page_preview"] = True
        return p


class ZaloNotifier(_BotNotifier):
    """Zalo Bot Platform: https://docs.zaloplatforms.com/docs/BOT/apis/sendMessage"""

    MA = "zalo"
    TEN = "Zalo"
    API = "https://bot-api.zaloplatforms.com/bot{token}/{method}"
    MAX_LEN = 2000       # "tu 1 den 2000 ky tu", tinh ca ky tu danh dau
    PARSE_MODE = None    # van ban tron - xem docstring dau file

    def _ten_bot(self, result: dict) -> str:
        return str(result.get("account_name") or result.get("id") or "bot")

    def _mo_ta_loi(self, data: dict, ma_http: int) -> str:
        # Tai lieu khong co mau phan hoi loi. Da gap ca `error_code` (bang 0
        # trong phan hoi thanh cong) lan `errorCode` (426 khi vuot han muc) -
        # doc ca hai, uu tien loi bang chu neu co.
        chu = data.get("description") or data.get("message")
        ma = data.get("error_code", data.get("errorCode"))
        if chu:
            return f"{chu} (mã {ma})" if ma not in (None, 0) else str(chu)
        if ma not in (None, 0):
            return f"Zalo báo lỗi mã {ma}"
        return f"HTTP {ma_http}"

    def tim_chat_id(self) -> dict:
        """Doc tin nhan gan day gui toi bot de lay chat_id. Chi doc, khong gui.

        getUpdates KHONG chay neu da dat webhook; app nay khong dat webhook nao.
        Tai lieu chinh thuc khong co mau phan hoi, nen doc ca hai dang: `result`
        la mot update, hoac mot danh sach update. chat_id nam o message.chat.id
        va co the la chuoi hex chu khong phai so.
        """
        # timeout la long-polling: khong co tin moi thi Zalo giu ket noi toi chung
        # ay giay. 5 giay du cho nguoi vua nhan tin, khong treo nut bam qua lau.
        data = self._call("getUpdates", {"timeout": "5"}, timeout=self.timeout + 10)
        if not data.get("ok"):
            return {"ok": False, "description": data.get("description")}
        kq = data.get("result")
        updates = kq if isinstance(kq, list) else ([kq] if isinstance(kq, dict) else [])
        thay, ra = set(), []
        for u in reversed(updates):          # moi nhat truoc
            if not isinstance(u, dict):
                continue
            msg = u.get("message") if isinstance(u.get("message"), dict) else {}
            chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
            nguoi = msg.get("from") if isinstance(msg.get("from"), dict) else {}
            cid = chat.get("id") or nguoi.get("id")
            if not cid or str(cid) in thay:
                continue
            thay.add(str(cid))
            ra.append({
                "chat_id": str(cid),
                "ten": str(nguoi.get("display_name") or chat.get("display_name") or ""),
                "text": str(msg.get("text") or "")[:80],
            })
        return {"ok": True, "chat": ra}


class MultiNotifier:
    """Gui toi moi kenh da cau hinh. App va CLI chi lam viec voi lop nay."""

    def __init__(self, tat_ca):
        self.tat_ca = list(tat_ca)

    def kenh(self, ma: str) -> _BotNotifier:
        for k in self.tat_ca:
            if k.MA == ma:
                return k
        raise KeyError(ma)

    @property
    def da_cau_hinh(self) -> list:
        return [k for k in self.tat_ca if k.configured]

    @property
    def configured(self) -> bool:
        return bool(self.da_cau_hinh)

    def send(self, text: str) -> dict:
        """ok CHI True khi MOI kenh da cau hinh deu gui duoc.

        Nguoi goi dua vao `ok` de danh dau "da bao" chong gui trung. Danh dau khi
        mot kenh hong thi kenh do khong bao gio nhan canh bao ay nua - mat im
        lang. Khong danh dau thi kenh da gui duoc se nhan lap o lan sau. Nhan lap
        con hon mat.
        """
        kenh = self.da_cau_hinh
        if not kenh:
            return {"ok": False, "description": "Chưa cấu hình kênh cảnh báo nào"}
        ket_qua = [(k.TEN, k.send(text)) for k in kenh]
        hong = [(t, r) for t, r in ket_qua if not r.get("ok")]
        if not hong:
            return {"ok": True, "kenh": [t for t, _ in ket_qua]}
        mo_ta = "; ".join(f"{t}: {r.get('description') or 'gửi thất bại'}" for t, r in hong)
        duoc = [t for t, r in ket_qua if r.get("ok")]
        if duoc:
            mo_ta += (" — đã gửi được qua " + ", ".join(duoc)
                      + " nhưng chưa đánh dấu là đã báo, lần sau sẽ gửi lại")
        return {"ok": False, "description": mo_ta}

    def send_test(self, chi: str = None) -> dict:
        kenh = [k for k in self.da_cau_hinh if chi is None or k.MA == chi]
        ds = []
        for k in kenh:
            r = k.send_test()
            ds.append({"kenh": k.TEN, "ma": k.MA, "ok": bool(r.get("ok")),
                       "bot": r.get("bot"), "description": r.get("description")})
        if not ds:
            return {"ok": False, "description": "Chưa cấu hình kênh cảnh báo nào", "kenh": []}
        hong = [x for x in ds if not x["ok"]]
        return {"ok": not hong, "kenh": ds,
                "description": "; ".join(f"{x['kenh']}: {x['description']}" for x in hong)}


def tu_cau_hinh(notify: dict | None) -> MultiNotifier:
    """Dung bo gui tu muc `notify` cua config. App va CLI dung chung mot cho."""
    n = notify or {}
    return MultiNotifier([
        TelegramNotifier(n.get("telegram_bot_token", ""), n.get("telegram_chat_id", "")),
        ZaloNotifier(n.get("zalo_bot_token", ""), n.get("zalo_chat_id", "")),
    ])


def build_message(records, warn: int, critical: int, title: str = None) -> str:
    """Gom ban ghi theo muc do khan roi dung noi dung HTML (kieu Telegram).

    Kenh van ban tron (Zalo) tu doi sang chu tron o _BotNotifier._dinh_dang.
    """
    buckets = {"expired": [], "critical": [], "expiring": []}
    for rec in records:
        days = rec.days_left
        if days is None:
            continue
        if days < 0:
            buckets["expired"].append(rec)
        elif days <= critical:
            buckets["critical"].append(rec)
        elif days <= warn:
            buckets["expiring"].append(rec)

    lines = [f"<b>{esc(title or 'Domain Gateway — cảnh báo hết hạn')}</b>"]
    for key in ("expired", "critical", "expiring"):
        group = sorted(buckets[key], key=lambda r: r.days_left)
        if not group:
            continue
        lines.append("")
        lines.append(f"{ICON[key]} <b>{BUCKET_LABEL[key]}</b>")
        for rec in group:
            when = rec.expires_at.astimezone().strftime("%d/%m/%Y") if rec.expires_at else "?"
            # Cat TRUOC roi moi escape - cat sau thi co the xe doi "&amp;"
            who = (rec.provider or rec.registrar or "không rõ nhà cung cấp")[:TOI_DA_TEN]
            days = rec.days_left
            when_text = f"quá <b>{abs(days)}</b> ngày" if days < 0 else f"còn <b>{days}</b> ngày"
            lines.append(f"• <code>{esc(rec.domain)}</code> — {when_text} ({esc(when)})")
            lines.append(f"   <i>{esc(who)}</i>")

    total = sum(len(v) for v in buckets.values())
    if total == 0:
        return ""
    lines.append("")
    lines.append(f"<i>Tổng {total} tên miền cần xử lý.</i>")
    return "\n".join(lines)


def bucket_of(rec, warn: int, critical: int) -> str | None:
    """Nhan muc do khan cua mot ban ghi, dung lam khoa chong gui trung."""
    days = rec.days_left
    if days is None:
        return None
    if days < 0:
        return "expired"
    if days <= critical:
        return "critical"
    if days <= warn:
        return "expiring"
    return None
