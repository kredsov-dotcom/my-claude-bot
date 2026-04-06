#!/usr/bin/env python3
"""
Персональный Claude AI Telegram Бот
Telegram → Claude API + SQLite память + файлы/фото + Агент Q (контроль ТС)
"""

import asyncio
import os
import sqlite3
import json
import base64
import logging
import io
import re
import tempfile
import subprocess
from datetime import datetime, date, timedelta
from pathlib import Path

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
import anthropic

# ─── КОНФИГУРАЦИЯ ─────────────────────────────────────────────────────────────
BOT_TOKEN         = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OWNER_ID          = int(os.environ.get("OWNER_ID", "0"))
DB_PATH           = os.environ.get("DB_PATH", "memory.db")
MODEL             = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_HISTORY       = 30

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── СОСТОЯНИЕ АГЕНТА Q (в памяти) ───────────────────────────────────────────
agent_state = {
    "active_vehicle": None,   # текущий а/м для следующих документов
    "awaiting": None,         # ожидаемый тип документа (если задан вручную)
}

# ─── МОНИТОРИНГ: КОНСТАНТЫ ────────────────────────────────────────────────────
MSK = pytz.timezone("Europe/Moscow")

# Татавтоматизация: 9 автомобилей — реестр присылают каждое утро
TATAVTO_VEHICLES = {"178", "539", "557", "029", "516", "072", "959", "629", "642"}

# Автомобили с фиксированным графиком
UPTJ_VEHICLES   = {"898", "321"}   # УПТЖ: 5 раб / 2 вых + праздники РФ и РТ
TATBUR_VEHICLES = {"626"}           # ТатбурНефть: без выходных
KRS_VEHICLES    = {"641"}           # КРС Сервис: кроме пятницы

# Опорная дата для цикла УПТЖ (первый день рабочего блока 5/2)
# Цикл: 5 рабочих + 2 выходных = 7 дней, повторяется независимо от дня недели
_UPTJ_REF = date(2025, 4, 7)   # понедельник — при необходимости настроить через /setuptjref

# ─── Праздники РФ + Татарстан ─────────────────────────────────────────────────
_RF_HOLIDAYS: set[str] = {
    # 2025
    "2025-01-01","2025-01-02","2025-01-03","2025-01-04","2025-01-05",
    "2025-01-06","2025-01-07","2025-01-08",
    "2025-02-24",                      # Перенос: 23.02 вс → 24.02 пн
    "2025-03-10",                      # Перенос: 8.03 сб → 10.03 пн
    "2025-04-30","2025-05-01","2025-05-02",
    "2025-05-08","2025-05-09",
    "2025-06-12","2025-06-13",
    "2025-11-03","2025-11-04",
    "2025-12-31",
    # 2026
    "2026-01-01","2026-01-02","2026-01-03","2026-01-04","2026-01-05",
    "2026-01-06","2026-01-07","2026-01-08","2026-01-09",
    "2026-02-23",
    "2026-03-09",
    "2026-05-01","2026-05-11",
    "2026-06-12",
    "2026-11-04",
}
_RT_HOLIDAYS: set[str] = _RF_HOLIDAYS | {"2025-08-30", "2026-08-30"}

def _is_holiday(d: date) -> bool:
    return d.isoformat() in _RT_HOLIDAYS

def is_uptj_working(d: date) -> bool:
    """898, 321: цикл 5/2, праздники РФ+РТ — выходной."""
    if _is_holiday(d):
        return False
    pos = (d - _UPTJ_REF).days % 7
    return 0 <= pos <= 4

def is_tatbur_working(_d: date) -> bool:
    """626: без выходных."""
    return True

def is_krs_working(d: date) -> bool:
    """641: пятница — выходной."""
    return d.weekday() != 4  # 0=пн … 4=пт

