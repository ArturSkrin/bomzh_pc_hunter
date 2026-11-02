"""Telegram через голый Bot API на httpx: карточки лотов и кнопки под ними."""

import json
import logging
import re
import sqlite3
from typing import Callable

import httpx

log = logging.getLogger("baraholka")
# Метка лота в тексте сообщения: по ней понимаю, к какому лоту ответ продавца (reply на карточку или черновик)
LOT_TAG = re.compile(r"#lot(\d+)")
# Продавцы пишут по-румынски латиницей; кириллица в reply — значит, это я говорю боту, что написать
CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")


class Bot:
    def __init__(self, token: str, chat_id: str, panel_url: str = ""):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.chat_id = str(chat_id)
        self.panel_url = panel_url
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

    def say(self, text: str, reply_to: int | None = None) -> None:
        self.call("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML",
                  disable_web_page_preview=True, **({"reply_to_message_id": reply_to} if reply_to else {}))

    def poll(self, conn: sqlite3.Connection, timeout: int, drafter: Callable | None = None) -> None:
        """Ждёт кнопок и сообщений до timeout секунд. drafter(lid, goal, text) пишет черновик."""
        for upd in self.call("getUpdates", offset=self.offset, timeout=timeout,
                             allowed_updates=["callback_query", "message"]):
            self.offset = upd["update_id"] + 1
            q, msg = upd.get("callback_query"), upd.get("message")
            # Чужие нажатия и сообщения игнорируем: бот публичный, а база — моя
            if q and str(q["message"]["chat"]["id"]) == self.chat_id:
                # Ответ на кнопку — сразу: Telegram ждёт его секунды, а черновик пишется дольше
                self.call("answerCallbackQuery", callback_query_id=q["id"], text=handle(conn, q["data"]))
                action, _, lid = q["data"].partition(":")
                if action == "skip":
                    self.call("editMessageReplyMarkup", chat_id=self.chat_id,
                              message_id=q["message"]["message_id"], reply_markup={"inline_keyboard": []})
                elif drafter:
                    self.send_draft(drafter, int(lid), action)
            elif msg and str(msg["chat"]["id"]) == self.chat_id and msg.get("text"):
                parent = msg.get("reply_to_message") or {}
                tag = LOT_TAG.search(parent.get("text") or parent.get("caption") or "")
                if not tag:
                    self.say("Ответь (reply) на карточку или черновик, чтобы я понял, к какому лоту. "
                             "По-русски — что написать продавцу, по-румынски — его ответ.")
                elif drafter:
                    goal = "say" if CYRILLIC.search(msg["text"]) else "reply"
                    self.send_draft(drafter, int(tag[1]), goal, msg["text"])

    def send_draft(self, drafter: Callable, lid: int, goal: str, text: str | None = None) -> None:
        try:
            m = drafter(lid, goal, text)
        except Exception as e:
            log.exception("Черновик для лота %s не написался", lid)
            self.say(f"Не получилось написать черновик ({type(e).__name__}), подробности в логе.")
            return
        self.say(draft_text(lid, m, self.panel_url))


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
        f"<a href=\"{_esc(row['url'])}\">Открыть объявление</a> #lot{row['id']}"
    )
    lid = row["id"]
    markup = {"inline_keyboard": [[
        {"text": "❓ Уточнить", "callback_data": f"ask:{lid}"},
        {"text": "🤝 Торговаться", "callback_data": f"bargain:{lid}"},
        {"text": "⏭ Пропустить", "callback_data": f"skip:{lid}"},
    ]]}
    return text, markup


def draft_text(lid: int, m: sqlite3.Row, panel_url: str = "") -> str:
    """Черновик в Telegram: текст в <code> — копируется одним нажатием."""
    flags = "".join(f"⚠️ {_esc(f['rule'])}: {_esc(f['detail'])}\n" for f in json.loads(m["flags"]))
    return (
        f"✍️ Черновик продавцу #lot{lid}\n\n<code>{_esc(m['text'])}</code>\n\n"
        + (f"<i>{_esc(m['note'])}</i>\n" if m["note"] else "")
        + flags
        + "\nReply на это сообщение: ответ продавца (по-румынски) или что написать (по-русски)."
        + (f"\nПереписка: {panel_url}/listing/{lid}" if panel_url else "")
    )


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
    return "Взял в работу, пишу черновик…"
