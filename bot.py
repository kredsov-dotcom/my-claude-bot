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

# ─── КОНФИГУРАЦИЯ ─────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OWNER_ID         = int(os.environ.get("OWNER_ID", "0"))   # ваш Telegram ID
DB_PATH          = os.environ.get("DB_PATH", "memory.db")
MODEL            = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_HISTORY      = 30   # сообщений в контексте

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── БАЗА ДАННЫХ ──────────────────────────────────────────────────────────────
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content    TEXT    NOT NULL,
            category   TEXT    DEFAULT 'general',
            salience   REAL    DEFAULT 1.0,
            created_at TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
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
        return c.execute(
            "SELECT content, category, created_at FROM memories "
            "ORDER BY salience DESC, created_at DESC LIMIT ?", (limit,)
        ).fetchall()

def save_memory(content, category="general", salience=1.0):
    with get_conn() as c:
        c.execute(
            "INSERT INTO memories (content, category, salience) VALUES (?, ?, ?)",
            (content, category, salience)
        )
        c.commit()

def get_history(limit=MAX_HISTORY):
    with get_conn() as c:
        rows = c.execute(
            "SELECT role, content FROM history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return list(reversed(rows))

def save_history(role, content):
    with get_conn() as c:
        c.execute(
            "INSERT INTO history (role, content) VALUES (?, ?)",
            (role, str(content))
        )
        # Оставляем последние 200 записей
        c.execute(
            "DELETE FROM history WHERE id NOT IN "
            "(SELECT id FROM history ORDER BY id DESC LIMIT 200)"
        )
        c.commit()

def clear_history():
    with get_conn() as c:
        c.execute("DELETE FROM history")
        c.commit()

# ─── СИСТЕМНЫЙ ПРОМПТ ─────────────────────────────────────────────────────────
def get_system_prompt():
    # Загружаем CLAUDE.md
    for path in ["CLAUDE.md", "/app/CLAUDE.md"]:
        p = Path(path)
        if p.exists():
            base = p.read_text(encoding="utf-8")
            break
    else:
        base = (
            "Ты — персональный AI-ассистент. "
            "Отвечай чётко, по делу, без лишней воды. "
            "Ты проактивен: если видишь проблему — говоришь об этом сразу."
        )

    # Добавляем активные воспоминания
    memories = get_memories()
    if memories:
        mem_lines = "\n".join(
            f"- [{m[1]}] {m[0]}" for m in memories
        )
        base += f"\n\n## Активные воспоминания\n{mem_lines}"

    # Добавляем текущую дату/время
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    base += f"\n\n## Текущее время\n{now}"

    return base

# ─── CLAUDE API ───────────────────────────────────────────────────────────────
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

async def ask_claude(messages: list) -> str:
    try:
        response = claude_client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=get_system_prompt(),
            messages=messages,
        )
        return response.content[0].text
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        return f"❌ Ошибка API: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return f"❌ Ошибка: {str(e)}"

def build_messages(new_role: str, new_content) -> list:
    """Строит список сообщений: история + новое сообщение"""
    history = get_history()
    messages = []
    for role, content in history:
        # Фото/документы сохранены как текст-заглушка — добавляем как текст
        messages.append({"role": role, "content": content})
    messages.append({"role": new_role, "content": new_content})
    return messages

# ─── АВТОРИЗАЦИЯ ──────────────────────────────────────────────────────────────
def is_authorized(update: Update) -> bool:
    if OWNER_ID == 0:
        return True
    return update.effective_user.id == OWNER_ID

async def deny(update: Update):
    await update.message.reply_text("⛔ Доступ запрещён.")

# ─── ОТПРАВКА ДЛИННЫХ СООБЩЕНИЙ ───────────────────────────────────────────────
async def send_long(update: Update, text: str):
    """Разбивает длинные сообщения на части"""
    if len(text) <= 4000:
        await update.message.reply_text(text)
        return
    parts = []
    while text:
        parts.append(text[:4000])
        text = text[4000:]
    for part in parts:
        await update.message.reply_text(part)

