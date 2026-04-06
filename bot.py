#!/usr/bin/env python3
"""
ÐÐµÑÑÐ¾Ð½Ð°Ð»ÑÐ½ÑÐ¹ Claude AI Telegram ÐÐ¾Ñ
Telegram â Claude API + SQLite Ð¿Ð°Ð¼ÑÑÑ + ÑÐ°Ð¹Ð»Ñ/ÑÐ¾ÑÐ¾ + ÐÐ³ÐµÐ½Ñ Q (ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ð¢Ð¡)
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

# âââ ÐÐÐÐ¤ÐÐÐ£Ð ÐÐ¦ÐÐ¯ âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

# âââ Ð¡ÐÐ¡Ð¢ÐÐ¯ÐÐÐ ÐÐÐÐÐ¢Ð Q (Ð² Ð¿Ð°Ð¼ÑÑÐ¸) âââââââââââââââââââââââââââââââââââââââââââ
agent_state = {
    "active_vehicle": None,   # ÑÐµÐºÑÑÐ¸Ð¹ Ð°/Ð¼ Ð´Ð»Ñ ÑÐ»ÐµÐ´ÑÑÑÐ¸Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð²
    "awaiting": None,         # Ð¾Ð¶Ð¸Ð´Ð°ÐµÐ¼ÑÐ¹ ÑÐ¸Ð¿ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ° (ÐµÑÐ»Ð¸ Ð·Ð°Ð´Ð°Ð½ Ð²ÑÑÑÐ½ÑÑ)
}

# âââ ÐÐÐÐÐ¢ÐÐ ÐÐÐ: ÐÐÐÐ¡Ð¢ÐÐÐ¢Ð« ââââââââââââââââââââââââââââââââââââââââââââââââââââ
MSK = pytz.timezone("Europe/Moscow")

# Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ñ: 9 Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¹ â ÑÐµÐµÑÑÑ Ð¿ÑÐ¸ÑÑÐ»Ð°ÑÑ ÐºÐ°Ð¶Ð´Ð¾Ðµ ÑÑÑÐ¾
TATAVTO_VEHICLES = {"178", "539", "557", "029", "516", "072", "959", "629", "642"}

# ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ð¸ Ñ ÑÐ¸ÐºÑÐ¸ÑÐ¾Ð²Ð°Ð½Ð½ÑÐ¼ Ð³ÑÐ°ÑÐ¸ÐºÐ¾Ð¼
UPTJ_VEHICLES   = {"898", "321"}   # Ð£ÐÐ¢Ð: 5 ÑÐ°Ð± / 2 Ð²ÑÑ + Ð¿ÑÐ°Ð·Ð´Ð½Ð¸ÐºÐ¸ Ð Ð¤ Ð¸ Ð Ð¢
TATBUR_VEHICLES = {"626"}           # Ð¢Ð°ÑÐ±ÑÑÐÐµÑÑÑ: Ð±ÐµÐ· Ð²ÑÑÐ¾Ð´Ð½ÑÑ
KRS_VEHICLES    = {"641"}           # ÐÐ Ð¡ Ð¡ÐµÑÐ²Ð¸Ñ: ÐºÑÐ¾Ð¼Ðµ Ð¿ÑÑÐ½Ð¸ÑÑ

# ÐÐ¿Ð¾ÑÐ½Ð°Ñ Ð´Ð°ÑÐ° Ð´Ð»Ñ ÑÐ¸ÐºÐ»Ð° Ð£ÐÐ¢Ð (Ð¿ÐµÑÐ²ÑÐ¹ Ð´ÐµÐ½Ñ ÑÐ°Ð±Ð¾ÑÐµÐ³Ð¾ Ð±Ð»Ð¾ÐºÐ° 5/2)
# Ð¦Ð¸ÐºÐ»: 5 ÑÐ°Ð±Ð¾ÑÐ¸Ñ + 2 Ð²ÑÑÐ¾Ð´Ð½ÑÑ = 7 Ð´Ð½ÐµÐ¹, Ð¿Ð¾Ð²ÑÐ¾ÑÑÐµÑÑÑ Ð½ÐµÐ·Ð°Ð²Ð¸ÑÐ¸Ð¼Ð¾ Ð¾Ñ Ð´Ð½Ñ Ð½ÐµÐ´ÐµÐ»Ð¸
_UPTJ_REF = date(2025, 4, 7)   # Ð¿Ð¾Ð½ÐµÐ´ÐµÐ»ÑÐ½Ð¸Ðº â Ð¿ÑÐ¸ Ð½ÐµÐ¾Ð±ÑÐ¾Ð´Ð¸Ð¼Ð¾ÑÑÐ¸ Ð½Ð°ÑÑÑÐ¾Ð¸ÑÑ ÑÐµÑÐµÐ· /setuptjref

# âââ ÐÑÐ°Ð·Ð´Ð½Ð¸ÐºÐ¸ Ð Ð¤ + Ð¢Ð°ÑÐ°ÑÑÑÐ°Ð½ âââââââââââââââââââââââââââââââââââââââââââââââââ
_RF_HOLIDAYS: set[str] = {
    # 2025
    "2025-01-01","2025-01-02","2025-01-03","2025-01-04","2025-01-05",
    "2025-01-06","2025-01-07","2025-01-08",
    "2025-02-24",                      # ÐÐµÑÐµÐ½Ð¾Ñ: 23.02 Ð²Ñ â 24.02 Ð¿Ð½
    "2025-03-10",                      # ÐÐµÑÐµÐ½Ð¾Ñ: 8.03 ÑÐ± â 10.03 Ð¿Ð½
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
    """898, 321: ÑÐ¸ÐºÐ» 5/2, Ð¿ÑÐ°Ð·Ð´Ð½Ð¸ÐºÐ¸ Ð Ð¤+Ð Ð¢ â Ð²ÑÑÐ¾Ð´Ð½Ð¾Ð¹."""
    if _is_holiday(d):
        return False
    pos = (d - _UPTJ_REF).days % 7
    return 0 <= pos <= 4

def is_tatbur_working(_d: date) -> bool:
    """626: Ð±ÐµÐ· Ð²ÑÑÐ¾Ð´Ð½ÑÑ."""
    return True

def is_krs_working(d: date) -> bool:
    """641: Ð¿ÑÑÐ½Ð¸ÑÐ° â Ð²ÑÑÐ¾Ð´Ð½Ð¾Ð¹."""
    return d.weekday() != 4  # 0=Ð¿Ð½ â¦ 4=Ð¿Ñ

# âââ ÐÐÐÐ ÐÐÐÐÐ«Ð¥ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # ÐÑÐ½Ð¾Ð²Ð½Ð°Ñ Ð¿Ð°Ð¼ÑÑÑ
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
    # Ð¢Ð°Ð±Ð»Ð¸ÑÑ ÐÐ³ÐµÐ½ÑÐ° Q
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
    # Ð ÐµÐµÑÑÑ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¹ Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ð¸
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_registry (
            date        TEXT PRIMARY KEY,
            raw_text    TEXT NOT NULL,
            vehicles_json TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
    """)
    # ÐÑÑÐ½Ð°Ð» Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÐµÐ½Ð½ÑÑ Ð°Ð»ÐµÑÑÐ¾Ð² (Ð½Ðµ ÑÐ¿Ð°Ð¼Ð¸ÑÑ Ð¿Ð¾Ð²ÑÐ¾ÑÐ½Ð¾)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts_sent (
            key         TEXT PRIMARY KEY,
            sent_at     TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"ÐÐ Ð¸Ð½Ð¸ÑÐ¸Ð°Ð»Ð¸Ð·Ð¸ÑÐ¾Ð²Ð°Ð½Ð°: {DB_PATH}")

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

# âââ ÐÐÐÐÐ¢ÐÐ ÐÐÐ: Ð ÐÐÐ¡Ð¢Ð  Ð ÐÐÐÐ Ð¢Ð« âââââââââââââââââââââââââââââââââââââââââââââ
def _msk_now() -> datetime:
    return datetime.now(MSK)

def _msk_today() -> date:
    return _msk_now().date()

def detect_registry(text: str) -> bool:
    """ÐÐ¾Ð·Ð²ÑÐ°ÑÐ°ÐµÑ True, ÐµÑÐ»Ð¸ ÑÐµÐºÑÑ Ð¿Ð¾ÑÐ¾Ð¶ Ð½Ð° ÑÐµÐµÑÑÑ Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ð¸."""
    found = sum(1 for num in TATAVTO_VEHICLES if num in text)
    return found >= 3   # Ð¼Ð¸Ð½Ð¸Ð¼ÑÐ¼ 3 Ð³Ð¾Ñ.Ð½Ð¾Ð¼ÐµÑÐ° Ð¸Ð· ÑÐ¿Ð¸ÑÐºÐ°

def parse_registry(text: str) -> dict:
    """
    ÐÐ°ÑÑÐ¸Ñ ÑÐµÐµÑÑÑ Ð²Ð¸Ð´Ð°:
        03.04.
        178 Ð² 9:00 ÐµÐ´ÐµÑ Ð½Ð° Ð¼Ð¾Ð½ÑÐ°Ð¶
        539 Ð²ÑÑÐ¾Ð´Ð½Ð¾Ð¹
    ÐÐ¾Ð·Ð²ÑÐ°ÑÐ°ÐµÑ: {"178": {"status": "working", "start_time": "09:00", "note": "..."}, ...}
    """
    vehicles: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for num in TATAVTO_VEHICLES:
            # ÐÑÐµÐ¼ Ð½Ð¾Ð¼ÐµÑ Ð² Ð½Ð°ÑÐ°Ð»Ðµ ÑÑÑÐ¾ÐºÐ¸ (Ñ Ð¿ÑÐ¾Ð±ÐµÐ»Ð°Ð¼Ð¸/ÑÐ¾ÑÐºÐ°Ð¼Ð¸ Ð´Ð¾)
            m = re.search(r'(?<!\d)' + re.escape(num) + r'(?!\d)', line)
            if not m:
                continue
            rest = line[m.end():].strip()
            is_off = bool(re.search(r'Ð²ÑÑÐ¾Ð´Ð½', rest, re.IGNORECASE))
            # ÐÑÐµÐ¼Ñ ÑÑÐ°ÑÑÐ°: Â«Ð² 9:00Â», Â«Ñ 01:30Â», Â«Ñ 9.00Â»
            t_match = re.search(r'(?:Ð²|Ñ)\s*(\d{1,2})[.:](\d{2})', rest)
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

# ââ ÐÐ»ÐµÑÑÑ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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
    """ÐÑÐ¿ÑÐ°Ð²Ð¸ÑÑ Ð²Ð»Ð°Ð´ÐµÐ»ÑÑÑ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ."""
    await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode="Markdown")

# ââ ÐÐ°Ð´Ð°ÑÐ¸ Ð¿Ð»Ð°Ð½Ð¸ÑÐ¾Ð²ÑÐ¸ÐºÐ° âââââââââââââââââââââââââââââââââââââââââââââââââââââââ
async def job_check_registry(app):
    """09:10 ÐÐ¡Ð â Ð¿ÑÐ¾Ð²ÐµÑÐ¸ÑÑ, Ð¿Ð¾Ð»ÑÑÐµÐ½ Ð»Ð¸ ÑÐµÐµÑÑÑ Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ð¸."""
    today = _msk_today()
    if get_registry(today) is None:
        if not alert_sent(today, "registry_missing"):
            await _push(app,
                f"â ï¸ *Ð ÐµÐµÑÑÑ Ð½Ðµ Ð¿Ð¾Ð»ÑÑÐµÐ½!*\n"
                f"Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ñ Ð½Ðµ Ð¿ÑÐ¸ÑÐ»Ð°Ð»Ð° ÑÐµÐµÑÑÑ Ð½Ð° "
                f"{today.strftime('%d.%m.%Y')} Ðº 09:10 ÐÐ¡Ð."
            )
            mark_alert(today, "registry_missing")

async def job_check_inspections(app):
    """ÐÐ°Ð¶Ð´ÑÐµ 15 Ð¼Ð¸Ð½ â Ð°Ð»ÐµÑÑ ÐµÑÐ»Ð¸ Ð¿ÑÐ¾ÑÑÐ» ÑÐ°Ñ Ñ Ð²ÑÐµÐ·Ð´Ð°, Ð° ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ð½ÐµÑ."""
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
                # ÐÑÑÑ Ð»Ð¸ ÑÐ¼ÐµÐ½Ð° Ð¸ ÑÐ¾ÑÑ Ð¾Ð´Ð¸Ð½ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ?
                shift_id = _shift_id_for(num, today)
                if shift_id and not _has_inspection(shift_id):
                    code = f"insp_{num}"
                    if not alert_sent(today, code):
                        await _push(app,
                            f"ð¨ *ÐÐµÑ ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ!*\n"
                            f"ð Ð/Ð¼: *{num}* (Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ñ)\n"
                            f"â° ÐÑÐµÑÐ°Ð» Ð² {info['start_time']} ÐÐ¡Ð â Ð¿ÑÐ¾ÑÑÐ» ÑÐ¶Ðµ ÑÐ°Ñ.\n"
                            f"ð ÐÑÑÑÑ Ð¾ ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ðµ Ð½Ðµ Ð¿Ð¾ÑÑÑÐ¿Ð°Ð»."
                        )
                        mark_alert(today, code)
            except Exception as e:
                logger.error(f"job_check_inspections [{num}]: {e}")

