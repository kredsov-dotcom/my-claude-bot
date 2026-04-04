#!/usr/bin/env python3
"""
Персональный Claude AI Telegram Бот
Telegram → Claude API + SQLite память + файлы/фото
"""

import asyncio
import os
import sqlite3
import json
import base64
import logging
import io
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
import anthropic

BOT_TOKEN        = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OWNER_ID         = int(os.environ.get("OWNER_ID", "0"))
DB_PATH          = os.environ.get("DB_PATH", "memory.db")
MODEL            = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_HISTORY      = 30

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            salience REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"БД инициализирована: {DB_PATH}")

def get_conn():
    return sqlite3.connect(DB_PATH)

def get_memories(limit=20):
    with get_conn() as c:
        return c.execute("SELECT content, category, created_at FROM memories ORDER BY salience DESC, created_at DESC LIMIT ?", (limit,)).fetchall()

def save_memory(content, category="general", salience=1.0):
    with get_conn() as c:
        c.execute("INSERT INTO memories (content, category, salience) VALUES (?, ?, ?)", (content, category, salience))
        c.commit()

def get_history(limit=MAX_HISTORY):
    with get_conn() as c:
        rows = c.execute("SELECT role, content FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return list(reversed(rows))

def save_history(role, content):
    with get_conn() as c:
        c.execute("INSERT INTO history (role, content) VALUES (?, ?)", (role, str(content)))
        c.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 200)")
        c.commit()

def clear_history():
    with get_conn() as c:
        c.execute("DELETE FROM history")
        c.commit()

def get_system_prompt():
    from pathlib import Path
    for path in ["CLAUDE.md", "/app/CLAUDE.md"]:
        p = Path(path)
        if p.exists():
            base = p.read_text(encoding="utf-8")
            break
    else:
        base = "Ты — персональный AI-ассистент. Отвечай чётко и по делу."
    memories = get_memories()
    if memories:
        base += "\n\n## Активные воспоминания\n" + "\n".join(f"- [{m[1]}] {m[0]}" for m in memories)
    base += f"\n\n## Текущее время\n{datetime.now().strftime('%d.%m.%Y %H:%M')}"
    return base

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

async def ask_claude(messages):
    try:
        r = claude_client.messages.create(model=MODEL, max_tokens=4096, system=get_system_prompt(), messages=messages)
        return r.content[0].text
    except Exception as e:
        return f"❌ Ошибка: {e}"

def build_messages(new_role, new_content):
    history = get_history()
    messages = []
    for role, content in history:
        try:
            messages.append({"role": role, "content": json.loads(content)})
        except:
            messages.append({"role": role, "content": content})
    messages.append({"role": new_role, "content": new_content})
    return messages

def is_authorized(update):
    return OWNER_ID == 0 or update.effective_user.id == OWNER_ID

async def deny(update):
    await update.message.reply_text("⛔ Доступ запрещён.")

async def send_long(update, text):
    if len(text) <= 4000:
        await update.message.reply_text(text)
        return
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])

async def cmd_start(update, context):
    if not is_authorized(update): return await deny(update)
    uid = update.effective_user.id
    await update.message.reply_text(f"👋 Привет! Ассистент запущен.\nТвой Telegram ID: `{uid}`\n\n/memory — воспоминания\n/checkpoint — сохранить контекст\n/status — статус\n/clear — очистить историю", parse_mode="Markdown")

async def cmd_memory(update, context):
    if not is_authorized(update): return await deny(update)
    memories = get_memories()
    if not memories:
        await update.message.reply_text("📭 Воспоминаний пока нет.")
        return
    lines = ["🧠 *Воспоминания:*\n"]
    for i, (c, cat, dt) in enumerate(memories, 1):
        lines.append(f"*{i}.* [{cat}] {c}\n_({dt[:10]})_\n")
    await send_long(update, "\n".join(lines))

async def cmd_checkpoint(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.reply_text("💾 Создаю checkpoint...")
    history = get_history(20)
    if not history:
        await update.message.reply_text("Нет истории.")
        return
    hist_text = "\n".join(f"{r.upper()}: {c[:300]}" for r, c in history)
    summary = await ask_claude([{"role": "user", "content": f"Сделай резюме 3-5 пунктов ключевых решений:\n\n{hist_text}"}])
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    save_memory(f"[Checkpoint {ts}]\n{summary}", category="checkpoint", salience=5.0)
    await update.message.reply_text(f"✅ Сохранено:\n\n{summary}\n\n_Можно начать новый чат._", parse_mode="Markdown")

async def cmd_status(update, context):
    if not is_authorized(update): return await deny(update)
    with get_conn() as c:
        mc = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        hc = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    await update.message.reply_text(f"📊 *Статус*\n\n🧠 Воспоминаний: {mc}\n💬 История: {hc}\n🤖 Модель: `{MODEL}`", parse_mode="Markdown")

async def cmd_clear(update, context):
    if not is_authorized(update): return await deny(update)
    clear_history()
    await update.message.reply_text("🗑 История очищена. Память сохранена.")

async def handle_text(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.chat.send_action("typing")
    user_text = update.message.text
    messages = build_messages("user", user_text)
    save_history("user", user_text)
    response = await ask_claude(messages)
    save_history("assistant", response)
    await send_long(update, response)

async def handle_photo(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.chat.send_action("typing")
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    buf.seek(0)
    image_b64 = base64.b64encode(buf.read()).decode()
    caption = update.message.caption or "Что на фото?"
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}, {"type": "text", "text": caption}]
    messages = build_messages("user", content)
    save_history("user", json.dumps([{"type": "image", "caption": caption}]))
    response = await ask_claude(messages)
    save_history("assistant", response)
    await send_long(update, response)

async def handle_document(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.chat.send_action("typing")
    doc = update.message.document
    tg_file = await context.bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    buf.seek(0)
    raw = buf.read()
    file_name = doc.file_name or "document"
    try:
        file_text = raw.decode("utf-8")
        file_content = f"Файл: {file_name}\n\n{file_text[:8000]}"
    except:
        file_content = f"Файл: {file_name} (бинарный)"
    caption = update.message.caption or "Проанализируй."
    user_text = f"{caption}\n\n{file_content}"
    messages = build_messages("user", user_text)
    save_history("user", f"[Документ: {file_name}]")
    response = await ask_claude(messages)
    save_history("assistant", response)
    await send_long(update, response)

async def handle_voice(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.reply_text("🎤 Голосовые пока не поддерживаются. Отправьте текстом.")

def main():
    init_db()
    logger.info(f"Запуск | модель: {MODEL} | owner_id: {OWNER_ID}")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("checkpoint", cmd_checkpoint))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("savelife", cmd_checkpoint))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