# ─── БАЗА ДАННЫХ ──────────────────────────────────────────────────────────────
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # Основная память
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
    # Таблицы Агента Q
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            vehicle_num TEXT NOT NULL,
            driver      TEXT DEFAULT '',
            customer    TEXT DEFAULT '',
            status      TEXT DEFAULT 'active',
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_docs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id   INTEGER NOT NULL,
            doc_type   TEXT NOT NULL,
            result     TEXT DEFAULT 'pending',
            analysis   TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Реестр автомобилей Татавтоматизации
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_registry (
            date        TEXT PRIMARY KEY,
            raw_text    TEXT NOT NULL,
            vehicles_json TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
    """)
    # Журнал отправленных алертов (не спамить повторно)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts_sent (
            key         TEXT PRIMARY KEY,
            sent_at     TEXT NOT NULL
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
        c.execute(
            "DELETE FROM history WHERE id NOT IN "
            "(SELECT id FROM history ORDER BY id DESC LIMIT 200)"
        )
        c.commit()

def clear_history():
    with get_conn() as c:
        c.execute("DELETE FROM history")
        c.commit()

# ─── МОНИТОРИНГ: РЕЕСТР И АЛЕРТЫ ─────────────────────────────────────────────
def _msk_now() -> datetime:
    return datetime.now(MSK)

def _msk_today() -> date:
    return _msk_now().date()

def detect_registry(text: str) -> bool:
    """Возвращает True, если текст похож на реестр Татавтоматизации."""
    found = sum(1 for num in TATAVTO_VEHICLES if num in text)
    return found >= 3   # минимум 3 гос.номера из списка

def parse_registry(text: str) -> dict:
    """
    Парсит реестр вида:
        03.04.
        178 в 9:00 едет на монтаж
        539 выходной
    Возвращает: {"178": {"status": "working", "start_time": "09:00", "note": "..."}, ...}
    """
    vehicles: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for num in TATAVTO_VEHICLES:
            # Ищем номер в начале строки (с пробелами/точками до)
            m = re.search(r'(?<!\d)' + re.escape(num) + r'(?!\d)', line)
            if not m:
                continue
            rest = line[m.end():].strip()
            is_off = bool(re.search(r'выходн', rest, re.IGNORECASE))
            # Время старта: «в 9:00», «с 01:30», «с 9.00»
            t_match = re.search(r'(?:в|с)\s*(\d{1,2})[.:](\d{2})', rest)
            start_time = None
            if t_match and not is_off:
                h, mn = int(t_match.group(1)), int(t_match.group(2))
                start_time = f"{h:02d}:{mn:02d}"
            vehicles[num] = {
                "status": "off" if is_off else "working",
                "start_time": start_time,
                "note": rest[:200],
            }
            break
    return vehicles

def save_registry(raw: str, vehicles: dict, for_date: date):
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO daily_registry (date, raw_text, vehicles_json, received_at)"
            " VALUES (?, ?, ?, ?)",
            (for_date.isoformat(), raw,
             json.dumps(vehicles, ensure_ascii=False),
             _msk_now().isoformat())
        )
        c.commit()

def get_registry(for_date: date) -> dict | None:
    with get_conn() as c:
        row = c.execute(
            "SELECT vehicles_json FROM daily_registry WHERE date = ?",
            (for_date.isoformat(),)
        ).fetchone()
    return json.loads(row[0]) if row else None

# ── Алерты ────────────────────────────────────────────────────────────────────
def _alert_key(day: date, code: str) -> str:
    return f"{day.isoformat()}:{code}"

def alert_sent(day: date, code: str) -> bool:
    with get_conn() as c:
        return bool(c.execute(
            "SELECT 1 FROM alerts_sent WHERE key = ?", (_alert_key(day, code),)
        ).fetchone())

def mark_alert(day: date, code: str):
    with get_conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO alerts_sent (key, sent_at) VALUES (?, ?)",
            (_alert_key(day, code), _msk_now().isoformat())
        )
        c.commit()

async def _push(app, text: str):
    """Отправить владельцу сообщение."""
    await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode="Markdown")

# ── Задачи планировщика ───────────────────────────────────────────────────────
async def job_check_registry(app):
    """09:10 МСК — проверить, получен ли реестр Татавтоматизации."""
    today = _msk_today()
    if get_registry(today) is None:
        if not alert_sent(today, "registry_missing"):
            await _push(app,
                f"⚠️ *Реестр не получен!*\n"
                f"Татавтоматизация не прислала реестр на "
                f"{today.strftime('%d.%m.%Y')} к 09:10 МСК."
            )
            mark_alert(today, "registry_missing")

async def job_check_inspections(app):
    """Каждые 15 мин — алерт если прошёл час с выезда, а самоконтроля нет."""
    now = _msk_now()
    today = now.date()
    registry = get_registry(today)
    if registry:
        for num, info in registry.items():
            if info["status"] != "working" or not info.get("start_time"):
                continue
            try:
                h, mn = map(int, info["start_time"].split(":"))
                start_dt = MSK.localize(
                    datetime(today.year, today.month, today.day, h, mn))
                elapsed_h = (now - start_dt).total_seconds() / 3600
                if elapsed_h < 1.0:
                    continue
                # Есть ли смена и хоть один документ?
                shift_id = _shift_id_for(num, today)
                if shift_id and not _has_inspection(shift_id):
                    code = f"insp_{num}"
                    if not alert_sent(today, code):
                        await _push(app,
                            f"🚨 *Нет самоконтроля!*\n"
                            f"🚗 А/м: *{num}* (Татавтоматизация)\n"
                            f"⏰ Выехал в {info['start_time']} МСК — прошёл уже час.\n"
                            f"📋 Отчёт о самоконтроле не поступал."
                        )
                        mark_alert(today, code)
            except Exception as e:
                logger.error(f"job_check_inspections [{num}]: {e}")

async def job_check_fixed(app):
    """09:00 МСК — проверить автомобили с фиксированным графиком."""
    today = _msk_today()
    checks = [
        (UPTJ_VEHICLES,   is_uptj_working,   "УПТЖ"),
        (TATBUR_VEHICLES, is_tatbur_working,  "ТатбурНефть"),
        (KRS_VEHICLES,    is_krs_working,     "КРС Сервис"),
    ]
    for vehicles, fn, org in checks:
        if not fn(today):
            continue
        for num in vehicles:
            shift_id = _shift_id_for(num, today)
            if shift_id and not _has_inspection(shift_id):
                code = f"fixed_{num}"
                if not alert_sent(today, code):
                    await _push(app,
                        f"⚠️ *Нет самоконтроля к 09:00!*\n"
                        f"🏢 Организация: {org}\n"
                        f"🚗 А/м: *{num}*\n"
                        f"📅 Сегодня рабочий день — самоконтроль не поступал."
                    )
                    mark_alert(today, code)

def _shift_id_for(vehicle_num: str, for_date: date) -> int | None:
    """ID смены для автомобиля на заданную дату (или None)."""
    with get_conn() as c:
        row = c.execute(
            "SELECT id FROM shifts WHERE vehicle_num = ? AND date = ? AND status = 'active'",
            (vehicle_num, for_date.isoformat())
        ).fetchone()
    return row[0] if row else None

def _has_inspection(shift_id: int) -> bool:
    """Есть ли хоть один документ самоконтроля в смене."""
    with get_conn() as c:
        cnt = c.execute(
            "SELECT COUNT(*) FROM vehicle_docs WHERE shift_id = ?", (shift_id,)
        ).fetchone()[0]
    return cnt > 0

def setup_scheduler(app) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=MSK)
    # 09:10 МСК — реестр Татавтоматизации
    scheduler.add_job(job_check_registry,  CronTrigger(hour=9,  minute=10, timezone=MSK), args=[app])
    # 09:00 МСК — фиксированные графики
    scheduler.add_job(job_check_fixed,     CronTrigger(hour=9,  minute=0,  timezone=MSK), args=[app])
    # Каждые 15 минут — проверка час-после-выезда
    scheduler.add_job(job_check_inspections, CronTrigger(minute="*/15",    timezone=MSK), args=[app])
    scheduler.start()
    logger.info("Планировщик мониторинга запущен")
    return scheduler

# ─── АГЕНТ Q: РАБОТА СО СМЕНОЙ ────────────────────────────────────────────────
DOC_TYPES = {
    "video":     "🎬 Видео кругового осмотра",
    "waybill":   "📋 Путевой лист",
    "dashboard": "📊 Приборная панель",
    "driver":    "👷 Водитель в спецодежде",
}
DOC_ORDER = ["video", "waybill", "dashboard", "driver"]

def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def get_today_shifts():
    """Возвращает список автомобилей в сегодняшней смене."""
    with get_conn() as c:
        return c.execute(
            "SELECT id, vehicle_num, driver, customer FROM shifts "
            "WHERE date = ? AND status = 'active' ORDER BY id",
            (today(),)
        ).fetchall()

def get_or_create_shift(vehicle_num: str, driver: str = "", customer: str = "") -> int:
    """Находит или создаёт запись смены для автомобиля на сегодня."""
    with get_conn() as c:
        row = c.execute(
            "SELECT id FROM shifts WHERE date = ? AND vehicle_num = ? AND status = 'active'",
            (today(), vehicle_num)
        ).fetchone()
        if row:
            return row[0]
        c.execute(
            "INSERT INTO shifts (date, vehicle_num, driver, customer) VALUES (?, ?, ?, ?)",
            (today(), vehicle_num, driver, customer)
        )
        c.commit()
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]

def get_vehicle_docs(shift_id: int) -> dict:
    """Возвращает словарь {doc_type: (result, analysis)} для смены."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT doc_type, result, analysis FROM vehicle_docs WHERE shift_id = ?",
            (shift_id,)
        ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}