async def job_check_fixed(app):
    """09:00 ÐÐ¡Ð â Ð¿ÑÐ¾Ð²ÐµÑÐ¸ÑÑ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ð¸ Ñ ÑÐ¸ÐºÑÐ¸ÑÐ¾Ð²Ð°Ð½Ð½ÑÐ¼ Ð³ÑÐ°ÑÐ¸ÐºÐ¾Ð¼."""
    today = _msk_today()
    checks = [
        (UPTJ_VEHICLES,   is_uptj_working,   "Ð£ÐÐ¢Ð"),
        (TATBUR_VEHICLES, is_tatbur_working,  "Ð¢Ð°ÑÐ±ÑÑÐÐµÑÑÑ"),
        (KRS_VEHICLES,    is_krs_working,     "ÐÐ Ð¡ Ð¡ÐµÑÐ²Ð¸Ñ"),
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
                        f"â ï¸ *ÐÐµÑ ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ðº 09:00!*\n"
                        f"ð¢ ÐÑÐ³Ð°Ð½Ð¸Ð·Ð°ÑÐ¸Ñ: {org}\n"
                        f"ð Ð/Ð¼: *{num}*\n"
                        f"ð Ð¡ÐµÐ³Ð¾Ð´Ð½Ñ ÑÐ°Ð±Ð¾ÑÐ¸Ð¹ Ð´ÐµÐ½Ñ â ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ð½Ðµ Ð¿Ð¾ÑÑÑÐ¿Ð°Ð»."
                    )
                    mark_alert(today, code)

def _shift_id_for(vehicle_num: str, for_date: date) -> int | None:
    """ID ÑÐ¼ÐµÐ½Ñ Ð´Ð»Ñ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ Ð½Ð° Ð·Ð°Ð´Ð°Ð½Ð½ÑÑ Ð´Ð°ÑÑ (Ð¸Ð»Ð¸ None)."""
    with get_conn() as c:
        row = c.execute(
            "SELECT id FROM shifts WHERE vehicle_num = ? AND date = ? AND status = 'active'",
            (vehicle_num, for_date.isoformat())
        ).fetchone()
    return row[0] if row else None

def _has_inspection(shift_id: int) -> bool:
    """ÐÑÑÑ Ð»Ð¸ ÑÐ¾ÑÑ Ð¾Ð´Ð¸Ð½ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ð² ÑÐ¼ÐµÐ½Ðµ."""
    with get_conn() as c:
        cnt = c.execute(
            "SELECT COUNT(*) FROM vehicle_docs WHERE shift_id = ?", (shift_id,)
        ).fetchone()[0]
    return cnt > 0

def setup_scheduler(app) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=MSK)
    # 09:10 ÐÐ¡Ð â ÑÐµÐµÑÑÑ Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ð¸
    scheduler.add_job(job_check_registry,  CronTrigger(hour=9,  minute=10, timezone=MSK), args=[app])
    # 09:00 ÐÐ¡Ð â ÑÐ¸ÐºÑÐ¸ÑÐ¾Ð²Ð°Ð½Ð½ÑÐµ Ð³ÑÐ°ÑÐ¸ÐºÐ¸
    scheduler.add_job(job_check_fixed,     CronTrigger(hour=9,  minute=0,  timezone=MSK), args=[app])
    # ÐÐ°Ð¶Ð´ÑÐµ 15 Ð¼Ð¸Ð½ÑÑ â Ð¿ÑÐ¾Ð²ÐµÑÐºÐ° ÑÐ°Ñ-Ð¿Ð¾ÑÐ»Ðµ-Ð²ÑÐµÐ·Ð´Ð°
    scheduler.add_job(job_check_inspections, CronTrigger(minute="*/15",    timezone=MSK), args=[app])
    scheduler.start()
    logger.info("ÐÐ»Ð°Ð½Ð¸ÑÐ¾Ð²ÑÐ¸Ðº Ð¼Ð¾Ð½Ð¸ÑÐ¾ÑÐ¸Ð½Ð³Ð° Ð·Ð°Ð¿ÑÑÐµÐ½")
    return scheduler

# âââ ÐÐÐÐÐ¢ Q: Ð ÐÐÐÐ¢Ð Ð¡Ð Ð¡ÐÐÐÐÐ ââââââââââââââââââââââââââââââââââââââââââââââââ
DOC_TYPES = {
    "video":     "ð¬ ÐÐ¸Ð´ÐµÐ¾ ÐºÑÑÐ³Ð¾Ð²Ð¾Ð³Ð¾ Ð¾ÑÐ¼Ð¾ÑÑÐ°",
    "waybill":   "ð ÐÑÑÐµÐ²Ð¾Ð¹ Ð»Ð¸ÑÑ",
    "dashboard": "ð ÐÑÐ¸Ð±Ð¾ÑÐ½Ð°Ñ Ð¿Ð°Ð½ÐµÐ»Ñ",
    "driver":    "ð· ÐÐ¾Ð´Ð¸ÑÐµÐ»Ñ Ð² ÑÐ¿ÐµÑÐ¾Ð´ÐµÐ¶Ð´Ðµ",
}
DOC_ORDER = ["video", "waybill", "dashboard", "driver"]

def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def get_today_shifts():
    """ÐÐ¾Ð·Ð²ÑÐ°ÑÐ°ÐµÑ ÑÐ¿Ð¸ÑÐ¾Ðº Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¹ Ð² ÑÐµÐ³Ð¾Ð´Ð½ÑÑÐ½ÐµÐ¹ ÑÐ¼ÐµÐ½Ðµ."""
    with get_conn() as c:
        return c.execute(
            "SELECT id, vehicle_num, driver, customer FROM shifts "
            "WHERE date = ? AND status = 'active' ORDER BY id",
            (today(),)
        ).fetchall()

def get_or_create_shift(vehicle_num: str, driver: str = "", customer: str = "") -> int:
    """ÐÐ°ÑÐ¾Ð´Ð¸Ñ Ð¸Ð»Ð¸ ÑÐ¾Ð·Ð´Ð°ÑÑ Ð·Ð°Ð¿Ð¸ÑÑ ÑÐ¼ÐµÐ½Ñ Ð´Ð»Ñ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ Ð½Ð° ÑÐµÐ³Ð¾Ð´Ð½Ñ."""
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
    """ÐÐ¾Ð·Ð²ÑÐ°ÑÐ°ÐµÑ ÑÐ»Ð¾Ð²Ð°ÑÑ {doc_type: (result, analysis)} Ð´Ð»Ñ ÑÐ¼ÐµÐ½Ñ."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT doc_type, result, analysis FROM vehicle_docs WHERE shift_id = ?",
            (shift_id,)
        ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}

def save_vehicle_doc(shift_id: int, doc_type: str, result: str, analysis: str):
    """Ð¡Ð¾ÑÑÐ°Ð½ÑÐµÑ Ð¸Ð»Ð¸ Ð¾Ð±Ð½Ð¾Ð²Ð»ÑÐµÑ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ."""
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
    """Ð£Ð´Ð°Ð»ÑÐµÑ Ð²ÑÐµ Ð·Ð°Ð¿Ð¸ÑÐ¸ ÑÐ¼ÐµÐ½Ñ Ð·Ð° ÑÐµÐ³Ð¾Ð´Ð½Ñ (Ð½Ð°ÑÐ°Ð»Ð¾ Ð½Ð¾Ð²Ð¾Ð¹ ÑÐ¼ÐµÐ½Ñ)."""
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
    """Ð¤Ð¾ÑÐ¼Ð°ÑÐ¸ÑÑÐµÑ ÑÐµÐºÑÑÐ¸Ð¹ ÑÑÐ°ÑÑÑ ÑÐ¼ÐµÐ½Ñ."""
    shifts = get_today_shifts()
    if not shifts:
        return (
            f"ð Ð¡Ð¼ÐµÐ½Ð° {datetime.now().strftime('%d.%m.%Y')}\n\n"
            "ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ð¸ Ð½Ðµ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ñ.\n"
            "ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐ¾ÑÐ¾ Ð³ÑÐ°ÑÐ¸ÐºÐ°/ÑÐ¿ÑÐ°Ð²ÐºÐ¸ Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ° Ð¸Ð»Ð¸ Ð¸ÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ /set [Ð½Ð¾Ð¼ÐµÑ]."
        )

    result_icon = {"ok": "â", "fail": "â", "pending": "â³"}
    lines = [f"ð *Ð¡Ð¼ÐµÐ½Ð° {datetime.now().strftime('%d.%m.%Y')}*\n"]
    ready_count = 0

    for shift_id, vehicle_num, driver, customer in shifts:
        docs = get_vehicle_docs(shift_id)
        header = f"ð *{vehicle_num}*"
        if customer:
            header += f" â {customer}"
        if driver:
            header += f"\n   ð¤ {driver}"
        lines.append(header)

        all_ok = True
        for dt in DOC_ORDER:
            label = DOC_TYPES[dt]
            if dt in docs:
                icon = result_icon.get(docs[dt][0], "â³")
                if docs[dt][0] != "ok":
                    all_ok = False
            else:
                icon = "â³"
                all_ok = False
            lines.append(f"   {icon} {label}")

        if all_ok:
            ready_count += 1
        lines.append("")

    lines.append(f"â ÐÐ¾ÑÐ¾Ð²Ð¾: {ready_count}/{len(shifts)} Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¹")
    active = agent_state["active_vehicle"]
    if active:
        lines.append(f"\nð ÐÐºÑÐ¸Ð²Ð½ÑÐ¹ Ð°/Ð¼: *{active}*")
    return "\n".join(lines)

# âââ ÐÐÐ¢ÐÐÐ¢ÐÐ ÐÐÐÐÐÐ Ð¢ÐÐÐ ÐÐÐÐ£ÐÐÐÐ¢Ð Ð ÐÐÐÐÐ Ð ÐÐÐ¢Ð ââââââââââââââââââââââââââââââ
SCHEDULE_KEYWORDS = ["ÑÐ¿ÑÐ°Ð²ÐºÐ°", "Ð³ÑÐ°ÑÐ¸Ðº", "ÑÐ°ÑÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ", "ÑÐ¼ÐµÐ½Ð°", "Ð½Ð°ÑÑÐ´Ð¾Ð²", "Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑ"]
DOC_KEYWORDS = {
    "video":     ["Ð²Ð¸Ð´ÐµÐ¾", "Ð¾ÑÐ¼Ð¾ÑÑ", "ÐºÑÑÐ³Ð¾Ð²Ð¾Ð¹", "Ð¾Ð±ÑÐ¾Ð´"],
    "waybill":   ["Ð¿ÑÑÐµÐ²Ð¾Ð¹", "Ð¿ÑÑÑÐ²ÑÐ¹", "Ð¿ÑÑÐµÐ²ÐºÐ°", "Ð¿ÑÑÑÐ²ÐºÐ°", "Ð¼Ð°ÑÑÑÑÑ"],
    "dashboard": ["Ð¿ÑÐ¸Ð±Ð¾ÑÐ½", "Ð¿Ð°Ð½ÐµÐ»Ñ", "ÑÐ¸ÑÐ¾Ðº", "ÑÐ¿Ð¸Ð´Ð¾Ð¼ÐµÑÑ", "Ð¾Ð´Ð¾Ð¼ÐµÑÑ", "dashboard"],
    "driver":    ["Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ", "ÑÐ¿ÐµÑÐ¾Ð´ÐµÐ¶Ð´", "ÑÐ¾ÑÐ¼", "ÑÐ°Ð±Ð¾ÑÐ°Ñ", "ÑÐµÐ²ÑÐ¾Ð½", "Ð²Ð°Ð¸Ñ"],
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

# ÐÐ°ÑÑÐµÑÐ½ ÑÐ¾ÑÑÐ¸Ð¹ÑÐºÐ¾Ð³Ð¾ Ð½Ð¾Ð¼ÐµÑÐ½Ð¾Ð³Ð¾ Ð·Ð½Ð°ÐºÐ° (ÑÐ¿ÑÐ¾ÑÑÐ½Ð½ÑÐ¹)
RU_PLATE_RE = re.compile(
    r'\b[ÐÐÐÐÐÐÐÐ Ð¡Ð¢Ð£Ð¥ABEKMHOPCTYX]\d{3}[ÐÐÐÐÐÐÐÐ Ð¡Ð¢Ð£Ð¥ABEKMHOPCTYX]{2}\s*\d{2,3}\b',
    re.IGNORECASE | re.UNICODE
)
# ÐÐ¾ÑÐ¾ÑÐºÐ¸Ð¹ Ð²Ð°ÑÐ¸Ð°Ð½Ñ Ð±ÐµÐ· ÑÐµÐ³Ð¸Ð¾Ð½Ð°
RU_PLATE_SHORT_RE = re.compile(
    r'\b[ÐÐÐÐÐÐÐÐ Ð¡Ð¢Ð£Ð¥ABEKMHOPCTYX]\d{3}[ÐÐÐÐÐÐÐÐ Ð¡Ð¢Ð£Ð¥ABEKMHOPCTYX]{2}\b',
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
    """ÐÐ¿ÑÐµÐ´ÐµÐ»ÑÐµÑ Ð¸ÑÐ¾Ð³ Ð°Ð½Ð°Ð»Ð¸Ð·Ð°: ok / fail / pending."""
    a = analysis.upper()
    ok_markers = ["â", "ÐÐÐÐ£Ð©ÐÐ", "Ð ÐÐÐ ÐÐ", "Ð¡ÐÐÐ¢ÐÐÐ¢Ð¡Ð¢ÐÐ£ÐÐ¢", "ÐÐÐ¢ÐÐ Ð ÐÐ«ÐÐÐÐ£",
                  "Ð ÐÐÐ Ð¯ÐÐÐ", "Ð ÐÐÐÐÐÐÐÐ¢"]
    fail_markers = ["â", "ÐÐÐÐÐ§ÐÐÐ", "ÐÐ ÐÐÐÐ£Ð©", "ÐÐÐÐ¡ÐÐ ÐÐÐ", "Ð¢Ð ÐÐÐ£ÐÐ¢", "ÐÐÐ Ð£Ð¨ÐÐÐ",
                    "ÐÐ¢ÐÐÐÐÐÐÐ", "ÐÐÐ¡ÐÐÐ¢ÐÐÐ¢Ð¡Ð¢Ð"]
    for m in ok_markers:
        if m in a:
            return "ok"
    for m in fail_markers:
        if m in a:
            return "fail"
    return "pending"

# âââ ÐÐ ÐÐÐÐ¢Ð« ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
VEHICLE_INSPECTION_PROMPT = """Ð¢Ñ â ÑÑÐ°ÑÑÐ¸Ð¹ Ð¼ÐµÑÐ°Ð½Ð¸Ðº Ð¡Ð ÐÐÐÐ . Ð¢ÐµÐ±Ðµ Ð¿ÑÐµÐ´Ð¾ÑÑÐ°Ð²Ð»ÐµÐ½Ñ ÐºÐ°Ð´ÑÑ Ð¸Ð· Ð²Ð¸Ð´ÐµÐ¾ ÐºÑÑÐ³Ð¾Ð²Ð¾Ð³Ð¾ Ð¾ÑÐ¼Ð¾ÑÑÐ° Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ Ð¿ÐµÑÐµÐ´ Ð²ÑÐµÐ·Ð´Ð¾Ð¼.
ÐÑÐ¾Ð²ÐµÑÑ ÑÐµÑÐ½Ð¸ÑÐµÑÐºÐ¾Ðµ ÑÐ¾ÑÑÐ¾ÑÐ½Ð¸Ðµ Ð¿Ð¾ ÐºÐ°Ð¶Ð´Ð¾Ð¼Ñ Ð¿ÑÐ½ÐºÑÑ Ð¸ Ð·Ð°ÑÐ¸ÐºÑÐ¸ÑÑÐ¹ Ð²ÑÐµ Ð¾ÑÐºÐ»Ð¾Ð½ÐµÐ½Ð¸Ñ:

