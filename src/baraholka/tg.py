"""Telegram через голый Bot API на httpx: карточки лотов и кнопки под ними."""

import json
import sqlite3

import httpx


class Bot:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.chat_id = str(chat_id)
        self.offset = 0
        self.http = httpx.Client(timeout=60)

    def call(self, method: str, **params):
        r = self.http.post(self.url + method, json=params)
        r.raise_for_status()
        return r.json()["result"]

    def send_card(self, row: sqlite3.Row) -> None:
        text, markup = card(row)
        photos = json.loads(row["photos"])
        if photos:
            self.call("sendPhoto", chat_id=self.chat_id, photo=photos[0], caption=text,
                      parse_mode="HTML", reply_markup=markup)
        else:
            self.call("sendMessage", chat_id=self.chat_id, text=text,
                      parse_mode="HTML", reply_markup=markup)

    def poll(self, conn: sqlite3.Connection, timeout: int) -> None:
        """Ждёт нажатия кнопок до timeout секунд и обрабатывает их."""
        for upd in self.call("getUpdates", offset=self.offset, timeout=timeout,
                             allowed_updates=["callback_query"]):
            self.offset = upd["update_id"] + 1
            q = upd.get("callback_query")
            # Чужие нажатия игнорируем: бот публичный, а база — моя
            if not q or str(q["message"]["chat"]["id"]) != self.chat_id:
                continue
            self.call("answerCallbackQuery", callback_query_id=q["id"], text=handle(conn, q["data"]))
            if q["data"].startswith("skip:"):
                self.call("editMessageReplyMarkup", chat_id=self.chat_id,
                          message_id=q["message"]["message_id"], reply_markup={"inline_keyboard": []})


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(row: sqlite3.Row) -> tuple[str, dict]:
    price = "БЕСПЛАТНО" if row["is_free"] else f"{row['price_ron']:.0f} lei" if row["price_ron"] is not None else "цена не указана"
    text = (
        f"<b>{_esc(row['title'])}</b>\n"
        f"💰 {price} · 📍 {_esc(row['city'] or '?')} · {row['source']}\n"
        + (f"⭐ <b>{row['llm_score']:.0f}/10</b> — {_esc(row['llm_comment'] or '')}\n" if row["llm_score"] is not None else "")
        + "\n"
        # 500 символов описания держат подпись к фото в лимите Telegram (1024)
        f"{_esc(row['description'][:500])}\n\n"
        f"<a href=\"{_esc(row['url'])}\">Открыть объявление</a>"
    )
    lid = row["id"]
    markup = {"inline_keyboard": [[
        {"text": "❓ Уточнить", "callback_data": f"ask:{lid}"},
        {"text": "🤝 Торговаться", "callback_data": f"bargain:{lid}"},
        {"text": "⏭ Пропустить", "callback_data": f"skip:{lid}"},
    ]]}
    return text, markup


def handle(conn: sqlite3.Connection, data: str) -> str:
    """Обрабатывает кнопку, возвращает короткий ответ-всплывашку."""
    action, _, lid = data.partition(":")
    if action == "skip":
        conn.execute("UPDATE listings SET status = 'skipped' WHERE id = ?", (int(lid),))
        conn.commit()
        return "Пропущено"
    # «Уточнить» / «Торговаться» — лот в работу, чтобы был виден в панели
    conn.execute("UPDATE listings SET status = 'negotiating' WHERE id = ?", (int(lid),))
    conn.commit()
    # TODO: черновики на румынском через LLM + guards.check_draft
    return "Взял в работу (видно в панели). Черновики сообщений — следующий шаг"