def save_vehicle_doc(shift_id: int, doc_type: str, result: str, analysis: str):
    """Сохраняет или обновляет документ автомобиля."""
    with get_conn() as c:
        existing = c.execute(
            "SELECT id FROM vehicle_docs WHERE shift_id = ? AND doc_type = ?",
            (shift_id, doc_type)
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE vehicle_docs SET result = ?, analysis = ?, created_at = datetime('now') "
                "WHERE shift_id = ? AND doc_type = ?",
                (result, analysis, shift_id, doc_type)
            )
        else:
            c.execute(
                "INSERT INTO vehicle_docs (shift_id, doc_type, result, analysis) VALUES (?, ?, ?, ?)",
                (shift_id, doc_type, result, analysis)
            )
        c.commit()

def clear_today_shifts():
    """Удаляет все записи смены за сегодня (начало новой смены)."""
    with get_conn() as c:
        shift_ids = [row[0] for row in c.execute(
            "SELECT id FROM shifts WHERE date = ?", (today(),)
        ).fetchall()]
        if shift_ids:
            placeholders = ",".join("?" * len(shift_ids))
            c.execute(f"DELETE FROM vehicle_docs WHERE shift_id IN ({placeholders})", shift_ids)
        c.execute("DELETE FROM shifts WHERE date = ?", (today(),))
        c.commit()

def format_shift_status() -> str:
    """Форматирует текущий статус смены."""
    shifts = get_today_shifts()
    if not shifts:
        return (
            f"📋 Смена {datetime.now().strftime('%d.%m.%Y')}\n\n"
            "Автомобили не добавлены.\n"
            "Отправьте фото графика/справки диспетчера или используйте /set [номер]."
        )

    result_icon = {"ok": "✅", "fail": "❌", "pending": "⏳"}
    lines = [f"📋 *Смена {datetime.now().strftime('%d.%m.%Y')}*\n"]
    ready_count = 0

    for shift_id, vehicle_num, driver, customer in shifts:
        docs = get_vehicle_docs(shift_id)
        header = f"🚛 *{vehicle_num}*"
        if customer:
            header += f" — {customer}"
        if driver:
            header += f"\n   👤 {driver}"
        lines.append(header)

        all_ok = True
        for dt in DOC_ORDER:
            label = DOC_TYPES[dt]
            if dt in docs:
                icon = result_icon.get(docs[dt][0], "⏳")
                if docs[dt][0] != "ok":
                    all_ok = False
            else:
                icon = "⏳"
                all_ok = False
            lines.append(f"   {icon} {label}")

        if all_ok:
            ready_count += 1
        lines.append("")

    lines.append(f"✅ Готово: {ready_count}/{len(shifts)} автомобилей")
    active = agent_state["active_vehicle"]
    if active:
        lines.append(f"\n📍 Активный а/м: *{active}*")
    return "\n".join(lines)

# ─── ДЕТЕКТИРОВАНИЕ ТИПА ДОКУМЕНТА И НОМЕРА АВТО ──────────────────────────────
SCHEDULE_KEYWORDS = ["справка", "график", "расписание", "смена", "нарядов", "диспетчер"]
DOC_KEYWORDS = {
    "video":     ["видео", "осмотр", "круговой", "обход"],
    "waybill":   ["путевой", "путёвый", "путевка", "путёвка", "маршрут"],
    "dashboard": ["приборн", "панель", "щиток", "спидометр", "одометр", "dashboard"],
    "driver":    ["водитель", "спецодежд", "форм", "рабочая", "шеврон", "ваир"],
}

def detect_is_schedule(caption: str) -> bool:
    cl = caption.lower()
    return any(k in cl for k in SCHEDULE_KEYWORDS)

def detect_doc_type(caption: str) -> str | None:
    cl = caption.lower()
    for dt, keywords in DOC_KEYWORDS.items():
        if any(k in cl for k in keywords):
            return dt
    return None

# Паттерн российского номерного знака (упрощённый)
RU_PLATE_RE = re.compile(
    r'\b[АВЕКМНОРСТУХABEKMHOPCTYX]\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s*\d{2,3}\b',
    re.IGNORECASE | re.UNICODE
)
# Короткий вариант без региона
RU_PLATE_SHORT_RE = re.compile(
    r'\b[АВЕКМНОРСТУХABEKMHOPCTYX]\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\b',
    re.IGNORECASE | re.UNICODE
)

def detect_vehicle_num(caption: str) -> str | None:
    m = RU_PLATE_RE.search(caption)
    if m:
        return re.sub(r'\s+', '', m.group()).upper()
    m = RU_PLATE_SHORT_RE.search(caption)
    if m:
        return m.group().upper()
    return None

def extract_result(analysis: str) -> str:
    """Определяет итог анализа: ok / fail / pending."""
    a = analysis.upper()
    ok_markers = ["✅", "ДОПУЩЕН", "В НОРМЕ", "СООТВЕТСТВУЕТ", "ГОТОВ К ВЫЕЗДУ",
                  "В ПОРЯДКЕ", "РЕГЛАМЕНТ"]
    fail_markers = ["❌", "ЗАМЕЧАНИ", "НЕ ДОПУЩ", "НЕИСПРАВН", "ТРЕБУЕТ", "НАРУШЕНИ",
                    "ОТКЛОНЕНИ", "НЕСООТВЕТСТВ"]
    for m in ok_markers:
        if m in a:
            return "ok"
    for m in fail_markers:
        if m in a:
            return "fail"
    return "pending"