1. ð¦ Ð¡ÐÐÐ¢ÐÐÐ«Ð ÐÐ ÐÐÐÐ Ð« (Ð¾Ð±ÑÐ·Ð°ÑÐµÐ»ÑÐ½Ð°Ñ Ð¿ÑÐ¾Ð²ÐµÑÐºÐ° Ð¿Ð¾ Ð²Ð¸Ð´ÐµÐ¾)
   - ÐÐµÑÐµÐ´Ð½Ð¸Ðµ ÑÐ°ÑÑ (Ð±Ð»Ð¸Ð¶Ð½Ð¸Ð¹ ÑÐ²ÐµÑ) â ÑÐ°Ð±Ð¾ÑÐ°ÑÑ?
   - ÐÐ°Ð´Ð½Ð¸Ðµ ÑÐ¾Ð½Ð°ÑÐ¸ Ð¸ ÑÑÐ¾Ð¿-ÑÐ¸Ð³Ð½Ð°Ð»Ñ â ÑÐ°Ð±Ð¾ÑÐ°ÑÑ?
   - ÐÐ¾Ð²Ð¾ÑÐ¾ÑÐ½Ð¸ÐºÐ¸ (Ð¿ÐµÑÐµÐ´Ð½Ð¸Ðµ Ð¸ Ð·Ð°Ð´Ð½Ð¸Ðµ) â ÑÐ°Ð±Ð¾ÑÐ°ÑÑ?
   - ÐÐ¾Ð´ÑÐ²ÐµÑÐºÐ° Ð³Ð¾ÑÑÐ´Ð°ÑÑÑÐ²ÐµÐ½Ð½ÑÑ Ð½Ð¾Ð¼ÐµÑÐ¾Ð² â ÑÐ°Ð±Ð¾ÑÐ°ÐµÑ?
   - ÐÐ°Ð±Ð°ÑÐ¸ÑÐ½ÑÐµ Ð¾Ð³Ð½Ð¸ â Ð¸ÑÐ¿ÑÐ°Ð²Ð½Ñ?

2. ð¿ Ð§ÐÐ¡Ð¢ÐÐ¢Ð ÐÐÐ¢ÐÐÐÐÐÐÐ¯
   - ÐÐ±ÑÐ°Ñ ÑÐ¸ÑÑÐ¾ÑÐ° ÐºÑÐ·Ð¾Ð²Ð°: Ð´Ð¾Ð¿ÑÑÑÐ¸Ð¼Ð¾ ÑÐ°Ð±Ð¾ÑÐµÐµ Ð·Ð°Ð³ÑÑÐ·Ð½ÐµÐ½Ð¸Ðµ, Ð½ÐµÐ´Ð¾Ð¿ÑÑÑÐ¸Ð¼Ð¾ â ÑÐ¸Ð»ÑÐ½Ð¾Ðµ Ð·Ð°Ð³ÑÑÐ·Ð½ÐµÐ½Ð¸Ðµ, Ð³ÑÑÐ·Ñ ÑÐºÑÑÐ²Ð°ÑÑÐ°Ñ Ð¿Ð¾Ð²ÑÐµÐ¶Ð´ÐµÐ½Ð¸Ñ
   - ÐÐ¾ÑÑÐ´Ð°ÑÑÑÐ²ÐµÐ½Ð½ÑÐµ Ð½Ð¾Ð¼ÐµÑÐ° (Ð¿ÐµÑÐµÐ´Ð½Ð¸Ð¹ Ð¸ Ð·Ð°Ð´Ð½Ð¸Ð¹): ÑÐ¸ÑÐ°ÐµÐ¼Ñ Ð¸ ÑÐ¸ÑÑÑ? ÐÐ°Ð³ÑÑÐ·Ð½ÑÐ½Ð½ÑÐ¹/Ð½ÐµÑÐ¸ÑÐ°ÐµÐ¼ÑÐ¹ Ð½Ð¾Ð¼ÐµÑ â Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ðµ

3. ð ÐÐ£ÐÐÐ Ð ÐÐ¡Ð¢ÐÐÐÐÐÐÐ
   - ÐÐ¸Ð´Ð¸Ð¼ÑÐµ Ð¿Ð¾Ð²ÑÐµÐ¶Ð´ÐµÐ½Ð¸Ñ: Ð²Ð¼ÑÑÐ¸Ð½Ñ, ÑÐ°ÑÐ°Ð¿Ð¸Ð½Ñ, Ð´ÐµÑÐ¾ÑÐ¼Ð°ÑÐ¸Ð¸
   - Ð¦ÐµÐ»Ð¾ÑÑÐ½Ð¾ÑÑÑ ÑÑÑÐºÐ¾Ð» (Ð»Ð¾Ð±Ð¾Ð²Ð¾Ðµ, Ð±Ð¾ÐºÐ¾Ð²ÑÐµ, Ð·Ð°Ð´Ð½ÐµÐµ)

4. ð ÐÐÐÐÐ¡Ð Ð Ð¨ÐÐÐ«
   - Ð¡Ð¾ÑÑÐ¾ÑÐ½Ð¸Ðµ ÑÐ¸Ð½: ÑÐ¿ÑÑÐµÐ½Ð½ÑÐµ, Ð¿Ð¾Ð²ÑÐµÐ¶Ð´ÐµÐ½Ð¸Ñ Ð±Ð¾ÐºÐ¾Ð²Ð¸Ð½, Ð¾ÑÑÐ°ÑÐ¾Ðº Ð¿ÑÐ¾ÑÐµÐºÑÐ¾ÑÐ°
   - Ð¡Ð¾ÑÑÐ¾ÑÐ½Ð¸Ðµ Ð´Ð¸ÑÐºÐ¾Ð²

5. ðï¸ Ð¢ÐÐÐ¢ / ÐÐ Ð£ÐÐÐÐÐ ÐÐ¢Ð¡ÐÐ (ÐµÑÐ»Ð¸ ÐµÑÑÑ)
   - Ð¦ÐµÐ»Ð¾ÑÑÐ½Ð¾ÑÑÑ ÑÐµÐ½ÑÐ°, Ð½Ð°Ð»Ð¸ÑÐ¸Ðµ ÑÐ°Ð·ÑÑÐ²Ð¾Ð² Ð¸Ð»Ð¸ Ð´ÑÑ
   - Ð¡Ð¾ÑÑÐ¾ÑÐ½Ð¸Ðµ ÐºÑÐµÐ¿ÐµÐ¶ÐµÐ¹, Ð´ÑÐ³, Ð±Ð¾ÑÑÐ¾Ð²

6. ð¨ ÐÐÐÐÐÐ¢ÐÐÐ¬ (Ð¿Ð¾ Ð²Ð½ÐµÑÐ½Ð¸Ð¼ Ð¿ÑÐ¸Ð·Ð½Ð°ÐºÐ°Ð¼)
   - ÐÑÐ¼ Ð¸Ð· Ð²ÑÑÐ»Ð¾Ð¿Ð½Ð¾Ð¹ ÑÑÑÐ±Ñ (ÑÐ²ÐµÑ, Ð¸Ð½ÑÐµÐ½ÑÐ¸Ð²Ð½Ð¾ÑÑÑ)
   - ÐÐ¸Ð´Ð¸Ð¼ÑÐµ Ð¿Ð¾Ð´ÑÑÐºÐ¸ Ð¼Ð°ÑÐ»Ð°, Ð¾ÑÐ»Ð°Ð¶Ð´Ð°ÑÑÐµÐ¹ Ð¶Ð¸Ð´ÐºÐ¾ÑÑÐ¸ Ð¿Ð¾Ð´ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¼

7. â ÐÐÐÐÐ®Ð§ÐÐÐÐ ÐÐÐ¥ÐÐÐÐÐ
   - ÐÐ¾Ð¿ÑÑÐµÐ½ Ð»Ð¸ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ Ðº Ð²ÑÐµÐ·Ð´Ñ?
   - ÐÐ¾Ð»Ð½ÑÐ¹ Ð¿ÐµÑÐµÑÐµÐ½Ñ Ð²ÑÑÐ²Ð»ÐµÐ½Ð½ÑÑ Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ð¹ (ÐµÑÐ»Ð¸ ÐµÑÑÑ)

ÐÑÐ»Ð¸ ÐºÐ°ÐºÐ¾Ð¹-ÑÐ¾ ÑÐ»ÐµÐ¼ÐµÐ½Ñ Ð½Ðµ Ð¿Ð¾Ð¿Ð°Ð» Ð² ÐºÐ°Ð´Ñ â ÑÐµÑÑÐ½Ð¾ ÑÐºÐ°Ð¶Ð¸ ÑÑÐ¾."""

WAYBILL_PROMPT = """Ð¢Ñ â ÑÑÐ°ÑÑÐ¸Ð¹ Ð¼ÐµÑÐ°Ð½Ð¸Ðº Ð¡Ð ÐÐÐÐ . ÐÑÐ¾Ð²ÐµÑÑ ÐÐ£Ð¢ÐÐÐÐ ÐÐÐ¡Ð¢ Ð½Ð° Ð¿ÑÐ°Ð²Ð¸Ð»ÑÐ½Ð¾ÑÑÑ Ð·Ð°Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ñ.

ÐÑÐ¾Ð²ÐµÑÑÐ¹ Ð¿Ð¾ Ð¿ÑÐ½ÐºÑÐ°Ð¼:
1. ð ÐÐÐ¢Ð â ÑÐºÐ°Ð·Ð°Ð½Ð° Ð»Ð¸, ÑÐ¾Ð¾ÑÐ²ÐµÑÑÑÐ²ÑÐµÑ Ð»Ð¸ ÑÐµÐ³Ð¾Ð´Ð½ÑÑÐ½ÐµÐ¼Ñ ÑÐ¸ÑÐ»Ñ?
2. ð§ ÐÐ¢ÐÐÐ¢ÐÐ ÐÐÐ¥ÐÐÐÐÐ â ÐµÑÑÑ Ð»Ð¸ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑ/ÑÑÐ°Ð¼Ð¿ Ð¼ÐµÑÐ°Ð½Ð¸ÐºÐ° Ð¾ Ð´Ð¾Ð¿ÑÑÐºÐµ Ð¢Ð¡ Ðº Ð²ÑÐµÐ·Ð´Ñ?
3. ð¥ ÐÐ¢ÐÐÐ¢ÐÐ ÐÐÐÐÐÐ â ÐµÑÑÑ Ð»Ð¸ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑ/ÑÑÐ°Ð¼Ð¿ Ð¼ÐµÐ´Ð¸ÐºÐ° Ð¾ Ð´Ð¾Ð¿ÑÑÐºÐµ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ?
4. ð ÐÐÐ§ÐÐÐ¬ÐÐ«Ð ÐÐ ÐÐÐÐ â ÑÐºÐ°Ð·Ð°Ð½ Ð»Ð¸ Ð¿Ð¾ÐºÐ°Ð·Ð°ÑÐµÐ»Ñ Ð¾Ð´Ð¾Ð¼ÐµÑÑÐ° Ð¿ÑÐ¸ Ð²ÑÐµÐ·Ð´Ðµ?
5. ð ÐÐÐÐÐÐÐÐÐÐÐ¡Ð¢Ð¬ â Ð·Ð°Ð¿Ð¾Ð»Ð½ÐµÐ½Ñ Ð»Ð¸ Ð²ÑÐµ Ð¾Ð±ÑÐ·Ð°ÑÐµÐ»ÑÐ½ÑÐµ Ð¿Ð¾Ð»Ñ: Ð¼Ð°ÑÑÑÑÑ, Ð¤ÐÐ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ, Ð³Ð¾Ñ.Ð½Ð¾Ð¼ÐµÑ, Ð½Ð°Ð¸Ð¼ÐµÐ½Ð¾Ð²Ð°Ð½Ð¸Ðµ Ð¾ÑÐ³Ð°Ð½Ð¸Ð·Ð°ÑÐ¸Ð¸?
6. ð ÐÐÐ§ÐÐ¢Ð â Ð¿ÑÐ¾ÑÑÐ°Ð²Ð»ÐµÐ½Ñ Ð»Ð¸ Ð½ÐµÐ¾Ð±ÑÐ¾Ð´Ð¸Ð¼ÑÐµ Ð¿ÐµÑÐ°ÑÐ¸?

