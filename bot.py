#!/usr/bin/env python3
"""Персональный Claude AI Telegram Бот"""

import os, sqlite3, base64, logging, io, tempfile, subprocess
from datetime import datetime
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic

BOT_TOKEN         = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OWNER_ID          = int(os.environ.get("OWNER_ID", "0"))
DB_PATH           = os.environ.get("DB_PATH", "memory.db")
MODEL             = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_HISTORY       = 30

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir: os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
        category TEXT DEFAULT 'general', salience REAL DEFAULT 1.0,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL,
        content TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')))""")
    conn.commit(); conn.close()
    logger.info(f"БД инициализирована: {DB_PATH}")

def get_conn(): return sqlite3.connect(DB_PATH)

def get_memories(limit=20):
    with get_conn() as c:
        return c.execute("SELECT content, category, created_at FROM memories ORDER BY salience DESC, created_at DESC LIMIT ?", (limit,)).fetchall()

def save_memory(content, category="general", salience=1.0):
    with get_conn() as c:
        c.execute("INSERT INTO memories (content, category, salience) VALUES (?, ?, ?)", (content, category, salience)); c.commit()

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
    with get_conn() as c: c.execute("DELETE FROM history"); c.commit()

def get_system_prompt():
    for path in ["CLAUDE.md", "/app/CLAUDE.md"]:
        p = Path(path)
        if p.exists(): base = p.read_text(encoding="utf-8"); break
    else:
        base = "Ты — персональный AI-ассистент. Отвечай чётко, по делу. Ты проактивен."
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
    except anthropic.APIError as e:
        logger.error(f"API error: {e}"); return f"❌ Ошибка API: {e}"
    except Exception as e:
        logger.error(f"Error: {e}"); return f"❌ Ошибка: {e}"

def build_messages(new_role, new_content):
    messages = [{"role": r, "content": c} for r, c in get_history()]
    messages.append({"role": new_role, "content": new_content})
    return messages

def is_authorized(update): return OWNER_ID == 0 or update.effective_user.id == OWNER_ID
async def deny(update): await update.message.reply_text("⛔ Доступ запрещён.")

async def send_long(update, text):
    if len(text) <= 4000: await update.message.reply_text(text); return
    for i in range(0, len(text), 4000): await update.message.reply_text(text[i:i+4000])