# ─── ПРОМПТЫ ──────────────────────────────────────────────────────────────────
VEHICLE_INSPECTION_PROMPT = """Ты — старший механик СК ВАИР. Тебе предоставлены кадры из видео кругового осмотра автомобиля перед выездом.
Проверь техническое состояние по каждому пункту и зафиксируй все отклонения:

1. 🔦 СВЕТОВЫЕ ПРИБОРЫ (обязательная проверка по видео)
   - Передние фары (ближний свет) — работают?
   - Задние фонари и стоп-сигналы — работают?
   - Поворотники (передние и задние) — работают?
   - Подсветка государственных номеров — работает?
   - Габаритные огни — исправны?

2. 🚿 ЧИСТОТА АВТОМОБИЛЯ
   - Общая чистота кузова: допустимо рабочее загрязнение, недопустимо — сильное загрязнение, грязь скрывающая повреждения
   - Государственные номера (передний и задний): читаемы и чисты? Загрязнённый/нечитаемый номер — замечание

3. 🚗 КУЗОВ И ОСТЕКЛЕНИЕ
   - Видимые повреждения: вмятины, царапины, деформации
   - Целостность стёкол (лобовое, боковые, заднее)

4. 🛞 КОЛЁСА И ШИНЫ
   - Состояние шин: спущенные, повреждения боковин, остаток протектора
   - Состояние дисков

5. 🏕️ ТЕНТ / ГРУЗОВОЙ ОТСЕК (если есть)
   - Целостность тента, наличие разрывов или дыр
   - Состояние крепежей, дуг, бортов

6. 💨 ДВИГАТЕЛЬ (по внешним признакам)
   - Дым из выхлопной трубы (цвет, интенсивность)
   - Видимые подтёки масла, охлаждающей жидкости под автомобилем

7. ✅ ЗАКЛЮЧЕНИЕ МЕХАНИКА
   - Допущен ли автомобиль к выезду?
   - Полный перечень выявленных замечаний (если есть)

Если какой-то элемент не попал в кадр — честно укажи это."""

WAYBILL_PROMPT = """Ты — старший механик СК ВАИР. Проверь ПУТЕВОЙ ЛИСТ на правильность заполнения.

Проверяй по пунктам:
1. 📅 ДАТА — указана ли, соответствует ли сегодняшнему числу?
2. 🔧 ОТМЕТКА МЕХАНИКА — есть ли подпись/штамп механика о допуске ТС к выезду?
3. 🏥 ОТМЕТКА МЕДИКА — есть ли подпись/штамп медика о допуске водителя?
4. 📍 НАЧАЛЬНЫЙ ПРОБЕГ — указан ли показатель одометра при выезде?
5. 📝 ЗАПОЛНЕННОСТЬ — заполнены ли все обязательные поля: маршрут, ФИО водителя, гос.номер, наименование организации?
6. 🔏 ПЕЧАТИ — проставлены ли необходимые печати?

Вынеси чёткое заключение механика:
✅ ПУТЕВОЙ ЛИСТ В ПОРЯДКЕ — если все ключевые поля заполнены корректно
❌ ЗАМЕЧАНИЯ: [конкретный перечень нарушений] — если есть ошибки или пропуски

Если изображение нечёткое или какой-то раздел не читается — укажи это отдельно."""

DASHBOARD_PROMPT = """Ты — старший механик СК ВАИР. Проверь фото ПРИБОРНОЙ ПАНЕЛИ автомобиля.

Проверяй по пунктам:
1. 🔢 ОДОМЕТР — зафиксируй показания пробега (обязательно назови цифры)
2. ⛽ УРОВЕНЬ ТОПЛИВА — достаточен ли? Менее 1/4 бака — замечание
3. 🌡️ ТЕМПЕРАТУРА ДВИГАТЕЛЯ — в норме? Стрелка не должна быть в красной зоне
4. ⏰ ВРЕМЯ НА ПАНЕЛИ — соответствует ли текущему времени? (отклонение более 5 минут — замечание)
5. ⚠️ ИНДИКАТОРЫ НЕИСПРАВНОСТЕЙ — горит ли Check Engine или другие предупреждения?
6. 🔋 АКБ — нет ли индикатора разряда аккумулятора?

Вынеси чёткое заключение механика:
✅ ПРИБОРНАЯ ПАНЕЛЬ В НОРМЕ — все параметры в допустимых пределах
❌ ЗАМЕЧАНИЯ: [конкретный перечень отклонений] — если есть проблемы

Если что-то не видно или нечитаемо на фото — укажи это."""

DRIVER_PROMPT = """Ты — старший механик СК ВАИР. Проверь фото ВОДИТЕЛЯ на соответствие требованиям охраны труда и регламенту ВАИР.

Проверяй по пунктам:
1. 👟 СПЕЦИАЛЬНАЯ ОБУВЬ — надета ли спецобувь?
   НАРУШЕНИЕ: кроссовки, кеды, туфли, сланцы — не допускаются. Должна быть рабочая/защитная обувь
2. 👕 СПЕЦОДЕЖДА — надет ли комплект рабочей одежды (комбинезон, куртка, жилет и т.п.)?
   Состояние: чистая, без явных загрязнений, разрывов и повреждений?
3. 🔖 ШЕВРОН / ЛОГОТИП ВАИР — виден ли на одежде шеврон или логотип «ВАИР»?
4. 🦺 КОМПЛЕКТНОСТЬ — все элементы спецодежды на месте?
5. 👤 ФИО ВОДИТЕЛЯ — если в контексте указано ФИО водителя из путевого листа/смены, укажи его в заключении. Это нужно для привязки фото к конкретному водителю.

Вынеси чёткое заключение механика:
✅ СООТВЕТСТВУЕТ РЕГЛАМЕНТУ — водитель в полной спецодежде ВАИР
❌ ЗАМЕЧАНИЯ: [конкретный перечень нарушений] — если есть несоответствия

Если что-то не видно на фото или водитель не попал в кадр полностью — укажи это."""