ÐÑÐ½ÐµÑÐ¸ ÑÑÑÐºÐ¾Ðµ Ð·Ð°ÐºÐ»ÑÑÐµÐ½Ð¸Ðµ Ð¼ÐµÑÐ°Ð½Ð¸ÐºÐ°:
â ÐÐ£Ð¢ÐÐÐÐ ÐÐÐ¡Ð¢ Ð ÐÐÐ Ð¯ÐÐÐ â ÐµÑÐ»Ð¸ Ð²ÑÐµ ÐºÐ»ÑÑÐµÐ²ÑÐµ Ð¿Ð¾Ð»Ñ Ð·Ð°Ð¿Ð¾Ð»Ð½ÐµÐ½Ñ ÐºÐ¾ÑÑÐµÐºÑÐ½Ð¾
â ÐÐÐÐÐ§ÐÐÐÐ¯: [ÐºÐ¾Ð½ÐºÑÐµÑÐ½ÑÐ¹ Ð¿ÐµÑÐµÑÐµÐ½Ñ Ð½Ð°ÑÑÑÐµÐ½Ð¸Ð¹] â ÐµÑÐ»Ð¸ ÐµÑÑÑ Ð¾ÑÐ¸Ð±ÐºÐ¸ Ð¸Ð»Ð¸ Ð¿ÑÐ¾Ð¿ÑÑÐºÐ¸

ÐÑÐ»Ð¸ Ð¸Ð·Ð¾Ð±ÑÐ°Ð¶ÐµÐ½Ð¸Ðµ Ð½ÐµÑÑÑÐºÐ¾Ðµ Ð¸Ð»Ð¸ ÐºÐ°ÐºÐ¾Ð¹-ÑÐ¾ ÑÐ°Ð·Ð´ÐµÐ» Ð½Ðµ ÑÐ¸ÑÐ°ÐµÑÑÑ â ÑÐºÐ°Ð¶Ð¸ ÑÑÐ¾ Ð¾ÑÐ´ÐµÐ»ÑÐ½Ð¾."""

DASHBOARD_PROMPT = """Ð¢Ñ â ÑÑÐ°ÑÑÐ¸Ð¹ Ð¼ÐµÑÐ°Ð½Ð¸Ðº Ð¡Ð ÐÐÐÐ . ÐÑÐ¾Ð²ÐµÑÑ ÑÐ¾ÑÐ¾ ÐÐ ÐÐÐÐ ÐÐÐ ÐÐÐÐÐÐ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ.

ÐÑÐ¾Ð²ÐµÑÑÐ¹ Ð¿Ð¾ Ð¿ÑÐ½ÐºÑÐ°Ð¼:
1. ð¢ ÐÐÐÐÐÐ¢Ð  â Ð·Ð°ÑÐ¸ÐºÑÐ¸ÑÑÐ¹ Ð¿Ð¾ÐºÐ°Ð·Ð°Ð½Ð¸Ñ Ð¿ÑÐ¾Ð±ÐµÐ³Ð° (Ð¾Ð±ÑÐ·Ð°ÑÐµÐ»ÑÐ½Ð¾ Ð½Ð°Ð·Ð¾Ð²Ð¸ ÑÐ¸ÑÑÑ)
2. â½ Ð£Ð ÐÐÐÐÐ¬ Ð¢ÐÐÐÐÐÐ â Ð´Ð¾ÑÑÐ°ÑÐ¾ÑÐµÐ½ Ð»Ð¸? ÐÐµÐ½ÐµÐµ 1/4 Ð±Ð°ÐºÐ° â Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ðµ
3. ð¡ï¸ Ð¢ÐÐÐÐÐ ÐÐ¢Ð£Ð Ð ÐÐÐÐÐÐ¢ÐÐÐ¯ â Ð² Ð½Ð¾ÑÐ¼Ðµ? Ð¡ÑÑÐµÐ»ÐºÐ° Ð½Ðµ Ð´Ð¾Ð»Ð¶Ð½Ð° Ð±ÑÑÑ Ð² ÐºÑÐ°ÑÐ½Ð¾Ð¹ Ð·Ð¾Ð½Ðµ
4. â° ÐÐ ÐÐÐ¯ ÐÐ ÐÐÐÐÐÐ â ÑÐ¾Ð¾ÑÐ²ÐµÑÑÑÐ²ÑÐµÑ Ð»Ð¸ ÑÐµÐºÑÑÐµÐ¼Ñ Ð²ÑÐµÐ¼ÐµÐ½Ð¸? (Ð¾ÑÐºÐ»Ð¾Ð½ÐµÐ½Ð¸Ðµ Ð±Ð¾Ð»ÐµÐµ 5 Ð¼Ð¸Ð½ÑÑ â Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ðµ)
5. â ï¸ ÐÐÐÐÐÐÐ¢ÐÐ Ð« ÐÐÐÐ¡ÐÐ ÐÐÐÐÐ¡Ð¢ÐÐ â Ð³Ð¾ÑÐ¸Ñ Ð»Ð¸ Check Engine Ð¸Ð»Ð¸ Ð´ÑÑÐ³Ð¸Ðµ Ð¿ÑÐµÐ´ÑÐ¿ÑÐµÐ¶Ð´ÐµÐ½Ð¸Ñ?
6. ð ÐÐÐ â Ð½ÐµÑ Ð»Ð¸ Ð¸Ð½Ð´Ð¸ÐºÐ°ÑÐ¾ÑÐ° ÑÐ°Ð·ÑÑÐ´Ð° Ð°ÐºÐºÑÐ¼ÑÐ»ÑÑÐ¾ÑÐ°?

ÐÑÐ½ÐµÑÐ¸ ÑÑÑÐºÐ¾Ðµ Ð·Ð°ÐºÐ»ÑÑÐµÐ½Ð¸Ðµ Ð¼ÐµÑÐ°Ð½Ð¸ÐºÐ°:
â ÐÐ ÐÐÐÐ ÐÐÐ¯ ÐÐÐÐÐÐ¬ Ð ÐÐÐ ÐÐ â Ð²ÑÐµ Ð¿Ð°ÑÐ°Ð¼ÐµÑÑÑ Ð² Ð´Ð¾Ð¿ÑÑÑÐ¸Ð¼ÑÑ Ð¿ÑÐµÐ´ÐµÐ»Ð°Ñ
â ÐÐÐÐÐ§ÐÐÐÐ¯: [ÐºÐ¾Ð½ÐºÑÐµÑÐ½ÑÐ¹ Ð¿ÐµÑÐµÑÐµÐ½Ñ Ð¾ÑÐºÐ»Ð¾Ð½ÐµÐ½Ð¸Ð¹] â ÐµÑÐ»Ð¸ ÐµÑÑÑ Ð¿ÑÐ¾Ð±Ð»ÐµÐ¼Ñ

ÐÑÐ»Ð¸ ÑÑÐ¾-ÑÐ¾ Ð½Ðµ Ð²Ð¸Ð´Ð½Ð¾ Ð¸Ð»Ð¸ Ð½ÐµÑÐ¸ÑÐ°ÐµÐ¼Ð¾ Ð½Ð° ÑÐ¾ÑÐ¾ â ÑÐºÐ°Ð¶Ð¸ ÑÑÐ¾."""

DRIVER_PROMPT = """Ð¢Ñ â ÑÑÐ°ÑÑÐ¸Ð¹ Ð¼ÐµÑÐ°Ð½Ð¸Ðº Ð¡Ð ÐÐÐÐ . ÐÑÐ¾Ð²ÐµÑÑ ÑÐ¾ÑÐ¾ ÐÐÐÐÐ¢ÐÐÐ¯ Ð½Ð° ÑÐ¾Ð¾ÑÐ²ÐµÑÑÑÐ²Ð¸Ðµ ÑÑÐµÐ±Ð¾Ð²Ð°Ð½Ð¸ÑÐ¼ Ð¾ÑÑÐ°Ð½Ñ ÑÑÑÐ´Ð° Ð¸ ÑÐµÐ³Ð»Ð°Ð¼ÐµÐ½ÑÑ ÐÐÐÐ .

ÐÑÐ¾Ð²ÐµÑÑÐ¹ Ð¿Ð¾ Ð¿ÑÐ½ÐºÑÐ°Ð¼:
1. ð Ð¡ÐÐÐ¦ÐÐÐÐ¬ÐÐÐ¯ ÐÐÐ£ÐÐ¬ â Ð½Ð°Ð´ÐµÑÐ° Ð»Ð¸ ÑÐ¿ÐµÑÐ¾Ð±ÑÐ²Ñ?
   ÐÐÐ Ð£Ð¨ÐÐÐÐ: ÐºÑÐ¾ÑÑÐ¾Ð²ÐºÐ¸, ÐºÐµÐ´Ñ, ÑÑÑÐ»Ð¸, ÑÐ»Ð°Ð½ÑÑ â Ð½Ðµ Ð´Ð¾Ð¿ÑÑÐºÐ°ÑÑÑÑ. ÐÐ¾Ð»Ð¶Ð½Ð° Ð±ÑÑÑ ÑÐ°Ð±Ð¾ÑÐ°Ñ/Ð·Ð°ÑÐ¸ÑÐ½Ð°Ñ Ð¾Ð±ÑÐ²Ñ
2. ð Ð¡ÐÐÐ¦ÐÐÐÐÐÐ â Ð½Ð°Ð´ÐµÑ Ð»Ð¸ ÐºÐ¾Ð¼Ð¿Ð»ÐµÐºÑ ÑÐ°Ð±Ð¾ÑÐµÐ¹ Ð¾Ð´ÐµÐ¶Ð´Ñ (ÐºÐ¾Ð¼Ð±Ð¸Ð½ÐµÐ·Ð¾Ð½, ÐºÑÑÑÐºÐ°, Ð¶Ð¸Ð»ÐµÑ Ð¸ Ñ.Ð¿.)?
   Ð¡Ð¾ÑÑÐ¾ÑÐ½Ð¸Ðµ: ÑÐ¸ÑÑÐ°Ñ, Ð±ÐµÐ· ÑÐ²Ð½ÑÑ Ð·Ð°Ð³ÑÑÐ·Ð½ÐµÐ½Ð¸Ð¹, ÑÐ°Ð·ÑÑÐ²Ð¾Ð² Ð¸ Ð¿Ð¾Ð²ÑÐµÐ¶Ð´ÐµÐ½Ð¸Ð¹?
3. ð Ð¨ÐÐÐ ÐÐ / ÐÐÐÐÐ¢ÐÐ ÐÐÐÐ  â Ð²Ð¸Ð´ÐµÐ½ Ð»Ð¸ Ð½Ð° Ð¾Ð´ÐµÐ¶Ð´Ðµ ÑÐµÐ²ÑÐ¾Ð½ Ð¸Ð»Ð¸ Ð»Ð¾Ð³Ð¾ÑÐ¸Ð¿ Â«ÐÐÐÐ Â»?
4. ð¦º ÐÐÐÐÐÐÐÐ¢ÐÐÐ¡Ð¢Ð¬ â Ð²ÑÐµ ÑÐ»ÐµÐ¼ÐµÐ½ÑÑ ÑÐ¿ÐµÑÐ¾Ð´ÐµÐ¶Ð´Ñ Ð½Ð° Ð¼ÐµÑÑÐµ?
5. ð¤ Ð¤ÐÐ ÐÐÐÐÐ¢ÐÐÐ¯ â ÐµÑÐ»Ð¸ Ð² ÐºÐ¾Ð½ÑÐµÐºÑÑÐµ ÑÐºÐ°Ð·Ð°Ð½Ð¾ Ð¤ÐÐ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ Ð¸Ð· Ð¿ÑÑÐµÐ²Ð¾Ð³Ð¾ Ð»Ð¸ÑÑÐ°/ÑÐ¼ÐµÐ½Ñ, ÑÐºÐ°Ð¶Ð¸ ÐµÐ³Ð¾ Ð² Ð·Ð°ÐºÐ»ÑÑÐµÐ½Ð¸Ð¸. Ð­ÑÐ¾ Ð½ÑÐ¶Ð½Ð¾ Ð´Ð»Ñ Ð¿ÑÐ¸Ð²ÑÐ·ÐºÐ¸ ÑÐ¾ÑÐ¾ Ðº ÐºÐ¾Ð½ÐºÑÐµÑÐ½Ð¾Ð¼Ñ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ.

ÐÑÐ½ÐµÑÐ¸ ÑÑÑÐºÐ¾Ðµ Ð·Ð°ÐºÐ»ÑÑÐµÐ½Ð¸Ðµ Ð¼ÐµÑÐ°Ð½Ð¸ÐºÐ°:
â Ð¡ÐÐÐ¢ÐÐÐ¢Ð¡Ð¢ÐÐ£ÐÐ¢ Ð ÐÐÐÐÐÐÐÐ¢Ð£ â Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ Ð² Ð¿Ð¾Ð»Ð½Ð¾Ð¹ ÑÐ¿ÐµÑÐ¾Ð´ÐµÐ¶Ð´Ðµ ÐÐÐÐ 
â ÐÐÐÐÐ§ÐÐÐÐ¯: [ÐºÐ¾Ð½ÐºÑÐµÑÐ½ÑÐ¹ Ð¿ÐµÑÐµÑÐµÐ½Ñ Ð½Ð°ÑÑÑÐµÐ½Ð¸Ð¹] â ÐµÑÐ»Ð¸ ÐµÑÑÑ Ð½ÐµÑÐ¾Ð¾ÑÐ²ÐµÑÑÑÐ²Ð¸Ñ

