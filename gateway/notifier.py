"""Gui canh bao het han qua Telegram.

Dung parse_mode=HTML thay vi MarkdownV2: ten mien chua dau cham va gach duoi,
MarkdownV2 bat buoc escape hang chuc ky tu nen rat de sinh loi 400 tu Telegram.
HTML chi can escape ba ky tu & < >.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096  # gioi han mot tin nhan cua Telegram

ICON = {"expired": "⚫", "critical": "\U0001f534", "expiring": "\U0001f7e1"}
BUCKET_LABEL = {
    "expired": "ĐÃ HẾT HẠN",
    "critical": "NGUY CẤP",
    "expiring": "SẮP HẾT HẠN",
}


def esc(text) -> str:
    return (str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: int = 20):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, payload: dict = None) -> dict:
        if not self.token:
            return {"ok": False, "description": "Chưa cấu hình bot token"}
        try:
            resp = requests.post(
                TELEGRAM_API.format(token=self.token, method=method),
                json=payload or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return {"ok": False, "description": f"Không gọi được Telegram: {type(exc).__name__}"}
        try:
            data = resp.json()
        except ValueError:
            return {"ok": False, "description": f"Telegram trả về HTTP {resp.status_code}"}
        if not data.get("ok"):
            # Telegram tra loi rat ro rang o truong description, giu nguyen de nguoi dung doc
            data.setdefault("description", f"HTTP {resp.status_code}")
        return data

    def check(self) -> dict:
        """Xac thuc token va tra ve thong tin bot. Khong gui tin nhan nao."""
        if not self.token:
            return {"ok": False, "description": "Chưa cấu hình bot token"}
        data = self._call("getMe")
        if data.get("ok"):
            bot = data.get("result") or {}
            return {"ok": True, "bot": bot.get("username"), "name": bot.get("first_name")}
        return {"ok": False, "description": data.get("description")}

    def send(self, text: str) -> dict:
        if not self.configured:
            return {"ok": False, "description": "Chưa cấu hình bot token hoặc chat id"}
        # Tin qua dai thi cat theo dong, khong cat giua chung mot dong
        chunks, current = [], ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > MAX_LEN - 32:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)

        last = {"ok": True}
        for chunk in chunks:
            last = self._call("sendMessage", {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
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
            f"Bot: <code>@{esc(info.get('bot'))}</code>\n"
            f"Thời điểm: {esc(now)}\n\n"
            "Từ giờ cảnh báo hết hạn tên miền sẽ được gửi vào đây."
        )
        result = self.send(text)
        if result.get("ok"):
            result["bot"] = info.get("bot")
        return result


def build_message(records, warn: int, critical: int, title: str = None) -> str:
    """Gom ban ghi theo muc do khan roi dung noi dung HTML cho Telegram."""
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
            who = rec.provider or rec.registrar or "không rõ nhà cung cấp"
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