SCHEDULE_PARSE_PROMPT = """Ты — система разбора рабочих документов диспетчера.
Проанализируй этот документ (утреннюю справку диспетчера или график работы автомобилей).
Извлеки список автомобилей, которые работают сегодня.

Для каждого автомобиля укажи:
- Гос. номер или внутренний номер (vehicle_num)
- ФИО водителя (driver) — если указано, иначе пустая строка
- Заказчик / объект (customer): Татавтоматизация / КРС Сервис / УПТЖ / Татбурнефть / другой

Выведи результат СТРОГО в формате JSON (без лишнего текста вокруг):
{"vehicles": [{"num": "А123БВ116", "driver": "Иванов И.И.", "customer": "КРС Сервис"}, ...]}

Если документ не является графиком или справкой — верни:
{"vehicles": [], "error": "Не является расписанием"}"""

# ─── СИСТЕМНЫЙ ПРОМПТ ─────────────────────────────────────────────────────────
def get_system_prompt():
    for path in ["CLAUDE.md", "/app/CLAUDE.md"]:
        p = Path(path)
        if p.exists():
            base = p.read_text(encoding="utf-8")
            break
    else:
        base = (
            "Ты — Агент Q, старший механик транспортной компании СК ВАИР. "
            "Твоя задача — проверять правильность заполнения документов самоконтроля водителей "
            "и фиксировать все отклонения и нарушения. "
            "Ты строг, внимателен к деталям и хорошо знаешь регламенты ВАИР. "
            "Отвечай чётко, по делу: сначала — вердикт (допущен / есть замечания), "
            "затем — конкретный перечень нарушений или подтверждение соответствия. "
            "Не допускай расплывчатых формулировок — каждое замечание должно быть конкретным."
        )
    memories = get_memories()
    if memories:
        mem_lines = "\n".join(f"- [{m[1]}] {m[0]}" for m in memories)
        base += f"\n\n## Активные воспоминания\n{mem_lines}"
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    base += f"\n\n## Текущее время\n{now}"
    return base

# ─── CLAUDE API ───────────────────────────────────────────────────────────────
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

async def ask_claude(messages: list, system: str = None) -> str:
    try:
        response = claude_client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system or get_system_prompt(),
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
    history = get_history()
    messages = []
    for role, content in history:
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
    if len(text) <= 4000:
        await update.message.reply_text(text)
        return
    parts = []
    while text:
        parts.append(text[:4000])
        text = text[4000:]
    for part in parts:
        await update.message.reply_text(part)