ÐÑÐ»Ð¸ ÑÑÐ¾-ÑÐ¾ Ð½Ðµ Ð²Ð¸Ð´Ð½Ð¾ Ð½Ð° ÑÐ¾ÑÐ¾ Ð¸Ð»Ð¸ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ Ð½Ðµ Ð¿Ð¾Ð¿Ð°Ð» Ð² ÐºÐ°Ð´Ñ Ð¿Ð¾Ð»Ð½Ð¾ÑÑÑÑ â ÑÐºÐ°Ð¶Ð¸ ÑÑÐ¾."""

SCHEDULE_PARSE_PROMPT = """Ð¢Ñ â ÑÐ¸ÑÑÐµÐ¼Ð° ÑÐ°Ð·Ð±Ð¾ÑÐ° ÑÐ°Ð±Ð¾ÑÐ¸Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð² Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ°.
ÐÑÐ¾Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸ÑÑÐ¹ ÑÑÐ¾Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ (ÑÑÑÐµÐ½Ð½ÑÑ ÑÐ¿ÑÐ°Ð²ÐºÑ Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ° Ð¸Ð»Ð¸ Ð³ÑÐ°ÑÐ¸Ðº ÑÐ°Ð±Ð¾ÑÑ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¹).
ÐÐ·Ð²Ð»ÐµÐºÐ¸ ÑÐ¿Ð¸ÑÐ¾Ðº Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¹, ÐºÐ¾ÑÐ¾ÑÑÐµ ÑÐ°Ð±Ð¾ÑÐ°ÑÑ ÑÐµÐ³Ð¾Ð´Ð½Ñ.

ÐÐ»Ñ ÐºÐ°Ð¶Ð´Ð¾Ð³Ð¾ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ ÑÐºÐ°Ð¶Ð¸:
- ÐÐ¾Ñ. Ð½Ð¾Ð¼ÐµÑ Ð¸Ð»Ð¸ Ð²Ð½ÑÑÑÐµÐ½Ð½Ð¸Ð¹ Ð½Ð¾Ð¼ÐµÑ (vehicle_num)
- Ð¤ÐÐ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ (driver) â ÐµÑÐ»Ð¸ ÑÐºÐ°Ð·Ð°Ð½Ð¾, Ð¸Ð½Ð°ÑÐµ Ð¿ÑÑÑÐ°Ñ ÑÑÑÐ¾ÐºÐ°
- ÐÐ°ÐºÐ°Ð·ÑÐ¸Ðº / Ð¾Ð±ÑÐµÐºÑ (customer): Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ñ / ÐÐ Ð¡ Ð¡ÐµÑÐ²Ð¸Ñ / Ð£ÐÐ¢Ð / Ð¢Ð°ÑÐ±ÑÑÐ½ÐµÑÑÑ / Ð´ÑÑÐ³Ð¾Ð¹

ÐÑÐ²ÐµÐ´Ð¸ ÑÐµÐ·ÑÐ»ÑÑÐ°Ñ Ð¡Ð¢Ð ÐÐÐ Ð² ÑÐ¾ÑÐ¼Ð°ÑÐµ JSON (Ð±ÐµÐ· Ð»Ð¸ÑÐ½ÐµÐ³Ð¾ ÑÐµÐºÑÑÐ° Ð²Ð¾ÐºÑÑÐ³):
{"vehicles": [{"num": "Ð123ÐÐ116", "driver": "ÐÐ²Ð°Ð½Ð¾Ð² Ð.Ð.", "customer": "ÐÐ Ð¡ Ð¡ÐµÑÐ²Ð¸Ñ"}, ...]}

ÐÑÐ»Ð¸ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ Ð½Ðµ ÑÐ²Ð»ÑÐµÑÑÑ Ð³ÑÐ°ÑÐ¸ÐºÐ¾Ð¼ Ð¸Ð»Ð¸ ÑÐ¿ÑÐ°Ð²ÐºÐ¾Ð¹ â Ð²ÐµÑÐ½Ð¸:
{"vehicles": [], "error": "ÐÐµ ÑÐ²Ð»ÑÐµÑÑÑ ÑÐ°ÑÐ¿Ð¸ÑÐ°Ð½Ð¸ÐµÐ¼"}"""

VEHICLE_SUMMARY_PROMPT = """Ты — старший механик СК ВАИР. Проанализируй ВСЕ результаты проверки автомобиля и сделай итоговую сводку.

Ниже указаны результаты проверки каждого документа. На основании ВСЕХ результатов составь:

1. ВЫЖИМКА ДЛЯ ВОДИТЕЛЯ — что нужно переделать/доснять:
   - Какие фото/видео нужно переснять (обход заново, доснять стёкла/зеркала, протереть гос.номер и т.п.)
   - Какие документы исправить

2. ЗАДАЧИ ДЛЯ МЕХАНИКА — технические замечания:
   - Плановое обслуживание (обработка порогов от коррозии, замена ламп и т.п.)
   - Выявленные неисправности, требующие ремонта
   - Проверки, которые нужно назначить (даты, сроки)