async def cmd_start(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.reply_text(
        f"👋 Привет! Ассистент запущен.\nID: `{update.effective_user.id}`\n\n"
        "/memory /checkpoint /status /clear", parse_mode="Markdown")

async def cmd_memory(update, context):
    if not is_authorized(update): return await deny(update)
    mems = get_memories()
    if not mems: await update.message.reply_text("📭 Воспоминаний нет."); return
    lines = ["🧠 *Воспоминания:*\n"] + [f"*{i}.* [{c}] {t}\n_({(d or '?')[:10]})_\n" for i,(t,c,d) in enumerate(mems,1)]
    await send_long(update, "\n".join(lines))

async def cmd_checkpoint(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.reply_text("💾 Создаю checkpoint...")
    history = get_history(20)
    if not history: await update.message.reply_text("Нет истории."); return
    ht = "\n".join(f"{r.upper()}: {c[:300]}" for r,c in history)
    summary = await ask_claude([{"role":"user","content":f"Резюме (3-5 пунктов):\n\n{ht}"}])
    save_memory(f"[Checkpoint {datetime.now().strftime('%d.%m.%Y %H:%M')}]\n{summary}", "checkpoint", 5.0)
    await update.message.reply_text(f"✅ Сохранено:\n\n{summary}", parse_mode="Markdown")

async def cmd_status(update, context):
    if not is_authorized(update): return await deny(update)
    with get_conn() as c:
        mc = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        hc = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    await update.message.reply_text(
        f"📊 *Статус*\n🧠 Воспоминаний: {mc}\n💬 История: {hc}\n🤖 `{MODEL}`\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}", parse_mode="Markdown")

async def cmd_clear(update, context):
    if not is_authorized(update): return await deny(update)
    clear_history(); await update.message.reply_text("🗑 История очищена.")

async def handle_text(update, context):
    if not is_authorized(update): return await deny(update)
    txt = update.message.text
    await update.message.chat.send_action("typing")
    msgs = build_messages("user", txt); save_history("user", txt)
    resp = await ask_claude(msgs); save_history("assistant", resp)
    await send_long(update, resp)

async def handle_photo(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.chat.send_action("typing")
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    buf = io.BytesIO(); await tg_file.download_to_memory(buf); buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    caption = update.message.caption or "Что на фото? Опиши подробно."
    content = [{"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},{"type":"text","text":caption}]
    msgs = build_messages("user", content); save_history("user", f"[Фото] {caption}")
    resp = await ask_claude(msgs); save_history("assistant", resp)
    await send_long(update, resp)

VEHICLE_INSPECTION_PROMPT = """Ты — система технического контроля транспортного средства.
Тебе предоставлены кадры из видео кругового осмотра автомобиля перед выездом.
Проанализируй каждый кадр и составь структурированный отчёт:

1. ФАРЫ И СВЕТОВЫЕ ПРИБОРЫ
   - Работают ли передние фары?
   - Работают ли задние фонари и стоп-сигналы?
   - Работает ли аварийная сигнализация (мигают ли все 4 поворотника)?
   - Исправны ли габаритные огни?

2. КУЗОВ И ВНЕШНИЙ ВИД
   - Видимые повреждения, вмятины, царапины
   - Целостность стёкол и зеркал

3. КОЛЁСА И ШИНЫ
   - Визуальное состояние шин (спущенные, трещины, износ)
   - Состояние дисков

4. ТЕНТ / ГРУЗОВОЙ ОТСЕК (если есть)
   - Целостность тента, наличие разрывов или повреждений
   - Состояние крепёжных ремней и дуг
   - Закрыт ли борт/задняя дверь

5. ДВИГАТЕЛЬ (по видимым признакам)
   - Цвет и количество дыма из выхлопа
   - Видимые протечки под автомобилем

6. ИТОГОВОЕ ЗАКЛЮЧЕНИЕ
   - ✅ ДОПУЩЕН К ВЫЕЗДУ / ❌ НЕ ДОПУЩЕН
   - Перечень выявленных нарушений и замечаний

Если что-то не видно или плохо различимо на кадрах — честно укажи это."""

async def extract_video_frames(video_path, max_frames=12):
    frames = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_pattern = os.path.join(tmp_dir, "frame_%03d.jpg")
        try:
            result = subprocess.run(
                ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",video_path],
                capture_output=True, text=True, timeout=30)
            duration = float(result.stdout.strip() or "10")
        except Exception: duration = 10.0
        interval = max(1.0, duration / max_frames)
        try:
            subprocess.run(
                ["ffmpeg","-i",video_path,"-vf",f"fps=1/{interval:.1f}","-vframes",str(max_frames),"-q:v","3",out_pattern],
                capture_output=True, timeout=60)
        except Exception as e:
            logger.error(f"ffmpeg error: {e}"); return []
        for f in sorted(Path(tmp_dir).glob("frame_*.jpg"))[:max_frames]:
            frames.append(base64.b64encode(f.read_bytes()).decode())
    return frames

async def handle_video(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.reply_text("🎬 Получил видео. Извлекаю кадры и анализирую — подождите 30–60 сек...")
    await update.message.chat.send_action("typing")
    video = update.message.video or update.message.document
    if not video: await update.message.reply_text("❌ Не удалось получить видеофайл."); return
    tg_file = await context.bot.get_file(video.file_id)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp: tmp_path = tmp.name
    try:
        await tg_file.download_to_drive(tmp_path)
        frames = await extract_video_frames(tmp_path, max_frames=12)
        if not frames:
            await update.message.reply_text("❌ Не удалось извлечь кадры. Попробуйте MP4 или MOV."); return
        await update.message.reply_text(f"✅ Кадров извлечено: {len(frames)}. Анализирую...")
        caption = update.message.caption or ""
        prompt = f"{caption}\n\n{VEHICLE_INSPECTION_PROMPT}" if caption else VEHICLE_INSPECTION_PROMPT
        content = []
        for i, b64 in enumerate(frames):
            content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}})
            content.append({"type":"text","text":f"[Кадр {i+1}/{len(frames)}]"})
        content.append({"type":"text","text":prompt})
        msgs = build_messages("user", content)
        save_history("user", f"[Видео осмотра авто, {len(frames)} кадров] {caption}")
        resp = await ask_claude(msgs); save_history("assistant", resp)
        await send_long(update, resp)
    finally:
        try: os.unlink(tmp_path)
        except: pass

async def handle_document(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.chat.send_action("typing")
    doc = update.message.document
    tg_file = await context.bot.get_file(doc.file_id)
    buf = io.BytesIO(); await tg_file.download_to_memory(buf); buf.seek(0); raw = buf.read()
    file_name = doc.file_name or "document"; mime_type = doc.mime_type or ""
    IMAGE_MIMES = {"image/jpeg","image/jpg","image/png","image/gif","image/webp"}
    is_image = mime_type in IMAGE_MIMES or file_name.lower().endswith((".jpg",".jpeg",".png",".gif",".webp"))
    caption = update.message.caption or ("Что на изображении?" if is_image else "Проанализируй этот документ.")
    if is_image:
        dm = mime_type if mime_type in IMAGE_MIMES else "image/jpeg"
        content = [{"type":"image","source":{"type":"base64","media_type":dm,"data":base64.b64encode(raw).decode()}},{"type":"text","text":caption}]
        msgs = build_messages("user", content); save_history("user", f"[Фото-файл: {file_name}] {caption}")
    else:
        try:
            ft = raw.decode("utf-8"); fc = f"Файл: {file_name}\n\n{ft[:8000]}"
            if len(ft)>8000: fc += "\n[...обрезан]"
        except: fc = f"Файл: {file_name} (бинарный, {len(raw)} байт)"
        ut = f"{caption}\n\n{fc}"; msgs = build_messages("user", ut)
        save_history("user", f"[Документ: {file_name}] {caption}")
    resp = await ask_claude(msgs); save_history("assistant", resp); await send_long(update, resp)

async def handle_voice(update, context):
    if not is_authorized(update): return await deny(update)
    await update.message.reply_text("🎤 Голосовые пока не поддерживаются. Отправьте текстом или файлом.")

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
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Бот запущен.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