# ─── КОМАНДЫ: ОСНОВНЫЕ ────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    uid = update.effective_user.id
    await update.message.reply_text(
        f"👋 Привет! Агент Q запущен.\n"
        f"Твой Telegram ID: `{uid}`\n\n"
        f"*Команды управления сменой:*\n"
        f"/shift — статус текущей смены\n"
        f"/report — полный отчёт по смене\n"
        f"/newshift — начать новую смену\n"
        f"/set [номер] — установить активный а/м\n\n"
        f"*Общие команды:*\n"
        f"/memory — воспоминания\n"
        f"/checkpoint — сохранить контекст\n"
        f"/status — статус системы\n"
        f"/clear — очистить историю диалога\n\n"
        f"*Как работать со сменой:*\n"
        f"1. Отправьте фото справки/графика диспетчера с подписью «справка»\n"
        f"2. Для каждого а/м отправляйте 4 документа с подписью:\n"
        f"   • «видео А123БВ» — видео кругового осмотра\n"
        f"   • «путевой А123БВ» — фото путевого листа\n"
        f"   • «приборная А123БВ» — фото панели приборов\n"
        f"   • «водитель А123БВ» — фото водителя в спецодежде\n"
        f"3. Или установите активный а/м через /set и отправляйте без подписи",
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
    save_memory(f"[Checkpoint {timestamp}]\n{summary}", category="checkpoint", salience=5.0)
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
        shift_count = c.execute(
            "SELECT COUNT(*) FROM shifts WHERE date = ?", (today(),)
        ).fetchone()[0]
    active = agent_state["active_vehicle"] or "не задан"
    await update.message.reply_text(
        f"📊 *Статус Агента Q*\n\n"
        f"🧠 Воспоминаний: {mem_count}\n"
        f"💬 Записей в истории: {hist_count}\n"
        f"🚛 Авто в смене сегодня: {shift_count}\n"
        f"📍 Активный а/м: {active}\n"
        f"🤖 Модель: `{MODEL}`\n"
        f"⏰ Сейчас: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        parse_mode="Markdown"
    )

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    clear_history()
    await update.message.reply_text(
        "🗑 История диалога очищена.\n"
        "Долгосрочная память и данные смены сохранены."
    )

# ─── КОМАНДЫ: АГЕНТ Q ─────────────────────────────────────────────────────────
async def cmd_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий статус смены."""
    if not is_authorized(update):
        return await deny(update)
    await send_long(update, format_shift_status())

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерирует полный отчёт по смене."""
    if not is_authorized(update):
        return await deny(update)
    shifts = get_today_shifts()
    if not shifts:
        await update.message.reply_text(
            "📭 Нет данных для отчёта. Сначала добавьте автомобили в смену."
        )
        return

    await update.message.reply_text("📝 Генерирую отчёт по смене...")

    # Порядок документов в отчёте
    REPORT_DOC_ORDER = [
        ("waybill",   "Путевой лист"),
        ("driver",    "Фото водителя"),
        ("dashboard", "Фото панели приборов"),
        ("video",     "Круговой видеоосмотр автомобиля"),
    ]

    lines = [f"📋 ОТЧЁТ ПО СМЕНЕ {datetime.now().strftime('%d.%m.%Y')}\n"]
    total_ok = 0

    for shift_id, vehicle_num, driver, customer in shifts:
        docs = get_vehicle_docs(shift_id)

        lines.append(f"{'─'*35}")
        header = f"🚛 {vehicle_num}"
        if customer:
            header += f"  |  {customer}"
        if driver:
            header += f"  |  {driver}"
        lines.append(header)
        lines.append(f"{'─'*35}")

        # 1. Отчёт — есть ли хоть один документ
        has_report = len(docs) > 0
        lines.append(f"1. Отчёт: {'✅ есть' if has_report else '❌ нет'}")

        # 2. Комплектность — все 4 типа документов получены
        missing_docs = [DOC_TYPES[dt] for dt in DOC_ORDER if dt not in docs]
        is_complete = len(missing_docs) == 0
        if is_complete:
            lines.append("2. Комплектность документов: ✅ полная")
        else:
            lines.append("2. Комплектность документов: ❌ неполная")
            for m in missing_docs:
                lines.append(f"   • не получен: {m}")

        # 3-6. По каждому типу документа
        vehicle_ok = is_complete
        for i, (doc_type, label) in enumerate(REPORT_DOC_ORDER, start=3):
            if doc_type not in docs:
                lines.append(f"{i}. {label}: ⏳ не получен")
                vehicle_ok = False
            else:
                result, analysis = docs[doc_type]
                if result == "ok":
                    lines.append(f"{i}. {label}: ✅ замечаний нет")
                elif result == "fail":
                    lines.append(f"{i}. {label}: ❌ есть замечания")
                    vehicle_ok = False
                    if analysis:
                        for remark in analysis.strip().split("\n"):
                            remark = remark.strip(" -•–—")
                            if remark:
                                lines.append(f"   • {remark}")
                else:
                    lines.append(f"{i}. {label}: ⏳ в обработке")
                    vehicle_ok = False

        lines.append(f"→ {'✅ К ВЫЕЗДУ ДОПУЩЕН' if vehicle_ok else '❌ ЕСТЬ ЗАМЕЧАНИЯ'}")
        lines.append("")
        if vehicle_ok:
            total_ok += 1

    lines.append(f"{'─'*35}")
    lines.append(f"ИТОГО: {total_ok}/{len(shifts)} а/м готовы к выезду")
    lines.append(f"Время отчёта: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    await send_long(update, "\n".join(lines))

async def cmd_newshift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает новую смену — удаляет данные за сегодня."""
    if not is_authorized(update):
        return await deny(update)
    clear_today_shifts()
    agent_state["active_vehicle"] = None
    agent_state["awaiting"] = None
    await update.message.reply_text(
        f"🔄 Новая смена начата ({datetime.now().strftime('%d.%m.%Y')}).\n"
        "Данные предыдущей смены за сегодня удалены.\n\n"
        "Отправьте фото справки диспетчера с подписью «справка» "
        "или добавьте автомобили через /set [номер]."
    )

async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает активный автомобиль: /set А123БВ116."""
    if not is_authorized(update):
        return await deny(update)
    args = context.args
    if not args:
        current = agent_state["active_vehicle"] or "не задан"
        await update.message.reply_text(
            f"📍 Текущий активный а/м: *{current}*\n\n"
            f"Использование: `/set А123БВ116`\n"
            f"После этого отправляйте документы без подписи — "
            f"они автоматически привяжутся к этому а/м.",
            parse_mode="Markdown"
        )
        return
    vehicle_num = " ".join(args).strip().upper()
    agent_state["active_vehicle"] = vehicle_num
    # Убеждаемся что запись в смене существует
    shift_id = get_or_create_shift(vehicle_num)
    await update.message.reply_text(
        f"✅ Активный а/м установлен: *{vehicle_num}*\n\n"
        f"Теперь отправляйте документы. Тип определяется автоматически по подписи:\n"
        f"• «видео» — видео кругового осмотра\n"
        f"• «путевой» — фото путевого листа\n"
        f"• «приборная» — фото приборной панели\n"
        f"• «водитель» — фото в спецодежде",
        parse_mode="Markdown"
    )

# ─── ОБРАБОТКА ДОКУМЕНТОВ АГЕНТА Q ───────────────────────────────────────────
async def process_schedule_image(update: Update, image_b64: str, mime: str = "image/jpeg"):
    """Разбирает фото справки/графика диспетчера и создаёт записи смены."""
    await update.message.reply_text("📋 Анализирую график / справку диспетчера...")
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
        {"type": "text", "text": SCHEDULE_PARSE_PROMPT},
    ]
    raw = await ask_claude(
        [{"role": "user", "content": content}],
        system="Ты — система разбора документов. Отвечай только валидным JSON без пояснений."
    )

    # Извлекаем JSON из ответа
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        await update.message.reply_text(
            f"⚠️ Не удалось разобрать документ как расписание.\n"
            f"Ответ модели: {raw[:500]}"
        )
        return

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        await update.message.reply_text("⚠️ Ошибка разбора JSON из ответа модели.")
        return

    if "error" in data or not data.get("vehicles"):
        err_msg = data.get("error", "Автомобили не найдены в документе.")
        await update.message.reply_text(
            f"⚠️ {err_msg}\n"
            f"Убедитесь, что это справка диспетчера или график работы."
        )
        return

    vehicles = data["vehicles"]
    added = []
    for v in vehicles:
        num = v.get("num", "").strip().upper()
        if not num:
            continue
        driver = v.get("driver", "")
        customer = v.get("customer", "")
        get_or_create_shift(num, driver, customer)
        added.append(f"🚛 {num}" + (f" ({customer})" if customer else "") + (f" — {driver}" if driver else ""))

    if not added:
        await update.message.reply_text("⚠️ Не удалось извлечь номера автомобилей из документа.")
        return

    reply = f"✅ В смену добавлено {len(added)} а/м:\n" + "\n".join(added)
    reply += "\n\nТеперь отправляйте документы самоконтроля по каждому автомобилю.\nИспользуйте /shift для просмотра статуса."
    await update.message.reply_text(reply)

async def process_vehicle_doc(update: Update, image_b64: str | None,
                               doc_type: str, vehicle_num: str,
                               video_frames: list | None = None,
                               mime: str = "image/jpeg"):
    """Анализирует документ самоконтроля для конкретного автомобиля."""
    shift_id = get_or_create_shift(vehicle_num)
    label = DOC_TYPES[doc_type]

    await update.message.reply_text(
        f"🔍 Анализирую: {label}\n🚛 Автомобиль: {vehicle_num}"
    )

    # Выбираем промпт
    prompts = {
        "video": VEHICLE_INSPECTION_PROMPT,
        "waybill": WAYBILL_PROMPT,
        "dashboard": DASHBOARD_PROMPT,
        "driver": DRIVER_PROMPT,
    }
    prompt_text = prompts[doc_type]
    # Для фото водителя — добавляем ФИО из смены
    if doc_type == "driver":
        with get_conn() as conn:
            row = conn.execute("SELECT driver FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        driver_name = row[0] if row and row[0] else ""
        if driver_name:
            prompt_text = f"ФИО водителя (из путевого листа): {driver_name}\n\n" + prompt_text

    # Формируем контент для Claude
    if doc_type == "video" and video_frames:
        content = []
        for i, frame_b64 in enumerate(video_frames):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}
            })
            content.append({"type": "text", "text": f"[Кадр {i+1} из {len(video_frames)}]"})
        content.append({"type": "text", "text": f"Автомобиль: {vehicle_num}\n\n{prompt_text}"})
    else:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
            {"type": "text", "text": f"Автомобиль: {vehicle_num}\n\n{prompt_text}"},
        ]

    analysis = await ask_claude(
        [{"role": "user", "content": content}],
        system=get_system_prompt()
    )

    result = extract_result(analysis)
    save_vehicle_doc(shift_id, doc_type, result, analysis)

    result_icon = {"ok": "✅", "fail": "❌", "pending": "⏳"}
    header = f"{result_icon.get(result, '⏳')} {label} — {vehicle_num}\n\n"
    await send_long(update, header + analysis)

    # Проверяем, все ли документы получены для этого авто
    docs = get_vehicle_docs(shift_id)
    missing = [DOC_TYPES[dt] for dt in DOC_ORDER if dt not in docs]
    if missing:
        await update.message.reply_text(
            f"📋 Для {vehicle_num} ещё нужны:\n" +
            "\n".join(f"• {m}" for m in missing)
        )
    else:
        all_ok = all(docs[dt][0] == "ok" for dt in DOC_ORDER)
        if all_ok:
            await update.message.reply_text(
                f"🎉 Автомобиль {vehicle_num} — все документы получены и в норме!\n"
                f"✅ Допущен к выезду."
            )
        else:
            fails = [DOC_TYPES[dt] for dt in DOC_ORDER if docs[dt][0] == "fail"]
            await update.message.reply_text(
                f"⚠️ Автомобиль {vehicle_num} — все документы получены.\n"
                f"❌ Есть замечания по: {', '.join(fails)}"
            )