Если все документы в порядке — напиши: ВСЕ В НОРМЕ, автомобиль допущен к выезду.
Отвечай структурированно и кратко."""

# âââ Ð¡ÐÐ¡Ð¢ÐÐÐÐ«Ð ÐÐ ÐÐÐÐ¢ âââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def get_system_prompt():
    for path in ["CLAUDE.md", "/app/CLAUDE.md"]:
        p = Path(path)
        if p.exists():
            base = p.read_text(encoding="utf-8")
            break
    else:
        base = (
            "Ð¢Ñ â ÐÐ³ÐµÐ½Ñ Q, ÑÑÐ°ÑÑÐ¸Ð¹ Ð¼ÐµÑÐ°Ð½Ð¸Ðº ÑÑÐ°Ð½ÑÐ¿Ð¾ÑÑÐ½Ð¾Ð¹ ÐºÐ¾Ð¼Ð¿Ð°Ð½Ð¸Ð¸ Ð¡Ð ÐÐÐÐ . "
            "Ð¢Ð²Ð¾Ñ Ð·Ð°Ð´Ð°ÑÐ° â Ð¿ÑÐ¾Ð²ÐµÑÑÑÑ Ð¿ÑÐ°Ð²Ð¸Ð»ÑÐ½Ð¾ÑÑÑ Ð·Ð°Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð² ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ð²Ð¾Ð´Ð¸ÑÐµÐ»ÐµÐ¹ "
            "Ð¸ ÑÐ¸ÐºÑÐ¸ÑÐ¾Ð²Ð°ÑÑ Ð²ÑÐµ Ð¾ÑÐºÐ»Ð¾Ð½ÐµÐ½Ð¸Ñ Ð¸ Ð½Ð°ÑÑÑÐµÐ½Ð¸Ñ. "
            "Ð¢Ñ ÑÑÑÐ¾Ð³, Ð²Ð½Ð¸Ð¼Ð°ÑÐµÐ»ÐµÐ½ Ðº Ð´ÐµÑÐ°Ð»ÑÐ¼ Ð¸ ÑÐ¾ÑÐ¾ÑÐ¾ Ð·Ð½Ð°ÐµÑÑ ÑÐµÐ³Ð»Ð°Ð¼ÐµÐ½ÑÑ ÐÐÐÐ . "
            "ÐÑÐ²ÐµÑÐ°Ð¹ ÑÑÑÐºÐ¾, Ð¿Ð¾ Ð´ÐµÐ»Ñ: ÑÐ½Ð°ÑÐ°Ð»Ð° â Ð²ÐµÑÐ´Ð¸ÐºÑ (Ð´Ð¾Ð¿ÑÑÐµÐ½ / ÐµÑÑÑ Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ñ), "
            "Ð·Ð°ÑÐµÐ¼ â ÐºÐ¾Ð½ÐºÑÐµÑÐ½ÑÐ¹ Ð¿ÐµÑÐµÑÐµÐ½Ñ Ð½Ð°ÑÑÑÐµÐ½Ð¸Ð¹ Ð¸Ð»Ð¸ Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð¸Ðµ ÑÐ¾Ð¾ÑÐ²ÐµÑÑÑÐ²Ð¸Ñ. "
            "ÐÐµ Ð´Ð¾Ð¿ÑÑÐºÐ°Ð¹ ÑÐ°ÑÐ¿Ð»ÑÐ²ÑÐ°ÑÑÑ ÑÐ¾ÑÐ¼ÑÐ»Ð¸ÑÐ¾Ð²Ð¾Ðº â ÐºÐ°Ð¶Ð´Ð¾Ðµ Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ðµ Ð´Ð¾Ð»Ð¶Ð½Ð¾ Ð±ÑÑÑ ÐºÐ¾Ð½ÐºÑÐµÑÐ½ÑÐ¼."
        )
    memories = get_memories()
    if memories:
        mem_lines = "\n".join(f"- [{m[1]}] {m[0]}" for m in memories)
        base += f"\n\n## ÐÐºÑÐ¸Ð²Ð½ÑÐµ Ð²Ð¾ÑÐ¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ñ\n{mem_lines}"
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    base += f"\n\n## Ð¢ÐµÐºÑÑÐµÐµ Ð²ÑÐµÐ¼Ñ\n{now}"
    return base

# âââ CLAUDE API âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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
        return f"â ÐÑÐ¸Ð±ÐºÐ° API: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return f"â ÐÑÐ¸Ð±ÐºÐ°: {str(e)}"

def build_messages(new_role: str, new_content) -> list:
    history = get_history()
    messages = []
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": new_role, "content": new_content})
    return messages

# âââ ÐÐÐ¢ÐÐ ÐÐÐÐ¦ÐÐ¯ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def is_authorized(update: Update) -> bool:
    if OWNER_ID == 0:
        return True
    return update.effective_user.id == OWNER_ID

async def deny(update: Update):
    await update.message.reply_text("â ÐÐ¾ÑÑÑÐ¿ Ð·Ð°Ð¿ÑÐµÑÑÐ½.")

# âââ ÐÐ¢ÐÐ ÐÐÐÐ ÐÐÐÐÐÐ«Ð¥ Ð¡ÐÐÐÐ©ÐÐÐÐ âââââââââââââââââââââââââââââââââââââââââââââââ
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

# âââ ÐÐÐÐÐÐÐ«: ÐÐ¡ÐÐÐÐÐ«Ð ââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    uid = update.effective_user.id
    await update.message.reply_text(
        f"ð ÐÑÐ¸Ð²ÐµÑ! ÐÐ³ÐµÐ½Ñ Q Ð·Ð°Ð¿ÑÑÐµÐ½.\n"
        f"Ð¢Ð²Ð¾Ð¹ Telegram ID: `{uid}`\n\n"
        f"*ÐÐ¾Ð¼Ð°Ð½Ð´Ñ ÑÐ¿ÑÐ°Ð²Ð»ÐµÐ½Ð¸Ñ ÑÐ¼ÐµÐ½Ð¾Ð¹:*\n"
        f"/shift â ÑÑÐ°ÑÑÑ ÑÐµÐºÑÑÐµÐ¹ ÑÐ¼ÐµÐ½Ñ\n"
        f"/report â Ð¿Ð¾Ð»Ð½ÑÐ¹ Ð¾ÑÑÑÑ Ð¿Ð¾ ÑÐ¼ÐµÐ½Ðµ\n"
        f"/newshift â Ð½Ð°ÑÐ°ÑÑ Ð½Ð¾Ð²ÑÑ ÑÐ¼ÐµÐ½Ñ\n"
        f"/set [Ð½Ð¾Ð¼ÐµÑ] â ÑÑÑÐ°Ð½Ð¾Ð²Ð¸ÑÑ Ð°ÐºÑÐ¸Ð²Ð½ÑÐ¹ Ð°/Ð¼\n\n"
        f"*ÐÐ±ÑÐ¸Ðµ ÐºÐ¾Ð¼Ð°Ð½Ð´Ñ:*\n"
        f"/memory â Ð²Ð¾ÑÐ¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ñ\n"
        f"/checkpoint â ÑÐ¾ÑÑÐ°Ð½Ð¸ÑÑ ÐºÐ¾Ð½ÑÐµÐºÑÑ\n"
        f"/status â ÑÑÐ°ÑÑÑ ÑÐ¸ÑÑÐµÐ¼Ñ\n"
        f"/clear â Ð¾ÑÐ¸ÑÑÐ¸ÑÑ Ð¸ÑÑÐ¾ÑÐ¸Ñ Ð´Ð¸Ð°Ð»Ð¾Ð³Ð°\n\n"
        f"*ÐÐ°Ðº ÑÐ°Ð±Ð¾ÑÐ°ÑÑ ÑÐ¾ ÑÐ¼ÐµÐ½Ð¾Ð¹:*\n"
        f"1. ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐ¾ÑÐ¾ ÑÐ¿ÑÐ°Ð²ÐºÐ¸/Ð³ÑÐ°ÑÐ¸ÐºÐ° Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ° Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑÑ Â«ÑÐ¿ÑÐ°Ð²ÐºÐ°Â»\n"
        f"2. ÐÐ»Ñ ÐºÐ°Ð¶Ð´Ð¾Ð³Ð¾ Ð°/Ð¼ Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ 4 Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ° Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑÑ:\n"
        f"   â¢ Â«Ð²Ð¸Ð´ÐµÐ¾ Ð123ÐÐÂ» â Ð²Ð¸Ð´ÐµÐ¾ ÐºÑÑÐ³Ð¾Ð²Ð¾Ð³Ð¾ Ð¾ÑÐ¼Ð¾ÑÑÐ°\n"
        f"   â¢ Â«Ð¿ÑÑÐµÐ²Ð¾Ð¹ Ð123ÐÐÂ» â ÑÐ¾ÑÐ¾ Ð¿ÑÑÐµÐ²Ð¾Ð³Ð¾ Ð»Ð¸ÑÑÐ°\n"
        f"   â¢ Â«Ð¿ÑÐ¸Ð±Ð¾ÑÐ½Ð°Ñ Ð123ÐÐÂ» â ÑÐ¾ÑÐ¾ Ð¿Ð°Ð½ÐµÐ»Ð¸ Ð¿ÑÐ¸Ð±Ð¾ÑÐ¾Ð²\n"
        f"   â¢ Â«Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ Ð123ÐÐÂ» â ÑÐ¾ÑÐ¾ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ Ð² ÑÐ¿ÐµÑÐ¾Ð´ÐµÐ¶Ð´Ðµ\n"
        f"3. ÐÐ»Ð¸ ÑÑÑÐ°Ð½Ð¾Ð²Ð¸ÑÐµ Ð°ÐºÑÐ¸Ð²Ð½ÑÐ¹ Ð°/Ð¼ ÑÐµÑÐµÐ· /set Ð¸ Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ Ð±ÐµÐ· Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¸",
        parse_mode="Markdown"
    )

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    memories = get_memories()
    if not memories:
        await update.message.reply_text("ð­ ÐÐ¾ÑÐ¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ð¹ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ.")
        return
    lines = ["ð§  *ÐÐ¾ÑÐ¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ñ:*\n"]
    for i, (content, category, created_at) in enumerate(memories, 1):
        date = created_at[:10] if created_at else "?"
        lines.append(f"*{i}.* [{category}] {content}\n_({date})_\n")
    await send_long(update, "\n".join(lines))

async def cmd_checkpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    await update.message.reply_text("ð¾ Ð¡Ð¾Ð·Ð´Ð°Ñ checkpoint...")
    history = get_history(20)
    if not history:
        await update.message.reply_text("ÐÐµÑ Ð¸ÑÑÐ¾ÑÐ¸Ð¸ Ð´Ð»Ñ ÑÐ¾ÑÑÐ°Ð½ÐµÐ½Ð¸Ñ.")
        return
    history_text = "\n".join(
        f"{role.upper()}: {content[:300]}" for role, content in history
    )
    prompt = (
        "Ð¡Ð´ÐµÐ»Ð°Ð¹ ÐºÑÐ°ÑÐºÐ¾Ðµ ÑÐµÐ·ÑÐ¼Ðµ (3-5 Ð¿ÑÐ½ÐºÑÐ¾Ð²) ÐºÐ»ÑÑÐµÐ²ÑÑ ÑÐµÑÐµÐ½Ð¸Ð¹, "
        "ÑÐ°ÐºÑÐ¾Ð² Ð¸ Ð´Ð¾Ð³Ð¾Ð²Ð¾ÑÑÐ½Ð½Ð¾ÑÑÐµÐ¹ Ð¸Ð· ÑÑÐ¾Ð³Ð¾ ÑÐ°Ð·Ð³Ð¾Ð²Ð¾ÑÐ°:\n\n" + history_text
    )
    summary = await ask_claude([{"role": "user", "content": prompt}])
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    save_memory(f"[Checkpoint {timestamp}]\n{summary}", category="checkpoint", salience=5.0)
    await update.message.reply_text(
        f"â Checkpoint ÑÐ¾ÑÑÐ°Ð½ÑÐ½:\n\n{summary}\n\n"
        f"_Ð¢ÐµÐ¿ÐµÑÑ Ð¼Ð¾Ð¶Ð½Ð¾ Ð½Ð°ÑÐ°ÑÑ Ð½Ð¾Ð²ÑÐ¹ ÑÐ°Ñ â ÐºÐ¾Ð½ÑÐµÐºÑÑ ÑÐ¾ÑÑÐ°Ð½ÑÐ½._",
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
    active = agent_state["active_vehicle"] or "Ð½Ðµ Ð·Ð°Ð´Ð°Ð½"
    await update.message.reply_text(
        f"ð *Ð¡ÑÐ°ÑÑÑ ÐÐ³ÐµÐ½ÑÐ° Q*\n\n"
        f"ð§  ÐÐ¾ÑÐ¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ð¹: {mem_count}\n"
        f"ð¬ ÐÐ°Ð¿Ð¸ÑÐµÐ¹ Ð² Ð¸ÑÑÐ¾ÑÐ¸Ð¸: {hist_count}\n"
        f"ð ÐÐ²ÑÐ¾ Ð² ÑÐ¼ÐµÐ½Ðµ ÑÐµÐ³Ð¾Ð´Ð½Ñ: {shift_count}\n"
        f"ð ÐÐºÑÐ¸Ð²Ð½ÑÐ¹ Ð°/Ð¼: {active}\n"
        f"ð¤ ÐÐ¾Ð´ÐµÐ»Ñ: `{MODEL}`\n"
        f"â° Ð¡ÐµÐ¹ÑÐ°Ñ: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        parse_mode="Markdown"
    )

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    clear_history()
    await update.message.reply_text(
        "ð ÐÑÑÐ¾ÑÐ¸Ñ Ð´Ð¸Ð°Ð»Ð¾Ð³Ð° Ð¾ÑÐ¸ÑÐµÐ½Ð°.\n"
        "ÐÐ¾Ð»Ð³Ð¾ÑÑÐ¾ÑÐ½Ð°Ñ Ð¿Ð°Ð¼ÑÑÑ Ð¸ Ð´Ð°Ð½Ð½ÑÐµ ÑÐ¼ÐµÐ½Ñ ÑÐ¾ÑÑÐ°Ð½ÐµÐ½Ñ."
    )

# âââ ÐÐÐÐÐÐÐ«: ÐÐÐÐÐ¢ Q âââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
async def cmd_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ÐÐ¾ÐºÐ°Ð·ÑÐ²Ð°ÐµÑ ÑÐµÐºÑÑÐ¸Ð¹ ÑÑÐ°ÑÑÑ ÑÐ¼ÐµÐ½Ñ."""
    if not is_authorized(update):
        return await deny(update)
    await send_long(update, format_shift_status())

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ÐÐµÐ½ÐµÑÐ¸ÑÑÐµÑ Ð¿Ð¾Ð»Ð½ÑÐ¹ Ð¾ÑÑÑÑ Ð¿Ð¾ ÑÐ¼ÐµÐ½Ðµ."""
    if not is_authorized(update):
        return await deny(update)
    shifts = get_today_shifts()
    if not shifts:
        await update.message.reply_text(
            "ð­ ÐÐµÑ Ð´Ð°Ð½Ð½ÑÑ Ð´Ð»Ñ Ð¾ÑÑÑÑÐ°. Ð¡Ð½Ð°ÑÐ°Ð»Ð° Ð´Ð¾Ð±Ð°Ð²ÑÑÐµ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ð¸ Ð² ÑÐ¼ÐµÐ½Ñ."
        )
        return

    await update.message.reply_text("ð ÐÐµÐ½ÐµÑÐ¸ÑÑÑ Ð¾ÑÑÑÑ Ð¿Ð¾ ÑÐ¼ÐµÐ½Ðµ...")

    # ÐÐ¾ÑÑÐ´Ð¾Ðº Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð² Ð² Ð¾ÑÑÑÑÐµ
    REPORT_DOC_ORDER = [
        ("waybill",   "ÐÑÑÐµÐ²Ð¾Ð¹ Ð»Ð¸ÑÑ"),
        ("driver",    "Ð¤Ð¾ÑÐ¾ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ"),
        ("dashboard", "Ð¤Ð¾ÑÐ¾ Ð¿Ð°Ð½ÐµÐ»Ð¸ Ð¿ÑÐ¸Ð±Ð¾ÑÐ¾Ð²"),
        ("video",     "ÐÑÑÐ³Ð¾Ð²Ð¾Ð¹ Ð²Ð¸Ð´ÐµÐ¾Ð¾ÑÐ¼Ð¾ÑÑ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ"),
    ]

    lines = [f"ð ÐÐ¢Ð§ÐÐ¢ ÐÐ Ð¡ÐÐÐÐ {datetime.now().strftime('%d.%m.%Y')}\n"]
    total_ok = 0

    for shift_id, vehicle_num, driver, customer in shifts:
        docs = get_vehicle_docs(shift_id)

        lines.append(f"{'â'*35}")
        header = f"ð {vehicle_num}"
        if customer:
            header += f"  |  {customer}"
        if driver:
            header += f"  |  {driver}"
        lines.append(header)
        lines.append(f"{'â'*35}")

        # 1. ÐÑÑÑÑ â ÐµÑÑÑ Ð»Ð¸ ÑÐ¾ÑÑ Ð¾Ð´Ð¸Ð½ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ
        has_report = len(docs) > 0
        lines.append(f"1. ÐÑÑÑÑ: {'â ÐµÑÑÑ' if has_report else 'â Ð½ÐµÑ'}")

        # 2. ÐÐ¾Ð¼Ð¿Ð»ÐµÐºÑÐ½Ð¾ÑÑÑ â Ð²ÑÐµ 4 ÑÐ¸Ð¿Ð° Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð² Ð¿Ð¾Ð»ÑÑÐµÐ½Ñ
        missing_docs = [DOC_TYPES[dt] for dt in DOC_ORDER if dt not in docs]
        is_complete = len(missing_docs) == 0
        if is_complete:
            lines.append("2. ÐÐ¾Ð¼Ð¿Ð»ÐµÐºÑÐ½Ð¾ÑÑÑ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð²: â Ð¿Ð¾Ð»Ð½Ð°Ñ")
        else:
            lines.append("2. ÐÐ¾Ð¼Ð¿Ð»ÐµÐºÑÐ½Ð¾ÑÑÑ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð²: â Ð½ÐµÐ¿Ð¾Ð»Ð½Ð°Ñ")
            for m in missing_docs:
                lines.append(f"   â¢ Ð½Ðµ Ð¿Ð¾Ð»ÑÑÐµÐ½: {m}")

        # 3-6. ÐÐ¾ ÐºÐ°Ð¶Ð´Ð¾Ð¼Ñ ÑÐ¸Ð¿Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ°
        vehicle_ok = is_complete
        for i, (doc_type, label) in enumerate(REPORT_DOC_ORDER, start=3):
            if doc_type not in docs:
                lines.append(f"{i}. {label}: â³ Ð½Ðµ Ð¿Ð¾Ð»ÑÑÐµÐ½")
                vehicle_ok = False
            else:
                result, analysis = docs[doc_type]
                if result == "ok":
                    lines.append(f"{i}. {label}: â Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ð¹ Ð½ÐµÑ")
                elif result == "fail":
                    lines.append(f"{i}. {label}: â ÐµÑÑÑ Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ñ")
                    vehicle_ok = False
                    if analysis:
                        for remark in analysis.strip().split("\n"):
                            remark = remark.strip(" -â¢ââ")
                            if remark:
                                lines.append(f"   â¢ {remark}")
                else:
                    lines.append(f"{i}. {label}: â³ Ð² Ð¾Ð±ÑÐ°Ð±Ð¾ÑÐºÐµ")
                    vehicle_ok = False

        lines.append(f"â {'â Ð ÐÐ«ÐÐÐÐ£ ÐÐÐÐ£Ð©ÐÐ' if vehicle_ok else 'â ÐÐ¡Ð¢Ð¬ ÐÐÐÐÐ§ÐÐÐÐ¯'}")
        lines.append("")
        if vehicle_ok:
            total_ok += 1

    lines.append(f"{'â'*35}")
    lines.append(f"ÐÐ¢ÐÐÐ: {total_ok}/{len(shifts)} Ð°/Ð¼ Ð³Ð¾ÑÐ¾Ð²Ñ Ðº Ð²ÑÐµÐ·Ð´Ñ")
    lines.append(f"ÐÑÐµÐ¼Ñ Ð¾ÑÑÑÑÐ°: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    await send_long(update, "\n".join(lines))

async def cmd_newshift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ÐÐ°ÑÐ¸Ð½Ð°ÐµÑ Ð½Ð¾Ð²ÑÑ ÑÐ¼ÐµÐ½Ñ â ÑÐ´Ð°Ð»ÑÐµÑ Ð´Ð°Ð½Ð½ÑÐµ Ð·Ð° ÑÐµÐ³Ð¾Ð´Ð½Ñ."""
    if not is_authorized(update):
        return await deny(update)
    clear_today_shifts()
    agent_state["active_vehicle"] = None
    agent_state["awaiting"] = None
    await update.message.reply_text(
        f"ð ÐÐ¾Ð²Ð°Ñ ÑÐ¼ÐµÐ½Ð° Ð½Ð°ÑÐ°ÑÐ° ({datetime.now().strftime('%d.%m.%Y')}).\n"
        "ÐÐ°Ð½Ð½ÑÐµ Ð¿ÑÐµÐ´ÑÐ´ÑÑÐµÐ¹ ÑÐ¼ÐµÐ½Ñ Ð·Ð° ÑÐµÐ³Ð¾Ð´Ð½Ñ ÑÐ´Ð°Ð»ÐµÐ½Ñ.\n\n"
        "ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐ¾ÑÐ¾ ÑÐ¿ÑÐ°Ð²ÐºÐ¸ Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ° Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑÑ Â«ÑÐ¿ÑÐ°Ð²ÐºÐ°Â» "
        "Ð¸Ð»Ð¸ Ð´Ð¾Ð±Ð°Ð²ÑÑÐµ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ð¸ ÑÐµÑÐµÐ· /set [Ð½Ð¾Ð¼ÐµÑ]."
    )