# ─── КОМАНДЫ ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    uid = update.effective_user.id
    await update.message.reply_text(
        f"👋 Привет! Ассистент запущен.\n"
        f"Твой Telegram ID: `{uid}`\n\n"
        f"Команды:\n"
        f"/memory — воспоминания\n"
        f"/checkpoint — сохранить контекст сессии\n"
        f"/status — статус системы\n"
        f"/clear — очистить историю диалога",
        parse_mode="Markdown"
    )

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    memories = get_memories()
    if not memories:
        await update.message.reply_text("📭 Воспоминаний пока нет.")
        return
    lines = ["🧠 *Воспоминания:*\n"]
    for i, (content, category, created_at) in enumerate(memories, 1):
        date = created_at[:10] if created_at else "?"
        lines.append(f"*{i}.* [{category}] {content}\n_({date})_\n")
    await send_long(update, "\n".join(lines))

async def cmd_checkpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    await update.message.reply_text("💾 Создаю checkpoint...")

    history = get_history(20)
    if not history:
        await update.message.reply_text("Нет истории для сохранения.")
        return

    history_text = "\n".join(
        f"{role.upper()}: {content[:300]}" for role, content in history
    )
    prompt = (
        "Сделай краткое резюме (3-5 пунктов) ключевых решений, "
        "фактов и договорённостей из этого разговора:\n\n" + history_text
    )
    summary = await ask_claude([{"role": "user", "content": prompt}])

    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    save_memory(
        f"[Checkpoint {timestamp}]\n{summary}",
        category="checkpoint",
        salience=5.0
    )

    await update.message.reply_text(
        f"✅ Checkpoint сохранён:\n\n{summary}\n\n"
        f"_Теперь можно начать новый чат — контекст сохранён._",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    with get_conn() as c:
        mem_count  = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        hist_count = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]

    await update.message.reply_text(
        f"📊 *Статус ассистента*\n\n"
        f"🧠 Воспоминаний: {mem_count}\n"
        f"💬 Записей в истории: {hist_count}\n"
        f"🤖 Модель: `{MODEL}`\n"
        f"🗄 БД: `{DB_PATH}`\n"
        f"⏰ Сейчас: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        parse_mode="Markdown"
    )

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    clear_history()
    await update.message.reply_text(
        "🗑 История диалога очищена.\n"
        "Долгосрочная память (воспоминания) сохранена."
    )

# ─── ОБРАБОТЧИКИ СООБЩЕНИЙ ────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)

    user_text = update.message.text
    await update.message.chat.send_action("typing")

    messages = build_messages("user", user_text)
    save_history("user", user_text)

    response = await ask_claude(messages)
    save_history("assistant", response)

    await send_long(update, response)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    await update.message.chat.send_action("typing")

    # Берём фото наилучшего качества
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)

    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    buf.seek(0)
    image_b64 = base64.b64encode(buf.read()).decode()

    caption = (
        update.message.caption
        or "Что на фото? Опиши подробно и помоги, если нужно."
    )

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": image_b64,
            },
        },
        {"type": "text", "text": caption},
    ]

    messages = build_messages("user", content)
    # В историю сохраняем текстовую заглушку (без base64)
    save_history("user", f"[Фото] {caption}")

    response = await ask_claude(messages)
    save_history("assistant", response)

    await send_long(update, response)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    await update.message.chat.send_action("typing")

    doc = update.message.document
    tg_file = await context.bot.get_file(doc.file_id)

    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    buf.seek(0)
    raw = buf.read()

    file_name = doc.file_name or "document"

    # Пробуем читать как текст
    try:
        file_text = raw.decode("utf-8")
        file_content = f"Файл: {file_name}\n\nСодержимое:\n{file_text[:8000]}"
        if len(file_text) > 8000:
            file_content += "\n\n[...файл обрезан, показаны первые 8000 символов]"
    except UnicodeDecodeError:
        file_content = f"Файл: {file_name} (бинарный, размер: {len(raw)} байт)"

    caption = update.message.caption or "Проанализируй этот документ."
    user_text = f"{caption}\n\n{file_content}"

    messages = build_messages("user", user_text)
    save_history("user", f"[Документ: {file_name}] {caption}")

    response = await ask_claude(messages)
    save_history("assistant", response)

    await send_long(update, response)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    await update.message.reply_text(
        "🎤 Голосовые сообщения пока не поддерживаются.\n"
        "Отправьте текстом или документом."
    )

# ─── ЗАПУСК ───────────────────────────────────────────────────────────────────
def main():
    init_db()
    logger.info(f"Запуск бота | модель: {MODEL} | owner_id: {OWNER_ID}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("memory",     cmd_memory))
    app.add_handler(CommandHandler("checkpoint", cmd_checkpoint))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("savelife",   cmd_checkpoint))  # алиас

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO,        handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VOICE,        handle_voice))

    logger.info("Бот запущен и ожидает сообщений...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