def _get_ffmpeg_bin() -> tuple[str, str]:
    """Returns paths to ffmpeg and ffprobe (imageio-ffmpeg first, then PATH)."""
    import shutil
    ffmpeg_exe = ffprobe_exe = None
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        ffprobe_exe = shutil.which("ffprobe") or ffmpeg_exe.replace("ffmpeg", "ffprobe")
        logger.info(f"imageio-ffmpeg: {ffmpeg_exe}")
    except Exception:
        pass
    if not ffmpeg_exe:
        ffmpeg_exe = shutil.which("ffmpeg") or "ffmpeg"
        ffprobe_exe = shutil.which("ffprobe") or "ffprobe"
        logger.info(f"system ffmpeg: {ffmpeg_exe}")
    return ffmpeg_exe, ffprobe_exe


# ─── ИЗВЛЕЧЕНИЕ КАДРОВ ВИДЕО ──────────────────────────────────────────────────
async def extract_video_frames(video_path: str, max_frames: int = 12) -> list:
    import asyncio as _asyncio

    ffmpeg_bin, ffprobe_bin = _get_ffmpeg_bin()

    async def run(*cmd):
        proc = await _asyncio.create_subprocess_exec(
            *cmd,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE
        )
        stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=90)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    frames = []
    tmp_dir_obj = tempfile.TemporaryDirectory()
    tmp_dir = tmp_dir_obj.name
    try:
        out_pattern = os.path.join(tmp_dir, "frame_%03d.jpg")

        # Определяем длительность
        duration = 10.0
        try:
            rc, out, err = await run(
                ffprobe_bin, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path
            )
            duration = float(out.strip() or "10")
        except Exception as e:
            logger.warning(f"ffprobe failed: {e}")

        interval = max(1.0, duration / max_frames)

        # Извлекаем кадры
        try:
            rc, out, err = await run(
                ffmpeg_bin, "-y", "-i", video_path,
                "-vf", f"fps=1/{interval:.1f}",
                "-vframes", str(max_frames),
                "-q:v", "3", out_pattern
            )
            if rc != 0:
                logger.error(f"ffmpeg exit {rc}. stderr: {err[-500:]}")
        except FileNotFoundError:
            logger.error("ffmpeg not found — не установлен в Railway?")
            return []
        except Exception as e:
            logger.error(f"ffmpeg exception: {e}")
            return []

        for f in sorted(Path(tmp_dir).glob("frame_*.jpg"))[:max_frames]:
            frames.append(base64.b64encode(f.read_bytes()).decode())

    finally:
        tmp_dir_obj.cleanup()

    return frames