async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ð£ÑÑÐ°Ð½Ð°Ð²Ð»Ð¸Ð²Ð°ÐµÑ Ð°ÐºÑÐ¸Ð²Ð½ÑÐ¹ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ: /set Ð123ÐÐ116."""
    if not is_authorized(update):
        return await deny(update)
    args = context.args
    if not args:
        current = agent_state["active_vehicle"] or "Ð½Ðµ Ð·Ð°Ð´Ð°Ð½"
        await update.message.reply_text(
            f"ð Ð¢ÐµÐºÑÑÐ¸Ð¹ Ð°ÐºÑÐ¸Ð²Ð½ÑÐ¹ Ð°/Ð¼: *{current}*\n\n"
            f"ÐÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°Ð½Ð¸Ðµ: `/set Ð123ÐÐ116`\n"
            f"ÐÐ¾ÑÐ»Ðµ ÑÑÐ¾Ð³Ð¾ Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÑ Ð±ÐµÐ· Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¸ â "
            f"Ð¾Ð½Ð¸ Ð°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸ÑÐµÑÐºÐ¸ Ð¿ÑÐ¸Ð²ÑÐ¶ÑÑÑÑ Ðº ÑÑÐ¾Ð¼Ñ Ð°/Ð¼.",
            parse_mode="Markdown"
        )
        return
    vehicle_num = " ".join(args).strip().upper()
    agent_state["active_vehicle"] = vehicle_num
    # Ð£Ð±ÐµÐ¶Ð´Ð°ÐµÐ¼ÑÑ ÑÑÐ¾ Ð·Ð°Ð¿Ð¸ÑÑ Ð² ÑÐ¼ÐµÐ½Ðµ ÑÑÑÐµÑÑÐ²ÑÐµÑ
    shift_id = get_or_create_shift(vehicle_num)
    await update.message.reply_text(
        f"â ÐÐºÑÐ¸Ð²Ð½ÑÐ¹ Ð°/Ð¼ ÑÑÑÐ°Ð½Ð¾Ð²Ð»ÐµÐ½: *{vehicle_num}*\n\n"
        f"Ð¢ÐµÐ¿ÐµÑÑ Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÑ. Ð¢Ð¸Ð¿ Ð¾Ð¿ÑÐµÐ´ÐµÐ»ÑÐµÑÑÑ Ð°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸ÑÐµÑÐºÐ¸ Ð¿Ð¾ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¸:\n"
        f"â¢ Â«Ð²Ð¸Ð´ÐµÐ¾Â» â Ð²Ð¸Ð´ÐµÐ¾ ÐºÑÑÐ³Ð¾Ð²Ð¾Ð³Ð¾ Ð¾ÑÐ¼Ð¾ÑÑÐ°\n"
        f"â¢ Â«Ð¿ÑÑÐµÐ²Ð¾Ð¹Â» â ÑÐ¾ÑÐ¾ Ð¿ÑÑÐµÐ²Ð¾Ð³Ð¾ Ð»Ð¸ÑÑÐ°\n"
        f"â¢ Â«Ð¿ÑÐ¸Ð±Ð¾ÑÐ½Ð°ÑÂ» â ÑÐ¾ÑÐ¾ Ð¿ÑÐ¸Ð±Ð¾ÑÐ½Ð¾Ð¹ Ð¿Ð°Ð½ÐµÐ»Ð¸\n"
        f"â¢ Â«Ð²Ð¾Ð´Ð¸ÑÐµÐ»ÑÂ» â ÑÐ¾ÑÐ¾ Ð² ÑÐ¿ÐµÑÐ¾Ð´ÐµÐ¶Ð´Ðµ",
        parse_mode="Markdown"
    )

# âââ ÐÐÐ ÐÐÐÐ¢ÐÐ ÐÐÐÐ£ÐÐÐÐ¢ÐÐ ÐÐÐÐÐ¢Ð Q âââââââââââââââââââââââââââââââââââââââââââ
async def process_schedule_image(update: Update, image_b64: str, mime: str = "image/jpeg"):
    """Ð Ð°Ð·Ð±Ð¸ÑÐ°ÐµÑ ÑÐ¾ÑÐ¾ ÑÐ¿ÑÐ°Ð²ÐºÐ¸/Ð³ÑÐ°ÑÐ¸ÐºÐ° Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ° Ð¸ ÑÐ¾Ð·Ð´Ð°ÑÑ Ð·Ð°Ð¿Ð¸ÑÐ¸ ÑÐ¼ÐµÐ½Ñ."""
    await update.message.reply_text("ð ÐÐ½Ð°Ð»Ð¸Ð·Ð¸ÑÑÑ Ð³ÑÐ°ÑÐ¸Ðº / ÑÐ¿ÑÐ°Ð²ÐºÑ Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ°...")
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
        {"type": "text", "text": SCHEDULE_PARSE_PROMPT},
    ]
    raw = await ask_claude(
        [{"role": "user", "content": content}],
        system="Ð¢Ñ â ÑÐ¸ÑÑÐµÐ¼Ð° ÑÐ°Ð·Ð±Ð¾ÑÐ° Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð². ÐÑÐ²ÐµÑÐ°Ð¹ ÑÐ¾Ð»ÑÐºÐ¾ Ð²Ð°Ð»Ð¸Ð´Ð½ÑÐ¼ JSON Ð±ÐµÐ· Ð¿Ð¾ÑÑÐ½ÐµÐ½Ð¸Ð¹."
    )

    # ÐÐ·Ð²Ð»ÐµÐºÐ°ÐµÐ¼ JSON Ð¸Ð· Ð¾ÑÐ²ÐµÑÐ°
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        await update.message.reply_text(
            f"â ï¸ ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ ÑÐ°Ð·Ð¾Ð±ÑÐ°ÑÑ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ ÐºÐ°Ðº ÑÐ°ÑÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ.\n"
            f"ÐÑÐ²ÐµÑ Ð¼Ð¾Ð´ÐµÐ»Ð¸: {raw[:500]}"
        )
        return

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        await update.message.reply_text("â ï¸ ÐÑÐ¸Ð±ÐºÐ° ÑÐ°Ð·Ð±Ð¾ÑÐ° JSON Ð¸Ð· Ð¾ÑÐ²ÐµÑÐ° Ð¼Ð¾Ð´ÐµÐ»Ð¸.")
        return

    if "error" in data or not data.get("vehicles"):
        err_msg = data.get("error", "ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ð¸ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ñ Ð² Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐµ.")
        await update.message.reply_text(
            f"â ï¸ {err_msg}\n"
            f"Ð£Ð±ÐµÐ´Ð¸ÑÐµÑÑ, ÑÑÐ¾ ÑÑÐ¾ ÑÐ¿ÑÐ°Ð²ÐºÐ° Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ° Ð¸Ð»Ð¸ Ð³ÑÐ°ÑÐ¸Ðº ÑÐ°Ð±Ð¾ÑÑ."
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
        added.append(f"ð {num}" + (f" ({customer})" if customer else "") + (f" â {driver}" if driver else ""))

    if not added:
        await update.message.reply_text("â ï¸ ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¸Ð·Ð²Ð»ÐµÑÑ Ð½Ð¾Ð¼ÐµÑÐ° Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»ÐµÐ¹ Ð¸Ð· Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ°.")
        return

    reply = f"â Ð ÑÐ¼ÐµÐ½Ñ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ {len(added)} Ð°/Ð¼:\n" + "\n".join(added)
    reply += "\n\nÐ¢ÐµÐ¿ÐµÑÑ Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÑ ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ð¿Ð¾ ÐºÐ°Ð¶Ð´Ð¾Ð¼Ñ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ.\nÐÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ /shift Ð´Ð»Ñ Ð¿ÑÐ¾ÑÐ¼Ð¾ÑÑÐ° ÑÑÐ°ÑÑÑÐ°."
    await update.message.reply_text(reply)

async def generate_vehicle_summary(update: Update, shift_id: int, vehicle_num: str):
    """Генерирует итоговую сводку по автомобилю после получения всех документов."""
    docs = get_vehicle_docs(shift_id)
    if not docs:
        return
    parts = []
    for dt in DOC_ORDER:
        if dt in docs:
            result, analysis = docs[dt]
            parts.append(f"--- {DOC_TYPES[dt]} (результат: {result}) ---\n{analysis}")
    all_analyses = "\n\n".join(parts)
    content = [{"type": "text", "text": f"Автомобиль: {vehicle_num}\n\n{all_analyses}\n\n{VEHICLE_SUMMARY_PROMPT}"}]
    summary = await ask_claude([{"role": "user", "content": content}])
    await send_long(update, f"📊 ИТОГОВАЯ СВОДКА — {vehicle_num}\n\n{summary}")


async def process_vehicle_doc(update: Update, image_b64: str | None,
                               doc_type: str, vehicle_num: str,
                               video_frames: list | None = None,
                               mime: str = "image/jpeg"):
    """ÐÐ½Ð°Ð»Ð¸Ð·Ð¸ÑÑÐµÑ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ ÑÐ°Ð¼Ð¾ÐºÐ¾Ð½ÑÑÐ¾Ð»Ñ Ð´Ð»Ñ ÐºÐ¾Ð½ÐºÑÐµÑÐ½Ð¾Ð³Ð¾ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ."""
    shift_id = get_or_create_shift(vehicle_num)
    label = DOC_TYPES[doc_type]

    await update.message.reply_text(
        f"ð ÐÐ½Ð°Ð»Ð¸Ð·Ð¸ÑÑÑ: {label}\nð ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ: {vehicle_num}"
    )

    # ÐÑÐ±Ð¸ÑÐ°ÐµÐ¼ Ð¿ÑÐ¾Ð¼Ð¿Ñ
    prompts = {
        "video": VEHICLE_INSPECTION_PROMPT,
        "waybill": WAYBILL_PROMPT,
        "dashboard": DASHBOARD_PROMPT,
        "driver": DRIVER_PROMPT,
    }
    prompt_text = prompts[doc_type]
    # ÐÐ»Ñ ÑÐ¾ÑÐ¾ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ â Ð´Ð¾Ð±Ð°Ð²Ð»ÑÐµÐ¼ Ð¤ÐÐ Ð¸Ð· ÑÐ¼ÐµÐ½Ñ
    if doc_type == "driver":
        with get_conn() as conn:
            row = conn.execute("SELECT driver FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        driver_name = row[0] if row and row[0] else ""
        if driver_name:
            prompt_text = f"Ð¤ÐÐ Ð²Ð¾Ð´Ð¸ÑÐµÐ»Ñ (Ð¸Ð· Ð¿ÑÑÐµÐ²Ð¾Ð³Ð¾ Ð»Ð¸ÑÑÐ°): {driver_name}\n\n" + prompt_text

    # Ð¤Ð¾ÑÐ¼Ð¸ÑÑÐµÐ¼ ÐºÐ¾Ð½ÑÐµÐ½Ñ Ð´Ð»Ñ Claude
    if doc_type == "video" and video_frames:
        content = []
        for i, frame_b64 in enumerate(video_frames):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}
            })
            content.append({"type": "text", "text": f"[ÐÐ°Ð´Ñ {i+1} Ð¸Ð· {len(video_frames)}]"})
        content.append({"type": "text", "text": f"ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ: {vehicle_num}\n\n{prompt_text}"})
    else:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
            {"type": "text", "text": f"ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ: {vehicle_num}\n\n{prompt_text}"},
        ]

    analysis = await ask_claude(
        [{"role": "user", "content": content}],
        system=get_system_prompt()
    )

    result = extract_result(analysis)
    save_vehicle_doc(shift_id, doc_type, result, analysis)

    result_icon = {"ok": "â", "fail": "â", "pending": "â³"}
    header = f"{result_icon.get(result, 'â³')} {label} â {vehicle_num}\n\n"
    await send_long(update, header + analysis)

    # ÐÑÐ¾Ð²ÐµÑÑÐµÐ¼, Ð²ÑÐµ Ð»Ð¸ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÑ Ð¿Ð¾Ð»ÑÑÐµÐ½Ñ Ð´Ð»Ñ ÑÑÐ¾Ð³Ð¾ Ð°Ð²ÑÐ¾
    docs = get_vehicle_docs(shift_id)
    missing = [DOC_TYPES[dt] for dt in DOC_ORDER if dt not in docs]
    if missing:
        await update.message.reply_text(
            f"ð ÐÐ»Ñ {vehicle_num} ÐµÑÑ Ð½ÑÐ¶Ð½Ñ:\n" +
            "\n".join(f"â¢ {m}" for m in missing)
        )
    else:
        all_ok = all(docs[dt][0] == "ok" for dt in DOC_ORDER)
        if all_ok:
            await update.message.reply_text(
                f"ð ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ {vehicle_num} â Ð²ÑÐµ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÑ Ð¿Ð¾Ð»ÑÑÐµÐ½Ñ Ð¸ Ð² Ð½Ð¾ÑÐ¼Ðµ!\n"
                f"â ÐÐ¾Ð¿ÑÑÐµÐ½ Ðº Ð²ÑÐµÐ·Ð´Ñ."
            )
        else:
            fails = [DOC_TYPES[dt] for dt in DOC_ORDER if docs[dt][0] == "fail"]
            await update.message.reply_text(
                f"â ï¸ ÐÐ²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ {vehicle_num} â Ð²ÑÐµ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÑ Ð¿Ð¾Ð»ÑÑÐµÐ½Ñ.\n"
                f"â ÐÑÑÑ Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ñ Ð¿Ð¾: {', '.join(fails)}"
            )

        # Генерируем итоговую сводку по автомобилю
        await generate_vehicle_summary(update, shift_id, vehicle_num)

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


# âââ ÐÐÐÐÐÐ§ÐÐÐÐ ÐÐÐÐ ÐÐ ÐÐÐÐÐ ââââââââââââââââââââââââââââââââââââââââââââââââââ
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

        # ÐÐ¿ÑÐµÐ´ÐµÐ»ÑÐµÐ¼ Ð´Ð»Ð¸ÑÐµÐ»ÑÐ½Ð¾ÑÑÑ
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

        # ÐÐ·Ð²Ð»ÐµÐºÐ°ÐµÐ¼ ÐºÐ°Ð´ÑÑ
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
            logger.error("ffmpeg not found â Ð½Ðµ ÑÑÑÐ°Ð½Ð¾Ð²Ð»ÐµÐ½ Ð² Railway?")
            return []
        except Exception as e:
            logger.error(f"ffmpeg exception: {e}")
            return []

        for f in sorted(Path(tmp_dir).glob("frame_*.jpg"))[:max_frames]:
            frames.append(base64.b64encode(f.read_bytes()).decode())

    finally:
        tmp_dir_obj.cleanup()

    return frames

# âââ ÐÐÐ ÐÐÐÐ¢Ð§ÐÐÐ Ð¡ÐÐÐÐ©ÐÐÐÐ ââââââââââââââââââââââââââââââââââââââââââââââââââââ
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)
    user_text = update.message.text

    # ââ ÐÐµÑÐµÑÐ²Ð°Ñ ÑÐµÐµÑÑÑÐ° Ð¢Ð°ÑÐ°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ð¸ âââââââââââââââââââââââââââââââââââââ
    if detect_registry(user_text):
        today = _msk_today()
        vehicles = parse_registry(user_text)
        if vehicles:
            save_registry(user_text, vehicles, today)
            # ÐÐ²ÑÐ¾Ð¼Ð°ÑÐ¸ÑÐµÑÐºÐ¸ Ð´Ð¾Ð±Ð°Ð²Ð¸ÑÑ ÑÐ°Ð±Ð¾ÑÐ¸Ðµ Ð°Ð²ÑÐ¾ Ð² ÑÐ¼ÐµÐ½Ñ
            for num, info in vehicles.items():
                if info["status"] == "working":
                    get_or_create_shift(num)
            working = [
                f"ð *{num}*: {info['note'][:70]}"
                for num, info in vehicles.items() if info["status"] == "working"
            ]
            off_list = [
                f"ð¿ï¸ {num}: Ð²ÑÑÐ¾Ð´Ð½Ð¾Ð¹"
                for num, info in vehicles.items() if info["status"] == "off"
            ]
            lines = [f"â *Ð ÐµÐµÑÑÑ Ð¿ÑÐ¸Ð½ÑÑ* ({today.strftime('%d.%m.%Y')})\n"]
            if working:
                lines.append(f"*Ð Ð°Ð±Ð¾ÑÐ°ÑÑ ({len(working)}):*")
                lines.extend(working)
            if off_list:
                lines.append(f"\n*ÐÑÑÐ¾Ð´Ð½ÑÐµ:*")
                lines.extend(off_list)
            unknown = TATAVTO_VEHICLES - set(vehicles.keys())
            if unknown:
                lines.append(f"\nâ ï¸ ÐÐµ Ð½Ð°Ð¹Ð´ÐµÐ½Ñ Ð² ÑÐµÐµÑÑÑÐµ: {', '.join(sorted(unknown))}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return
    # ââ ÐÐ±ÑÑÐ½ÑÐ¹ ÑÐ°Ñ Ñ Claude ââââââââââââââââââââââââââââââââââââââââââââââââââ
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

    # ÐÐ¿ÑÐµÐ´ÐµÐ»ÑÐµÐ¼ ÐºÐ¾Ð½ÑÐµÐºÑÑ: ÑÐ¿ÑÐ°Ð²ÐºÐ° Ð´Ð¸ÑÐ¿ÐµÑÑÐµÑÐ°?
    if detect_is_schedule(caption):
        await process_schedule_image(update, image_b64)
        return

    # ÐÐ¿ÑÐµÐ´ÐµÐ»ÑÐµÐ¼ ÑÐ¸Ð¿ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ°
    doc_type = detect_doc_type(caption)
    # ÐÐ¿ÑÐµÐ´ÐµÐ»ÑÐµÐ¼ Ð½Ð¾Ð¼ÐµÑ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ Ð¸Ð· Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¸ Ð¸Ð»Ð¸ Ð¸Ð· agent_state
    vehicle_num = detect_vehicle_num(caption) or agent_state.get("active_vehicle")

    if doc_type and vehicle_num:
        await process_vehicle_doc(update, image_b64, doc_type, vehicle_num)
        return

    if doc_type and not vehicle_num:
        await update.message.reply_text(
            f"ð ÐÐ»Ñ ÐºÐ°ÐºÐ¾Ð³Ð¾ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ ÑÑÐ¾Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ?\n"
            f"Ð£ÐºÐ°Ð¶Ð¸ÑÐµ Ð½Ð¾Ð¼ÐµÑ Ð² Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¸ Ð¸Ð»Ð¸ Ð¸ÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ /set [Ð½Ð¾Ð¼ÐµÑ]."
        )
        return

    # ÐÐµÑ ÐºÐ¾Ð½ÑÐµÐºÑÑÐ° â ÑÑÐ°Ð½Ð´Ð°ÑÑÐ½ÑÐ¹ Ð°Ð½Ð°Ð»Ð¸Ð· ÑÐ¾ÑÐ¾
    prompt_text = caption or "Ð§ÑÐ¾ Ð½Ð° ÑÐ¾ÑÐ¾? ÐÐ¿Ð¸ÑÐ¸ Ð¿Ð¾Ð´ÑÐ¾Ð±Ð½Ð¾."
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
        {"type": "text", "text": prompt_text},
    ]
    messages = build_messages("user", content)
    save_history("user", f"[Ð¤Ð¾ÑÐ¾] {caption}")
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

        # ÐÑÐ¾Ð²ÐµÑÑÐµÐ¼ ÐºÐ¾Ð½ÑÐµÐºÑÑ Ð°Ð³ÐµÐ½ÑÐ° Q
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
                "ð ÐÐ»Ñ ÐºÐ°ÐºÐ¾Ð³Ð¾ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ ÑÑÐ¾Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ?\n"
                "Ð£ÐºÐ°Ð¶Ð¸ÑÐµ Ð½Ð¾Ð¼ÐµÑ Ð² Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¸ Ð¸Ð»Ð¸ Ð¸ÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ /set [Ð½Ð¾Ð¼ÐµÑ]."
            )
            return

        # Ð¡ÑÐ°Ð½Ð´Ð°ÑÑÐ½ÑÐ¹ Ð°Ð½Ð°Ð»Ð¸Ð· Ð¸Ð·Ð¾Ð±ÑÐ°Ð¶ÐµÐ½Ð¸Ñ
        prompt_text = caption or "Ð§ÑÐ¾ Ð½Ð° Ð¸Ð·Ð¾Ð±ÑÐ°Ð¶ÐµÐ½Ð¸Ð¸? ÐÐ¿Ð¸ÑÐ¸ Ð¿Ð¾Ð´ÑÐ¾Ð±Ð½Ð¾."
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": detected_mime, "data": image_b64}},
            {"type": "text", "text": prompt_text},
        ]
        messages = build_messages("user", content)
        save_history("user", f"[Ð¤Ð¾ÑÐ¾-ÑÐ°Ð¹Ð»: {file_name}] {caption}")
        response = await ask_claude(messages)
        save_history("assistant", response)
        await send_long(update, response)
    else:
        # Ð¢ÐµÐºÑÑÐ¾Ð²ÑÐ¹ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ
        try:
            file_text = raw.decode("utf-8")
            file_content = f"Ð¤Ð°Ð¹Ð»: {file_name}\n\nÐ¡Ð¾Ð´ÐµÑÐ¶Ð¸Ð¼Ð¾Ðµ:\n{file_text[:8000]}"
            if len(file_text) > 8000:
                file_content += "\n\n[...ÑÐ°Ð¹Ð» Ð¾Ð±ÑÐµÐ·Ð°Ð½]"
        except UnicodeDecodeError:
            file_content = f"Ð¤Ð°Ð¹Ð»: {file_name} (Ð±Ð¸Ð½Ð°ÑÐ½ÑÐ¹, ÑÐ°Ð·Ð¼ÐµÑ: {len(raw)} Ð±Ð°Ð¹Ñ)"

        prompt_text = caption or "ÐÑÐ¾Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸ÑÑÐ¹ ÑÑÐ¾Ñ Ð´Ð¾ÐºÑÐ¼ÐµÐ½Ñ."
        user_text = f"{prompt_text}\n\n{file_content}"
        messages = build_messages("user", user_text)
        save_history("user", f"[ÐÐ¾ÐºÑÐ¼ÐµÐ½Ñ: {file_name}] {caption}")
        response = await ask_claude(messages)
        save_history("assistant", response)
        await send_long(update, response)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await deny(update)

    caption = update.message.caption or ""
    vehicle_num = detect_vehicle_num(caption) or agent_state.get("active_vehicle")

    await update.message.reply_text("ð¬ ÐÐ¾Ð»ÑÑÐ¸Ð» Ð²Ð¸Ð´ÐµÐ¾. ÐÐ·Ð²Ð»ÐµÐºÐ°Ñ ÐºÐ°Ð´ÑÑ...")
    await update.message.chat.send_action("typing")

    video = update.message.video or update.message.document
    if video is None:
        await update.message.reply_text("â ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¿Ð¾Ð»ÑÑÐ¸ÑÑ Ð²Ð¸Ð´ÐµÐ¾ÑÐ°Ð¹Ð».")
        return

    tg_file = await context.bot.get_file(video.file_id)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await tg_file.download_to_drive(tmp_path)
        frames = await extract_video_frames(tmp_path, max_frames=12)

        if not frames:
            await update.message.reply_text(
                "â ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¸Ð·Ð²Ð»ÐµÑÑ ÐºÐ°Ð´ÑÑ Ð¸Ð· Ð²Ð¸Ð´ÐµÐ¾.\n"
                "Ð£Ð±ÐµÐ´Ð¸ÑÐµÑÑ ÑÑÐ¾ ÑÐ°Ð¹Ð» Ð² ÑÐ¾ÑÐ¼Ð°ÑÐµ MP4, MOV Ð¸Ð»Ð¸ AVI."
            )
            return

        await update.message.reply_text(f"â ÐÐ·Ð²Ð»ÐµÑÐµÐ½Ð¾ ÐºÐ°Ð´ÑÐ¾Ð²: {len(frames)}. ÐÐ½Ð°Ð»Ð¸Ð·Ð¸ÑÑÑ...")

        # ÐÐ¾Ð½ÑÐµÐºÑÑ ÐÐ³ÐµÐ½ÑÐ° Q â Ð²Ð¸Ð´ÐµÐ¾ ÐºÑÑÐ³Ð¾Ð²Ð¾Ð³Ð¾ Ð¾ÑÐ¼Ð¾ÑÑÐ°
        if vehicle_num:
            await process_vehicle_doc(
                update, None, "video", vehicle_num, video_frames=frames
            )
            save_history("user", f"[ÐÐ¸Ð´ÐµÐ¾ Ð¾ÑÐ¼Ð¾ÑÑÐ°: {vehicle_num}, {len(frames)} ÐºÐ°Ð´ÑÐ¾Ð²]")
            return

        # ÐÐ¸Ð´ÐµÐ¾ Ð±ÐµÐ· Ð¿ÑÐ¸Ð²ÑÐ·ÐºÐ¸ Ðº Ð°Ð²ÑÐ¾ â ÑÑÐ°Ð½Ð´Ð°ÑÑÐ½ÑÐ¹ Ð°Ð½Ð°Ð»Ð¸Ð·
        if not vehicle_num:
            await update.message.reply_text(
                "â¹ï¸ ÐÐ¾Ð¼ÐµÑ Ð°Ð²ÑÐ¾Ð¼Ð¾Ð±Ð¸Ð»Ñ Ð½Ðµ ÑÐºÐ°Ð·Ð°Ð½ Ð² Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¸ Ð¸ Ð½Ðµ Ð·Ð°Ð´Ð°Ð½ ÑÐµÑÐµÐ· /set.\n"
                "ÐÑÐ¿Ð¾Ð»Ð½ÑÑ Ð¾Ð±ÑÐ¸Ð¹ Ð°Ð½Ð°Ð»Ð¸Ð· Ð²Ð¸Ð´ÐµÐ¾..."
            )

        user_prompt = caption if caption else VEHICLE_INSPECTION_PROMPT
        content = []
        for i, frame_b64 in enumerate(frames):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}
            })
            content.append({"type": "text", "text": f"[ÐÐ°Ð´Ñ {i+1} Ð¸Ð· {len(frames)}]"})
        content.append({"type": "text", "text": user_prompt})

        messages = build_messages("user", content)
        save_history("user", f"[ÐÐ¸Ð´ÐµÐ¾, {len(frames)} ÐºÐ°Ð´ÑÐ¾Ð²] {caption}")
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
        "ð¤ ÐÐ¾Ð»Ð¾ÑÐ¾Ð²ÑÐµ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ñ Ð¿Ð¾ÐºÐ° Ð½Ðµ Ð¿Ð¾Ð´Ð´ÐµÑÐ¶Ð¸Ð²Ð°ÑÑÑÑ.\n"
        "ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐµÐºÑÑÐ¾Ð¼ Ð¸Ð»Ð¸ Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ¾Ð¼."
    )

# âââ ÐÐÐÐ£Ð¡Ð âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
async def _post_init(app: Application) -> None:
    """Start the APScheduler inside the running event loop."""
    setup_scheduler(app)


def main():
    init_db()
    logger.info(f"ÐÐ°Ð¿ÑÑÐº ÐÐ³ÐµÐ½ÑÐ° Q | Ð¼Ð¾Ð´ÐµÐ»Ñ: {MODEL} | owner_id: {OWNER_ID}")

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # ÐÑÐ½Ð¾Ð²Ð½ÑÐµ ÐºÐ¾Ð¼Ð°Ð½Ð´Ñ
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("memory",     cmd_memory))
    app.add_handler(CommandHandler("checkpoint", cmd_checkpoint))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("savelife",   cmd_checkpoint))

    # ÐÐ¾Ð¼Ð°Ð½Ð´Ñ ÐÐ³ÐµÐ½ÑÐ° Q
    app.add_handler(CommandHandler("shift",      cmd_shift))
    app.add_handler(CommandHandler("report",     cmd_report))
    app.add_handler(CommandHandler("newshift",   cmd_newshift))
    app.add_handler(CommandHandler("set",        cmd_set))

    # Ð¡Ð¾Ð¾Ð±ÑÐµÐ½Ð¸Ñ
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO,          handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO,          handle_video))
    app.add_handler(MessageHandler(filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL,   handle_document))
    app.add_handler(MessageHandler(filters.VOICE,          handle_voice))

    logger.info("ÐÐ³ÐµÐ½Ñ Q Ð·Ð°Ð¿ÑÑÐµÐ½ Ð¸ Ð¾Ð¶Ð¸Ð´Ð°ÐµÑ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ð¹...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