# ─── ОБРАБОТЧИКИ СООБЩЕНИЙ ────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    user_text = update.message.text

    # ── Перехват реестра Татавтоматизации ─────────────────────────────────────
    if detect_registry(user_text):
        today = _msk_today()
        vehicles = parse_registry(user_text)
        if vehicles:
            save_registry(user_text, vehicles, today)
            # Автоматически добавить рабочие авто в смену
            for num, info in vehicles.items():
                if info["status"] == "working":
                    get_or_create_shift(num)
            working = [
                f"🚗 *{num}*: {info['note'][:70]}"
                for num, info in vehicles.items() if info["status"] == "working"
            ]
            off_list = [
                f"🅿️ {num}: выходной"
                for num, info in vehicles.items() if info["status"] == "off"
            ]
            lines = [f"✅ *Реестр принят* ({today.strftime('%d.%m.%Y')})\n"]
            if working:
                lines.append(f"*Работают ({len(working)}):*")
                lines.extend(working)
            if off_list:
                lines.append(f"\n*Выходные:*")
                lines.extend(off_list)
            unknown = TATAVTO_VEHICLES - set(vehicles.keys())
            if unknown:
                lines.append(f"\n⚠️ Не найдены в реестре: {', '.join(sorted(unknown))}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return
    # ── Обычный чат с Claude ──────────────────────────────────────────────────
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

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    buf.seek(0)
    image_b64 = base64.b64encode(buf.read()).decode()
    caption = update.message.caption or ""

    # Определяем контекст: справка диспетчера?
    if detect_is_schedule(caption):
        await process_schedule_image(update, image_b64)
        return

    # Определяем тип документа
    doc_type = detect_doc_type(caption)
    # Определяем номер автомобиля из подписи или из agent_state
    vehicle_num = detect_vehicle_num(caption) or agent_state.get("active_vehicle")

    if doc_type and vehicle_num:
        await process_vehicle_doc(update, image_b64, doc_type, vehicle_num)
        return

    if doc_type and not vehicle_num:
        await update.message.reply_text(
            f"🚛 Для какого автомобиля этот документ?\n"
            f"Укажите номер в подписи или используйте /set [номер]."
        )
        return

    # Нет контекста — стандартный анализ фото
    prompt_text = caption or "Что на фото? Опиши подробно."
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
        {"type": "text", "text": prompt_text},
    ]
    messages = build_messages("user", content)
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
    mime_type = doc.mime_type or ""
    caption = update.message.caption or ""

    IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"}
    is_image = (mime_type in IMAGE_MIMES or
                file_name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")))

    if is_image:
        detected_mime = mime_type if mime_type in IMAGE_MIMES else "image/jpeg"
        image_b64 = base64.b64encode(raw).decode()

        # Проверяем контекст агента Q
        if detect_is_schedule(caption):
            await process_schedule_image(update, image_b64, detected_mime)
            return

        doc_type = detect_doc_type(caption)
        vehicle_num = detect_vehicle_num(caption) or agent_state.get("active_vehicle")

        if doc_type and vehicle_num:
            await process_vehicle_doc(update, image_b64, doc_type, vehicle_num, mime=detected_mime)
            return

        if doc_type and not vehicle_num:
            await update.message.reply_text(
                "🚛 Для какого автомобиля этот документ?\n"
                "Укажите номер в подписи или используйте /set [номер]."
            )
            return

        # Стандартный анализ изображения
        prompt_text = caption or "Что на изображении? Опиши подробно."
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": detected_mime, "data": image_b64}},
            {"type": "text", "text": prompt_text},
        ]
        messages = build_messages("user", content)
        save_history("user", f"[Фото-файл: {file_name}] {caption}")
        response = await ask_claude(messages)
        save_history("assistant", response)
        await send_long(update, response)
    else:
        # Текстовый документ
        try:
            file_text = raw.decode("utf-8")
            file_content = f"Файл: {file_name}\n\nСодержимое:\n{file_text[:8000]}"
            if len(file_text) > 8000:
                file_content += "\n\n[...файл обрезан]"
        except UnicodeDecodeError:
            file_content = f"Файл: {file_name} (бинарный, размер: {len(raw)} байт)"

        prompt_text = caption or "Проанализируй этот документ."
        user_text = f"{prompt_text}\n\n{file_content}"
        messages = build_messages("user", user_text)
        save_history("user", f"[Документ: {file_name}] {caption}")
        response = await ask_claude(messages)
        save_history("assistant", response)
        await send_long(update, response)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)

    caption = update.message.caption or ""
    vehicle_num = detect_vehicle_num(caption) or agent_state.get("active_vehicle")

    await update.message.reply_text("🎬 Получил видео. Извлекаю кадры...")
    await update.message.chat.send_action("typing")

    video = update.message.video or update.message.document
    if video is None:
        await update.message.reply_text("❌ Не удалось получить видеофайл.")
        return

    tg_file = await context.bot.get_file(video.file_id)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await tg_file.download_to_drive(tmp_path)
        frames = await extract_video_frames(tmp_path, max_frames=12)

        if not frames:
            await update.message.reply_text(
                "❌ Не удалось извлечь кадры из видео.\n"
                "Убедитесь что файл в формате MP4, MOV или AVI."
            )
            return

        await update.message.reply_text(f"✅ Извлечено кадров: {len(frames)}. Анализирую...")

        # Контекст Агента Q — видео кругового осмотра
        if vehicle_num:
            await process_vehicle_doc(
                update, None, "video", vehicle_num, video_frames=frames
            )
            save_history("user", f"[Видео осмотра: {vehicle_num}, {len(frames)} кадров]")
            return

        # Видео без привязки к авто — стандартный анализ
        if not vehicle_num:
            await update.message.reply_text(
                "ℹ️ Номер автомобиля не указан в подписи и не задан через /set.\n"
                "Выполняю общий анализ видео..."
            )

        user_prompt = caption if caption else VEHICLE_INSPECTION_PROMPT
        content = []
        for i, frame_b64 in enumerate(frames):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}
            })
            content.append({"type": "text", "text": f"[Кадр {i+1} из {len(frames)}]"})
        content.append({"type": "text", "text": user_prompt})

        messages = build_messages("user", content)
        save_history("user", f"[Видео, {len(frames)} кадров] {caption}")
        response = await ask_claude(messages)
        save_history("assistant", response)
        await send_long(update, response)

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    await update.message.reply_text(
        "🎤 Голосовые сообщения пока не поддерживаются.\n"
        "Отправьте текстом или документом."
    )

# ─── ЗАПУСК ───────────────────────────────────────────────────────────────────
async def _post_init(app: Application) -> None:
    """Start the APScheduler inside the running event loop."""
    setup_scheduler(app)


def main():
    init_db()
    logger.info(f"Запуск Агента Q | модель: {MODEL} | owner_id: {OWNER_ID}")

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # Основные команды
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("memory",     cmd_memory))
    app.add_handler(CommandHandler("checkpoint", cmd_checkpoint))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("savelife",   cmd_checkpoint))

    # Команды Агента Q
    app.add_handler(CommandHandler("shift",      cmd_shift))
    app.add_handler(CommandHandler("report",     cmd_report))
    app.add_handler(CommandHandler("newshift",   cmd_newshift))
    app.add_handler(CommandHandler("set",        cmd_set))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO,          handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO,          handle_video))
    app.add_handler(MessageHandler(filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL,   handle_document))
    app.add_handler(MessageHandler(filters.VOICE,          handle_voice))

    logger.info("Агент Q запущен и ожидает сообщений...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
