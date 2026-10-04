import os
import asyncio
import secrets
import string
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI
import uvicorn


# ============================================================
# NASTROYKA BOT — FULL VERSION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "nastroyka.db").strip() or "nastroyka.db"
SUPPORT = os.getenv("SUPPORT", "@ruzvix").strip() or "@ruzvix"

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("nastroyka")

bot: Optional[Bot] = None
if BOT_TOKEN:
    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

dp = Dispatcher()
app = FastAPI(title="Nastroyka Bot")


# ============================================================
# FSM
# ============================================================

class Form(StatesGroup):
    brand = State()
    model = State()
    package = State()
    channel = State()
    broadcast = State()
    add_ref = State()
    ban = State()
    text = State()
    code = State()
    search_user = State()
    setting = State()


# ============================================================
# SQLITE COMPATIBILITY HELPERS
# aiosqlite 0.21 has execute(), but not Connection.execute_fetchone()
# ============================================================

async def fetchone(connection, query, params=()):
    async with connection.execute(query, params) as cursor:
        return await cursor.fetchone()


async def fetchall(connection, query, params=()):
    async with connection.execute(query, params) as cursor:
        return await cursor.fetchall()


async def db():
    c = await aiosqlite.connect(DB_PATH)
    c.row_factory = aiosqlite.Row
    await c.execute("PRAGMA journal_mode=WAL")
    await c.execute("PRAGMA foreign_keys=ON")
    return c


async def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# DATABASE
# ============================================================

async def init_db():
    c = await db()
    await c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        referrals INTEGER DEFAULT 0,
        referred_by INTEGER,
        banned INTEGER DEFAULT 0,
        created_at TEXT,
        last_seen TEXT
    );

    CREATE TABLE IF NOT EXISTS brands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        emoji TEXT DEFAULT '📱',
        active INTEGER DEFAULT 1,
        sort INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        sort INTEGER DEFAULT 0,
        UNIQUE(brand_id, name)
    );

    CREATE TABLE IF NOT EXISTS packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        emoji TEXT DEFAULT '🟢',
        refs INTEGER NOT NULL DEFAULT 0,
        description TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        sort INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT UNIQUE NOT NULL,
        title TEXT,
        link TEXT,
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        brand TEXT,
        model TEXT,
        package TEXT,
        refs_used INTEGER DEFAULT 0,
        used INTEGER DEFAULT 0,
        created_at TEXT,
        used_at TEXT
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER,
        invitee_id INTEGER UNIQUE,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS user_selections (
        user_id INTEGER PRIMARY KEY,
        model_id INTEGER,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        action TEXT,
        target TEXT,
        created_at TEXT
    );
    """)

    # Safe migrations for an already-created database.
    migrations = [
        ("packages", "description", "TEXT DEFAULT ''"),
        ("channels", "link", "TEXT"),
    ]
    for table, column, definition in migrations:
        try:
            await c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except Exception:
            pass

    defaults = {
        "start_text": (
            "🔥 <b>NASTROYKA BOT</b>\n\n"
            "🎮 Free Fire uchun professional telefon sozlamalari.\n\n"
            "📱 Telefoningiz brendini tanlang:"
        ),
        "support": SUPPORT,
        "ref_enabled": "1",
        "welcome_footer": "⚡ Tez • Qulay • Tartibli",
    }
    for key, value in defaults.items():
        await c.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            (key, value)
        )

    # Default packages. Existing rows are preserved.
    packages = [
        ("BASE", "🟢", 1, "Boshlang‘ich paket", 1),
        ("PREMIUM", "🟡", 2, "Kengaytirilgan paket", 2),
        ("VIP", "🔴", 3, "Eng yuqori paket", 3),
    ]
    for name, emoji, refs, description, sort in packages:
        await c.execute(
            """
            INSERT OR IGNORE INTO packages(name,emoji,refs,description,sort)
            VALUES(?,?,?,?,?)
            """,
            (name, emoji, refs, description, sort)
        )

    # Full starter phone catalogue. Admin can add more later.
    brand_data = [
        ("iPhone", "🍎", 1),
        ("Samsung", "🟦", 2),
        ("Xiaomi", "🔵", 3),
        ("Redmi", "🔴", 4),
        ("POCO", "🟡", 5),
        ("Realme", "🟢", 6),
        ("Infinix", "⚫", 7),
        ("Honor", "🔷", 8),
        ("Tecno", "🔷", 9),
        ("OPPO", "🟢", 10),
        ("Vivo", "🔵", 11),
    ]
    for name, emoji, sort in brand_data:
        await c.execute(
            "INSERT OR IGNORE INTO brands(name,emoji,sort) VALUES(?,?,?)",
            (name, emoji, sort)
        )

    model_data = {
        "iPhone": [
            "iPhone 11", "iPhone 11 Pro", "iPhone 11 Pro Max",
            "iPhone 12", "iPhone 12 Pro", "iPhone 12 Pro Max",
            "iPhone 13", "iPhone 13 Pro", "iPhone 13 Pro Max",
            "iPhone 14", "iPhone 14 Pro", "iPhone 14 Pro Max",
            "iPhone 15", "iPhone 15 Pro", "iPhone 15 Pro Max",
            "iPhone 16", "iPhone 16 Pro", "iPhone 16 Pro Max",
            "iPhone 17", "iPhone 17 Pro", "iPhone 17 Pro Max",
        ],
        "Samsung": [
            "Galaxy A15", "Galaxy A25", "Galaxy A35", "Galaxy A55",
            "Galaxy S21", "Galaxy S21 Ultra", "Galaxy S22", "Galaxy S22 Ultra",
            "Galaxy S23", "Galaxy S23 Ultra", "Galaxy S24", "Galaxy S24 Ultra",
            "Galaxy S25", "Galaxy S25 Ultra", "Galaxy S26", "Galaxy S26 Ultra",
            "Galaxy Note 20", "Galaxy Z Flip 5", "Galaxy Z Fold 5",
            "Galaxy Z Flip 6", "Galaxy Z Fold 6",
        ],
        "Xiaomi": [
            "Xiaomi 12", "Xiaomi 12 Pro", "Xiaomi 13", "Xiaomi 13 Pro",
            "Xiaomi 14", "Xiaomi 14 Pro", "Xiaomi 15", "Xiaomi 15 Pro",
            "Xiaomi 15 Ultra", "Xiaomi 16", "Xiaomi 16 Pro",
        ],
        "Redmi": [
            "Redmi 10", "Redmi 10C", "Redmi 12", "Redmi 13",
            "Redmi 14C", "Redmi Note 10", "Redmi Note 11", "Redmi Note 12",
            "Redmi Note 13", "Redmi Note 14", "Redmi Note 15",
            "Redmi Note 14 Pro", "Redmi Note 15 Pro",
        ],
        "POCO": [
            "POCO X3", "POCO X3 Pro", "POCO X4 Pro", "POCO X5 Pro",
            "POCO X6", "POCO X6 Pro", "POCO X7", "POCO X7 Pro",
            "POCO F4", "POCO F5", "POCO F6", "POCO F7",
            "POCO M5", "POCO M6",
        ],
        "Realme": [
            "Realme 8", "Realme 9", "Realme 10", "Realme 11",
            "Realme 12", "Realme 13", "Realme 14",
            "Realme C35", "Realme C55", "Realme C67", "Realme C75",
            "Realme GT Neo 5", "Realme GT 6", "Realme GT 7",
        ],
        "Infinix": [
            "Hot 10", "Hot 11", "Hot 12", "Hot 20", "Hot 30",
            "Hot 40", "Hot 50", "Hot 60", "Note 10", "Note 12",
            "Note 30", "Note 40", "Note 50", "GT 10 Pro", "GT 20 Pro",
        ],
        "Honor": [
            "X8", "X8a", "X8b", "X9", "X9a", "X9b", "X9c",
            "90", "90 Lite", "200", "200 Pro", "400", "400 Pro",
            "Magic 5 Pro", "Magic 6 Pro", "Magic 7 Pro",
        ],
        "Tecno": [
            "Spark 8", "Spark 9", "Spark 10", "Spark 20", "Spark 30", "Spark 40",
            "Camon 19", "Camon 20", "Camon 30", "Camon 40",
            "Pova 4", "Pova 5", "Pova 6", "Pova 7",
        ],
        "OPPO": [
            "A16", "A17", "A18", "A38", "A58", "A78", "A98",
            "Reno 7", "Reno 8", "Reno 10", "Reno 11", "Reno 12", "Reno 13",
            "Find X5 Pro", "Find X6 Pro", "Find X7 Ultra",
        ],
        "Vivo": [
            "Y20", "Y21", "Y22", "Y27", "Y36", "Y38", "Y50",
            "V23", "V25", "V27", "V29", "V30", "V40",
            "X80", "X90", "X100", "X200",
        ],
    }

    for brand_name, models in model_data.items():
        row = await fetchone(c, "SELECT id FROM brands WHERE name=?", (brand_name,))
        if not row:
            continue
        brand_id = row[0]
        for sort, model_name in enumerate(models, 1):
            await c.execute(
                """
                INSERT OR IGNORE INTO models(brand_id,name,sort)
                VALUES(?,?,?)
                """,
                (brand_id, model_name, sort)
            )

    await c.commit()
    await c.close()


# ============================================================
# SETTINGS / COMMON
# ============================================================

async def get_setting(key, default=""):
    c = await db()
    row = await fetchone(c, "SELECT value FROM settings WHERE key=?", (key,))
    await c.close()
    return row[0] if row else default


async def set_setting(key, value):
    c = await db()
    await c.execute(
        """
        INSERT INTO settings(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value)
    )
    await c.commit()
    await c.close()


async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def audit(admin_id, action, target=""):
    c = await db()
    await c.execute(
        "INSERT INTO audit(admin_id,action,target,created_at) VALUES(?,?,?,?)",
        (admin_id, action, target, await now_iso())
    )
    await c.commit()
    await c.close()


async def user_banned(user_id: int) -> bool:
    c = await db()
    row = await fetchone(c, "SELECT banned FROM users WHERE id=?", (user_id,))
    await c.close()
    return bool(row and row[0])


async def ensure_user(user, referrer: Optional[int] = None):
    c = await db()
    existing = await fetchone(c, "SELECT id,referred_by FROM users WHERE id=?", (user.id,))
    now = await now_iso()

    if not existing:
        if referrer == user.id:
            referrer = None
        await c.execute(
            """
            INSERT INTO users(id,username,first_name,referrals,referred_by,banned,created_at,last_seen)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (user.id, user.username, user.first_name or "", 0, referrer, 0, now, now)
        )
    else:
        await c.execute(
            "UPDATE users SET username=?,first_name=?,last_seen=? WHERE id=?",
            (user.username, user.first_name or "", now, user.id)
        )
    await c.commit()
    await c.close()


async def channels_ok(user_id: int) -> bool:
    if not bot:
        return False
    c = await db()
    rows = await fetchall(c, "SELECT chat_id FROM channels WHERE active=1")
    await c.close()
    if not rows:
        return True
    for row in rows:
        try:
            member = await bot.get_chat_member(row[0], user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception:
            return False
    return True


async def finalize_referral(user_id: int):
    c = await db()
    row = await fetchone(c, "SELECT referred_by FROM users WHERE id=?", (user_id,))
    if not row or not row[0]:
        await c.close()
        return None

    inviter_id = int(row[0])
    if inviter_id == user_id:
        await c.close()
        return None

    already = await fetchone(c, "SELECT id FROM referrals WHERE invitee_id=?", (user_id,))
    if already:
        await c.close()
        return None

    inviter = await fetchone(c, "SELECT id FROM users WHERE id=? AND banned=0", (inviter_id,))
    if not inviter:
        await c.close()
        return None

    await c.execute(
        "INSERT INTO referrals(inviter_id,invitee_id,created_at) VALUES(?,?,?)",
        (inviter_id, user_id, await now_iso())
    )
    await c.execute("UPDATE users SET referrals=referrals+1 WHERE id=?", (inviter_id,))
    await c.commit()
    await c.close()
    return inviter_id


async def notify_referrer(inviter_id):
    if not bot:
        return
    try:
        await bot.send_message(
            inviter_id,
            "🎉 <b>Yangi referral!</b>\n\n"
            "Sizga <b>+1 referral</b> qo‘shildi."
        )
    except Exception:
        pass


async def complete_subscription(user_id: int):
    if not await channels_ok(user_id):
        return False
    inviter = await finalize_referral(user_id)
    if inviter:
        await notify_referrer(inviter)
    return True


# ============================================================
# KEYBOARDS — USER
# ============================================================

async def channel_kb():
    c = await db()
    rows = await fetchall(c, "SELECT chat_id,title,link FROM channels WHERE active=1 ORDER BY id")
    await c.close()
    builder = InlineKeyboardBuilder()
    for row in rows:
        link = row[2]
        if not link and str(row[0]).startswith("@"):
            link = f"https://t.me/{str(row[0])[1:]}"
        if link:
            builder.button(text=f"📢 {row[1] or row[0]}", url=link)
    builder.button(text="✅ Obunani tekshirish", callback_data="checksub")
    builder.adjust(1)
    return builder.as_markup()


async def brands_kb():
    c = await db()
    rows = await fetchall(
        c,
        "SELECT id,name,emoji FROM brands WHERE active=1 ORDER BY sort,id"
    )
    await c.close()
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(text=f"{row[2]} {row[1]}", callback_data=f"brand:{row[0]}")
    builder.button(text="🎁 Referral markazi", callback_data="myref")
    builder.button(text="⚙️ Maxsus sozlama", callback_data="custom")
    builder.button(text="🆘 Yordam", callback_data="help")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


async def models_kb(brand_id):
    c = await db()
    rows = await fetchall(
        c,
        "SELECT id,name FROM models WHERE brand_id=? AND active=1 ORDER BY sort,id",
        (brand_id,)
    )
    brand = await fetchone(c, "SELECT name,emoji FROM brands WHERE id=?", (brand_id,))
    await c.close()
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(text=f"📱 {row[1]}", callback_data=f"model:{row[0]}")
    builder.button(text="⬅️ Brendlar", callback_data="home")
    builder.adjust(2)
    return builder.as_markup(), brand


async def packages_kb():
    c = await db()
    rows = await fetchall(
        c,
        "SELECT id,name,emoji,refs,description FROM packages WHERE active=1 ORDER BY sort,id"
    )
    await c.close()
    ref_enabled = await get_setting("ref_enabled", "1") == "1"
    builder = InlineKeyboardBuilder()
    for row in rows:
        refs_text = f"{row[3]} referral" if ref_enabled and row[3] > 0 else "darhol"
        builder.button(text=f"{row[2]} {row[1]} • {refs_text}", callback_data=f"pkg:{row[0]}")
    builder.button(text="⬅️ Modellar", callback_data="backmodel")
    builder.adjust(1)
    return builder.as_markup()


async def home_text():
    return (
        await get_setting("start_text")
        + "\n\n"
        + await get_setting("welcome_footer", "⚡ Tez • Qulay • Tartibli")
    )


async def admin_kb():
    builder = InlineKeyboardBuilder()
    buttons = [
        ("📊 Statistika", "astats"), ("👥 Users", "users"),
        ("📱 Telefonlar", "phones"), ("💎 Paketlar", "packs"),
        ("📢 Majburiy obuna", "channels"), ("📣 Reklama", "broadcast"),
        ("🔐 Kodlar", "codes"), ("🎁 Referral", "refs"),
        ("🚫 Ban / Unban", "banmenu"), ("📝 Matnlar", "texts"),
        ("⚙️ Sozlamalar", "settings"), ("🧾 Audit", "auditlog"),
    ]
    for text, callback in buttons:
        builder.button(text=text, callback_data=callback)
    builder.adjust(2)
    return builder.as_markup()


async def admin_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Admin", callback_data="adminhome")
    ]])


async def user_back_kb(callback_data="home"):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Orqaga", callback_data=callback_data)
    ]])


# ============================================================
# /START + SUBSCRIPTION
# ============================================================

@dp.message(CommandStart())
async def start(message: Message):
    user = message.from_user
    if await user_banned(user.id):
        return await message.answer("🚫 <b>Siz botdan foydalanish huquqidan mahrumsiz.</b>")

    referrer = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].isdigit():
        referrer = int(parts[1])

    await ensure_user(user, referrer)

    if not await channels_ok(user.id):
        return await message.answer(
            "🔒 <b>Davom etish uchun majburiy kanallarga obuna bo‘ling.</b>\n\n"
            "Obuna bo‘lgach, pastdagi tugmani bosing.",
            reply_markup=await channel_kb()
        )

    await complete_subscription(user.id)
    await message.answer(await home_text(), reply_markup=await brands_kb())


@dp.callback_query(F.data == "checksub")
async def checksub(callback: CallbackQuery):
    if await user_banned(callback.from_user.id):
        return await callback.answer("🚫 Siz bloklangansiz.", show_alert=True)
    if not await channels_ok(callback.from_user.id):
        return await callback.answer("❌ Hali barcha kanallarga obuna bo‘lmagansiz.", show_alert=True)
    await complete_subscription(callback.from_user.id)
    await callback.message.edit_text(await home_text(), reply_markup=await brands_kb())
    await callback.answer("✅ Obuna tasdiqlandi")


# ============================================================
# USER NAVIGATION
# ============================================================

@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    if await user_banned(callback.from_user.id):
        return await callback.answer("🚫 Bloklangan.", show_alert=True)
    await callback.message.edit_text(await home_text(), reply_markup=await brands_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("brand:"))
async def brand(callback: CallbackQuery):
    if not await channels_ok(callback.from_user.id):
        return await callback.answer("🔒 Avval majburiy obunani bajaring.", show_alert=True)
    try:
        brand_id = int(callback.data.split(":", 1)[1])
    except Exception:
        return await callback.answer("❌ Noto‘g‘ri brend.", show_alert=True)

    c = await db()
    row = await fetchone(c, "SELECT name,emoji FROM brands WHERE id=? AND active=1", (brand_id,))
    count = await fetchone(c, "SELECT COUNT(*) FROM models WHERE brand_id=? AND active=1", (brand_id,))
    await c.close()
    if not row:
        return await callback.answer("Brend topilmadi.", show_alert=True)

    kb, _ = await models_kb(brand_id)
    text = (
        f"{row[1]} <b>{row[0]}</b>\n\n"
        f"📱 <b>{count[0] if count else 0} ta model</b> mavjud.\n\n"
        "Modelingizni tanlang:"
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("model:"))
async def model(callback: CallbackQuery):
    try:
        model_id = int(callback.data.split(":", 1)[1])
    except Exception:
        return await callback.answer("❌ Noto‘g‘ri model.", show_alert=True)

    c = await db()
    row = await fetchone(
        c,
        """
        SELECT m.id,m.name,b.name,b.emoji
        FROM models m JOIN brands b ON b.id=m.brand_id
        WHERE m.id=? AND m.active=1 AND b.active=1
        """,
        (model_id,)
    )
    await c.close()
    if not row:
        return await callback.answer("Model topilmadi.", show_alert=True)

    c = await db()
    await c.execute(
        """
        INSERT INTO user_selections(user_id,model_id,updated_at) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET model_id=excluded.model_id,updated_at=excluded.updated_at
        """,
        (callback.from_user.id, model_id, await now_iso())
    )
    await c.commit()
    await c.close()

    await callback.message.edit_text(
        f"{row[3]} <b>{row[2]} {row[1]}</b>\n\n"
        "⚙️ Sozlama darajasini tanlang:",
        reply_markup=await packages_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "backmodel")
async def backmodel(callback: CallbackQuery):
    c = await db()
    row = await fetchone(c, "SELECT model_id FROM user_selections WHERE user_id=?", (callback.from_user.id,))
    brand_row = None
    if row:
        brand_row = await fetchone(
            c,
            "SELECT b.id,b.name,b.emoji FROM models m JOIN brands b ON b.id=m.brand_id WHERE m.id=?",
            (row[0],)
        )
    await c.close()
    if not brand_row:
        return await callback.message.edit_text(await home_text(), reply_markup=await brands_kb())
    kb, _ = await models_kb(brand_row[0])
    await callback.message.edit_text(
        f"{brand_row[2]} <b>{brand_row[1]}</b>\n\nModelingizni tanlang:",
        reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("pkg:"))
async def package(callback: CallbackQuery):
    try:
        package_id = int(callback.data.split(":", 1)[1])
    except Exception:
        return await callback.answer("❌ Paket noto‘g‘ri.", show_alert=True)

    c = await db()
    selection = await fetchone(c, "SELECT model_id FROM user_selections WHERE user_id=?", (callback.from_user.id,))
    if not selection:
        await c.close()
        return await callback.answer("Modelni qayta tanlang.", show_alert=True)

    pkg = await fetchone(
        c,
        "SELECT id,name,emoji,refs,description FROM packages WHERE id=? AND active=1",
        (package_id,)
    )
    model_row = await fetchone(
        c,
        """
        SELECT m.name,b.name,b.emoji
        FROM models m JOIN brands b ON b.id=m.brand_id
        WHERE m.id=? AND m.active=1 AND b.active=1
        """,
        (selection[0],)
    )
    user_row = await fetchone(c, "SELECT referrals FROM users WHERE id=?", (callback.from_user.id,))
    await c.close()

    if not pkg or not model_row or not user_row:
        return await callback.answer("Ma’lumot topilmadi.", show_alert=True)

    ref_enabled = await get_setting("ref_enabled", "1") == "1"
    required = pkg[3] if ref_enabled else 0
    current = int(user_row[0])

    if current < required:
        need = required - current
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start={callback.from_user.id}"
        share = f"https://t.me/share/url?url={link}&text=NASTROYKA%20BOT%20ga%20qo%27shiling"
        await callback.message.edit_text(
            "🔐 <b>PAKETGA KIRISH UCHUN REFERRAL KERAK</b>\n\n"
            f"📱 Qurilma: <b>{model_row[1]} {model_row[0]}</b>\n"
            f"{pkg[2]} Paket: <b>{pkg[1]}</b>\n\n"
            f"👥 Sizda: <b>{current}</b>\n"
            f"🎯 Kerak: <b>{required}</b>\n"
            f"➕ Yetishmayapti: <b>{need}</b>\n\n"
            "🔗 Havolangizni do‘stlaringizga yuboring.\n"
            "Referral yangi foydalanuvchi kirib, majburiy obunani bajarganidan keyin hisoblanadi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📤 Do‘stlarga yuborish", url=share)],
                [InlineKeyboardButton(text="🔄 Referralni tekshirish", callback_data="myref")],
                [InlineKeyboardButton(text="⬅️ Paketlar", callback_data="backmodel")],
            ])
        )
        return await callback.answer()

    code = "RVX-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    c = await db()
    result = await c.execute(
        "UPDATE users SET referrals=referrals-? WHERE id=? AND referrals>=?",
        (required, callback.from_user.id, required)
    )
    if result.rowcount != 1:
        await c.close()
        return await callback.answer("❌ Referral yetarli emas.", show_alert=True)

    await c.execute(
        """
        INSERT INTO codes(code,user_id,brand,model,package,refs_used,used,created_at)
        VALUES(?,?,?,?,?,?,0,?)
        """,
        (code, callback.from_user.id, model_row[1], model_row[0], pkg[1], required, await now_iso())
    )
    await c.commit()
    await c.close()

    support = await get_setting("support", SUPPORT)
    await callback.message.edit_text(
        "🎉 <b>SO‘ROV TASDIQLANDI!</b>\n\n"
        f"📱 Telefon: <b>{model_row[1]} {model_row[0]}</b>\n"
        f"{pkg[2]} Paket: <b>{pkg[1]}</b>\n"
        f"👥 Sarflandi: <b>{required} referral</b>\n\n"
        "🔐 <b>MAXFIY KOD</b>\n\n"
        f"<code>{code}</code>\n\n"
        "⚠️ Kod bir martalik. Boshqalarga bermang.\n\n"
        f"📩 Support: <b>{support}</b>\n\n"
        "Kodni supportga yuboring. Admin tekshiradi.",
        reply_markup=await user_back_kb("home")
    )
    await callback.answer("✅ Kod yaratildi")


@dp.callback_query(F.data == "myref")
async def myref(callback: CallbackQuery):
    c = await db()
    row = await fetchone(c, "SELECT referrals FROM users WHERE id=?", (callback.from_user.id,))
    await c.close()
    refs = int(row[0]) if row else 0
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={callback.from_user.id}"
    share = f"https://t.me/share/url?url={link}&text=NASTROYKA%20BOT"
    await callback.message.edit_text(
        "🎁 <b>REFERRAL MARKAZI</b>\n\n"
        f"👥 Sizning referral: <b>{refs}</b>\n\n"
        "🔗 <b>Sizning shaxsiy havolangiz:</b>\n"
        f"<code>{link}</code>\n\n"
        "⚡ Referral faqat yangi foydalanuvchi majburiy obunani bajargandan keyin hisoblanadi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Ulashish", url=share)],
            [InlineKeyboardButton(text="⬅️ Bosh sahifa", callback_data="home")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "help")
async def help_button(callback: CallbackQuery):
    support = await get_setting("support", SUPPORT)
    await callback.message.edit_text(
        "🆘 <b>YORDAM</b>\n\n"
        "1️⃣ Telefon brendini tanlang\n"
        "2️⃣ Modelni tanlang\n"
        "3️⃣ BASE / PREMIUM / VIP paketini tanlang\n"
        "4️⃣ Kerakli referralni yig‘ing\n"
        "5️⃣ Maxfiy kodni oling\n"
        "6️⃣ Kodni supportga yuboring\n\n"
        f"📩 Support: <b>{support}</b>",
        reply_markup=await user_back_kb("home")
    )
    await callback.answer()


@dp.callback_query(F.data == "custom")
async def custom(callback: CallbackQuery):
    support = await get_setting("support", SUPPORT)
    await callback.message.edit_text(
        "⚙️ <b>MAXSUS SOZLAMA</b>\n\n"
        "Telefon yoki modelingiz ro‘yxatda bo‘lmasa, supportga yozing.\n\n"
        f"📩 Support: <b>{support}</b>",
        reply_markup=await user_back_kb("home")
    )
    await callback.answer()


# ============================================================
# ADMIN HELPERS
# ============================================================

async def deny(callback: CallbackQuery):
    return await callback.answer("⛔ Ruxsat yo‘q.", show_alert=True)


@dp.message(Command("admin"))
async def admin(message: Message):
    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Ruxsat yo‘q.")
    await message.answer(
        "👑 <b>NASTROYKA CONTROL CENTER</b>\n\n"
        "Barcha boshqaruv shu panelda.",
        reply_markup=await admin_kb()
    )


@dp.callback_query(F.data == "adminhome")
async def admin_home(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await deny(callback)
    await callback.message.edit_text(
        "👑 <b>NASTROYKA CONTROL CENTER</b>\n\nBoshqaruv paneli:",
        reply_markup=await admin_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "astats")
async def admin_stats(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await deny(callback)
    c = await db()
    users = await fetchone(c, "SELECT COUNT(*) FROM users")
    active = await fetchone(c, "SELECT COUNT(*) FROM users WHERE banned=0")
    refs = await fetchone(c, "SELECT COALESCE(SUM(referrals),0) FROM users")
    codes = await fetchone(c, "SELECT COUNT(*) FROM codes")
    used = await fetchone(c, "SELECT COUNT(*) FROM codes WHERE used=1")
    brands = await fetchone(c, "SELECT COUNT(*) FROM brands WHERE active=1")
    models = await fetchone(c, "SELECT COUNT(*) FROM models WHERE active=1")
    channels = await fetchone(c, "SELECT COUNT(*) FROM channels WHERE active=1")
    await c.close()
    await callback.message.edit_text(
        "📊 <b>STATISTIKA</b>\n\n"
        f"👥 Users: <b>{users[0]}</b>\n"
        f"🟢 Active: <b>{active[0]}</b>\n"
        f"🎁 Referral qoldiq: <b>{refs[0]}</b>\n"
        f"🔐 Kodlar: <b>{codes[0]}</b>\n"
        f"✅ Ishlatilgan: <b>{used[0]}</b>\n"
        f"📱 Brendlar: <b>{brands[0]}</b>\n"
        f"📲 Modellar: <b>{models[0]}</b>\n"
        f"📢 Kanallar: <b>{channels[0]}</b>",
        reply_markup=await admin_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "users")
async def admin_users(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await deny(callback)
    c = await db()
    rows = await fetchall(
        c,
        "SELECT id,username,first_name,referrals,banned,last_seen FROM users ORDER BY last_seen DESC LIMIT 30"
    )
    await c.close()
    lines = ["👥 <b>FOYDALANUVCHILAR</b>", ""]
    for i, row in enumerate(rows, 1):
        status = "🚫" if row[4] else "🟢"
        lines.append(f"{i}. <code>{row[0]}</code> @{row[1] or '-'} | REF {row[3]} | {status}")
    lines.append("")
    lines.append("🔎 Aniq user uchun: <code>/user USER_ID</code>")
    await callback.message.edit_text("\n".join(lines), reply_markup=await admin_back_kb())
    await callback.answer()


@dp.message(Command("user"))
async def user_info(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("/user USER_ID")
    uid = int(parts[1])
    c = await db()
    row = await fetchone(c, "SELECT id,username,first_name,referrals,referred_by,banned,created_at,last_seen FROM users WHERE id=?", (uid,))
    code_count = await fetchone(c, "SELECT COUNT(*) FROM codes WHERE user_id=?", (uid,))
    await c.close()
    if not row:
        return await message.answer("❌ User topilmadi.")
    await message.answer(
        "👤 <b>USER MA’LUMOTI</b>\n\n"
        f"🆔 <code>{row[0]}</code>\n"
        f"👤 @{row[1] or '-'}\n"
        f"📝 {row[2] or '-'}\n"
        f"🎁 Referral: <b>{row[3]}</b>\n"
        f"🔗 Taklif qilgan: <code>{row[4] or '-'}</code>\n"
        f"🚦 Holat: {'🚫 BAN' if row[5] else '🟢 ACTIVE'}\n"
        f"🔐 Kodlar: <b>{code_count[0]}</b>\n"
        f"🕒 Oxirgi faollik: {row[7]}"
    )


# ============================================================
# ADMIN PHONES
# ============================================================

@dp.callback_query(F.data == "phones")
async def phones(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await deny(callback)
    c = await db()
    brands = await fetchall(c, "SELECT id,name,emoji,active FROM brands ORDER BY sort,id")
    model_count = await fetchone(c, "SELECT COUNT(*) FROM models WHERE active=1")
    await c.close()
    text = ["📱 <b>TELEFON KATALOGI</b>", ""]
    for row in brands:
        text.append(f"{row[0]}. {row[2]} {row[1]} {'🟢' if row[3] else '🔴'}")
    text += ["", f"📲 Faol modellar: <b>{model_count[0]}</b>", "", "Buyruqlar:",
             "<code>/addbrand Nomi | Emoji</code>",
             "<code>/addmodel BRAND_ID | Model</code>",
             "<code>/hidebrand ID</code>",
             "<code>/showbrand ID</code>",
             "<code>/hidemodel ID</code>",
             "<code>/showmodel ID</code>",
             "<code>/setbrand ID | Nomi | Emoji</code>"]
    await callback.message.edit_text("\n".join(text), reply_markup=await admin_back_kb())
    await callback.answer()


@dp.message(Command("addbrand"))
async def addbrand_cmd(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        data = message.text.split(maxsplit=1)[1]
        name, emoji = [x.strip() for x in data.split("|", 1)]
    except Exception:
        return await message.answer("/addbrand Nomi | Emoji")
    c = await db()
    await c.execute("INSERT OR IGNORE INTO brands(name,emoji,sort) VALUES(?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM brands))", (name, emoji))
    await c.commit(); await c.close()
    await audit(message.from_user.id, "add_brand", name)
    await message.answer("✅ Brend qo‘shildi.")


@dp.message(Command("addmodel"))
async def addmodel_cmd(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        data = message.text.split(maxsplit=1)[1]
        brand_id, name = [x.strip() for x in data.split("|", 1)]
        brand_id = int(brand_id)
    except Exception:
        return await message.answer("/addmodel BRAND_ID | Model")
    c = await db()
    brand = await fetchone(c, "SELECT id FROM brands WHERE id=?", (brand_id,))
    if not brand:
        await c.close(); return await message.answer("❌ Brand ID topilmadi.")
    await c.execute("INSERT OR IGNORE INTO models(brand_id,name,sort) VALUES(?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM models WHERE brand_id=?))", (brand_id,name,brand_id))
    await c.commit(); await c.close()
    await audit(message.from_user.id, "add_model", f"{brand_id}:{name}")
    await message.answer("✅ Model qo‘shildi.")


async def toggle_catalog(message: Message, table: str, active: int):
    if not await is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer(f"/{'hide' if not active else 'show'}{'brand' if table == 'brands' else 'model'} ID")
    item_id = int(parts[1])
    c = await db()
    await c.execute(f"UPDATE {table} SET active=? WHERE id=?", (active,item_id))
    await c.commit(); await c.close()
    await message.answer("✅ Saqlandi.")


@dp.message(Command("hidebrand"))
async def hidebrand(message: Message): await toggle_catalog(message,"brands",0)
@dp.message(Command("showbrand"))
async def showbrand(message: Message): await toggle_catalog(message,"brands",1)
@dp.message(Command("hidemodel"))
async def hidemodel(message: Message): await toggle_catalog(message,"models",0)
@dp.message(Command("showmodel"))
async def showmodel(message: Message): await toggle_catalog(message,"models",1)


@dp.message(Command("setbrand"))
async def setbrand(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        data = message.text.split(maxsplit=1)[1]
        bid, name, emoji = [x.strip() for x in data.split("|",2)]
        bid=int(bid)
    except Exception:
        return await message.answer("/setbrand ID | Nomi | Emoji")
    c=await db(); await c.execute("UPDATE brands SET name=?,emoji=? WHERE id=?",(name,emoji,bid)); await c.commit(); await c.close()
    await message.answer("✅ Brend yangilandi.")


# ============================================================
# ADMIN PACKAGES
# ============================================================

@dp.callback_query(F.data == "packs")
async def packages_admin(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    c=await db(); rows=await fetchall(c,"SELECT id,emoji,name,refs,description,active FROM packages ORDER BY sort,id"); await c.close()
    lines=["💎 <b>PAKETLAR</b>",""]
    for row in rows:
        lines.append(f"{row[0]}. {row[1]} <b>{row[2]}</b> — {row[3]} ref — {'🟢' if row[5] else '🔴'}")
        if row[4]: lines.append(f"   {row[4]}")
    lines += ["","✏️ <code>/setpack ID | Nomi | Emoji | Referral | Izoh</code>","🆕 <code>/addpack Nomi | Emoji | Referral | Izoh</code>","🗑 <code>/delpack ID</code>","🔄 <code>/showpack ID</code>"]
    await callback.message.edit_text("\n".join(lines),reply_markup=await admin_back_kb()); await callback.answer()


@dp.message(Command("setpack"))
async def setpack(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        data=message.text.split(maxsplit=1)[1]
        parts=[x.strip() for x in data.split("|",4)]
        if len(parts)<4: raise ValueError
        pid,name,emoji,refs=parts[:4]; desc=parts[4] if len(parts)>4 else ""
        pid=int(pid); refs=int(refs)
    except Exception:
        return await message.answer("/setpack ID | Nomi | Emoji | Referral | Izoh")
    c=await db(); await c.execute("UPDATE packages SET name=?,emoji=?,refs=?,description=? WHERE id=?",(name,emoji,max(0,refs),desc,pid)); await c.commit(); await c.close()
    await audit(message.from_user.id,"set_package",str(pid)); await message.answer("✅ Paket yangilandi.")


@dp.message(Command("addpack"))
async def addpack(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        data=message.text.split(maxsplit=1)[1]; name,emoji,refs,desc=[x.strip() for x in data.split("|",3)]; refs=int(refs)
    except Exception:
        return await message.answer("/addpack Nomi | Emoji | Referral | Izoh")
    c=await db(); await c.execute("INSERT INTO packages(name,emoji,refs,description,sort) VALUES(?,?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM packages))",(name,emoji,max(0,refs),desc)); await c.commit(); await c.close(); await message.answer("✅ Paket qo‘shildi.")


@dp.message(Command("delpack"))
async def delpack(message: Message):
    if not await is_admin(message.from_user.id): return
    parts=message.text.split();
    if len(parts)!=2 or not parts[1].isdigit(): return await message.answer("/delpack ID")
    c=await db(); await c.execute("UPDATE packages SET active=0 WHERE id=?",(int(parts[1]),)); await c.commit(); await c.close(); await message.answer("🗑 Paket yashirildi.")


@dp.message(Command("showpack"))
async def showpack(message: Message):
    if not await is_admin(message.from_user.id): return
    parts=message.text.split();
    if len(parts)!=2 or not parts[1].isdigit(): return await message.answer("/showpack ID")
    c=await db(); await c.execute("UPDATE packages SET active=1 WHERE id=?",(int(parts[1]),)); await c.commit(); await c.close(); await message.answer("🔄 Paket qaytarildi.")


# ============================================================
# REQUIRED CHANNELS
# ============================================================

@dp.callback_query(F.data == "channels")
async def admin_channels(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    c=await db(); rows=await fetchall(c,"SELECT id,chat_id,title,link,active FROM channels ORDER BY id"); await c.close()
    lines=["📢 <b>MAJBURIY OBUNA</b>",""]
    if not rows: lines.append("Hozircha kanal qo‘shilmagan.")
    for row in rows: lines.append(f"{row[0]}. {row[2] or row[1]} — {row[1]} {'🟢' if row[4] else '🔴'}")
    lines += ["","➕ <code>/addchannel @kanal | Nomi | https://t.me/kanal</code>","🗑 <code>/delchannel ID</code>","🔄 <code>/showchannel ID</code>","\n⚠️ Bot kanalga admin bo‘lishi kerak."]
    await callback.message.edit_text("\n".join(lines),reply_markup=await admin_back_kb()); await callback.answer()


@dp.message(Command("addchannel"))
async def addchannel(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        data=message.text.split(maxsplit=1)[1]; parts=[x.strip() for x in data.split("|",2)]; chat_id=parts[0]; title=parts[1] if len(parts)>1 and parts[1] else chat_id; link=parts[2] if len(parts)>2 else (f"https://t.me/{chat_id[1:]}" if chat_id.startswith("@") else "")
    except Exception:
        return await message.answer("/addchannel @kanal | Nomi | https://t.me/kanal")
    c=await db(); await c.execute("INSERT OR REPLACE INTO channels(chat_id,title,link,active) VALUES(?,?,?,1)",(chat_id,title,link)); await c.commit(); await c.close(); await message.answer("✅ Majburiy kanal qo‘shildi.")


@dp.message(Command("delchannel"))
async def delchannel(message: Message):
    if not await is_admin(message.from_user.id): return
    parts=message.text.split();
    if len(parts)!=2 or not parts[1].isdigit(): return await message.answer("/delchannel ID")
    c=await db(); await c.execute("UPDATE channels SET active=0 WHERE id=?",(int(parts[1]),)); await c.commit(); await c.close(); await message.answer("🗑 Kanal o‘chirildi.")


@dp.message(Command("showchannel"))
async def showchannel(message: Message):
    if not await is_admin(message.from_user.id): return
    parts=message.text.split();
    if len(parts)!=2 or not parts[1].isdigit(): return await message.answer("/showchannel ID")
    c=await db(); await c.execute("UPDATE channels SET active=1 WHERE id=?",(int(parts[1]),)); await c.commit(); await c.close(); await message.answer("🔄 Kanal qaytarildi.")


# ============================================================
# BROADCAST
# ============================================================

@dp.callback_query(F.data == "broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    await state.set_state(Form.broadcast)
    await callback.message.answer(
        "📣 <b>REKLAMA</b>\n\n"
        "Keyingi yuborgan xabaringiz bloklanmagan foydalanuvchilarga nusxa qilinadi.\n"
        "Matn, rasm, video yoki boshqa Telegram xabari bo‘lishi mumkin.\n\n"
        "Bekor qilish: <code>/cancel</code>"
    )
    await callback.answer()


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if await is_admin(message.from_user.id):
        await state.clear(); await message.answer("✅ Amal bekor qilindi.")


@dp.message(Form.broadcast)
async def do_broadcast(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id): return
    c=await db(); users=await fetchall(c,"SELECT id FROM users WHERE banned=0"); await c.close()
    success=0; failed=0
    for row in users:
        try:
            await bot.copy_message(chat_id=row[0],from_chat_id=message.chat.id,message_id=message.message_id)
            success+=1
        except Exception:
            failed+=1
        await asyncio.sleep(0.05)
    await state.clear()
    await audit(message.from_user.id,"broadcast",f"success={success},failed={failed}")
    await message.answer(f"📣 <b>YAKUNLANDI</b>\n\n✅ Yetkazildi: <b>{success}</b>\n❌ Xato: <b>{failed}</b>")


# ============================================================
# CODES
# ============================================================

@dp.callback_query(F.data == "codes")
async def admin_codes(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    c=await db(); rows=await fetchall(c,"SELECT code,user_id,brand,model,package,used,created_at FROM codes ORDER BY id DESC LIMIT 30"); await c.close()
    lines=["🔐 <b>MAXFIY KODLAR</b>",""]
    if not rows: lines.append("Hozircha kod yo‘q.")
    for row in rows:
        lines += [f"<code>{row[0]}</code> — {'✅ USED' if row[5] else '🟢 NEW'}",f"👤 {row[1]} | 📱 {row[2]} {row[3]} | 💎 {row[4]}",""]
    lines += ["🛠 Ishlatilgan qilish: <code>/usecode RVX-XXXXXXXXXX</code>"]
    await callback.message.edit_text("\n".join(lines),reply_markup=await admin_back_kb()); await callback.answer()


@dp.message(Command("usecode"))
async def usecode(message: Message):
    if not await is_admin(message.from_user.id): return
    parts=message.text.split()
    if len(parts)!=2: return await message.answer("/usecode RVX-XXXXXXXXXX")
    code=parts[1].strip().upper()
    c=await db(); row=await fetchone(c,"SELECT used FROM codes WHERE code=?",(code,))
    if not row: await c.close(); return await message.answer("❌ Kod topilmadi.")
    if row[0]: await c.close(); return await message.answer("ℹ️ Kod allaqachon ishlatilgan.")
    await c.execute("UPDATE codes SET used=1,used_at=? WHERE code=?",(await now_iso(),code)); await c.commit(); await c.close(); await audit(message.from_user.id,"use_code",code); await message.answer("✅ Kod ishlatilgan deb belgilandi.")


# ============================================================
# REFERRAL ADMIN
# ============================================================

@dp.callback_query(F.data == "refs")
async def admin_refs(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    enabled=await get_setting("ref_enabled","1")
    await callback.message.edit_text(
        "🎁 <b>REFERRAL BOSHQARUVI</b>\n\n"
        f"Holat: <b>{'🟢 Yoqilgan' if enabled=='1' else '🔴 O‘chirilgan'}</b>\n\n"
        "Userga qo‘shish/ayirish:\n"
        "<code>/ref USER_ID | +5</code>\n"
        "<code>/ref USER_ID | -2</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Referralni yoq/o‘chir", callback_data="toggle_ref")],
            [InlineKeyboardButton(text="⬅️ Admin", callback_data="adminhome")]
        ])
    ); await callback.answer()


@dp.callback_query(F.data == "toggle_ref")
async def toggle_ref(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    current=await get_setting("ref_enabled","1"); new="0" if current=="1" else "1"; await set_setting("ref_enabled",new); await audit(callback.from_user.id,"toggle_ref",new)
    await callback.message.edit_text(f"🎁 Referral: <b>{'🟢 YOQILDI' if new=='1' else '🔴 O‘CHIRILDI'}</b>",reply_markup=await admin_kb()); await callback.answer("Saqlandi")


@dp.message(Command("ref"))
async def ref_cmd(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        data=message.text.split(maxsplit=1)[1]; uid,amount=[x.strip() for x in data.split("|",1)]; uid=int(uid); amount=int(amount)
    except Exception:
        return await message.answer("/ref USER_ID | +5")
    c=await db(); result=await c.execute("UPDATE users SET referrals=MAX(0,referrals+?) WHERE id=?",(amount,uid)); await c.commit(); await c.close()
    if result.rowcount==0: return await message.answer("❌ User topilmadi.")
    await audit(message.from_user.id,"edit_referral",f"{uid}:{amount}"); await message.answer("✅ Referral o‘zgartirildi.")


# ============================================================
# BAN / UNBAN
# ============================================================

@dp.callback_query(F.data == "banmenu")
async def banmenu(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    await callback.message.edit_text(
        "🚫 <b>BAN BOSHQARUVI</b>\n\n"
        "<code>/ban USER_ID</code>\n"
        "<code>/unban USER_ID</code>",
        reply_markup=await admin_back_kb()
    ); await callback.answer()


async def set_ban(message: Message, value: int):
    if not await is_admin(message.from_user.id): return
    parts=message.text.split()
    if len(parts)!=2 or not parts[1].isdigit(): return await message.answer("/ban USER_ID" if value else "/unban USER_ID")
    uid=int(parts[1]); c=await db(); result=await c.execute("UPDATE users SET banned=? WHERE id=?",(value,uid)); await c.commit(); await c.close()
    if result.rowcount==0: return await message.answer("❌ User topilmadi.")
    await audit(message.from_user.id,"ban" if value else "unban",str(uid)); await message.answer("🚫 User bloklandi." if value else "✅ User blokdan chiqarildi.")

@dp.message(Command("ban"))
async def ban(message: Message): await set_ban(message,1)
@dp.message(Command("unban"))
async def unban(message: Message): await set_ban(message,0)


# ============================================================
# TEXTS
# ============================================================

@dp.callback_query(F.data == "texts")
async def texts(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    start=await get_setting("start_text"); support=await get_setting("support",SUPPORT)
    await callback.message.edit_text(
        "📝 <b>MATNLAR</b>\n\n"
        f"📌 Start matni:\n{start[:400]}\n\n"
        f"📩 Support: <b>{support}</b>\n\n"
        "<code>/setstart YANGI MATN</code>\n"
        "<code>/setsupport @username</code>",
        reply_markup=await admin_back_kb()
    ); await callback.answer()


@dp.message(Command("setstart"))
async def setstart(message: Message):
    if not await is_admin(message.from_user.id): return
    value=message.text.split(maxsplit=1)
    if len(value)!=2: return await message.answer("/setstart YANGI MATN")
    await set_setting("start_text",value[1]); await audit(message.from_user.id,"set_start_text"); await message.answer("✅ Start matni saqlandi.")


@dp.message(Command("setsupport"))
async def setsupport(message: Message):
    if not await is_admin(message.from_user.id): return
    value=message.text.split(maxsplit=1)
    if len(value)!=2: return await message.answer("/setsupport @username")
    await set_setting("support",value[1].strip()); await audit(message.from_user.id,"set_support",value[1].strip()); await message.answer("✅ Support saqlandi.")


# ============================================================
# SETTINGS / AUDIT
# ============================================================

@dp.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    ref=await get_setting("ref_enabled","1"); support=await get_setting("support",SUPPORT)
    await callback.message.edit_text(
        "⚙️ <b>GLOBAL SOZLAMALAR</b>\n\n"
        f"🎁 Referral: <b>{'YOQILGAN' if ref=='1' else 'O‘CHIRILGAN'}</b>\n"
        f"📩 Support: <b>{support}</b>\n\n"
        "Admin ID lar Render ENV dagi <code>ADMIN_IDS</code> orqali boshqariladi.\n"
        "Katalog, paket, kanal, referral va matnlar alohida bo‘limlardan boshqariladi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Referralni o‘zgartirish", callback_data="toggle_ref")],
            [InlineKeyboardButton(text="⬅️ Admin", callback_data="adminhome")]
        ])
    ); await callback.answer()


@dp.callback_query(F.data == "auditlog")
async def auditlog(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return await deny(callback)
    c=await db(); rows=await fetchall(c,"SELECT admin_id,action,target,created_at FROM audit ORDER BY id DESC LIMIT 30"); await c.close()
    lines=["🧾 <b>AUDIT LOG</b>",""]
    for row in rows: lines.append(f"👤 {row[0]} | <b>{row[1]}</b> | {row[2] or '-'}\n🕒 {row[3]}")
    await callback.message.edit_text("\n".join(lines),reply_markup=await admin_back_kb()); await callback.answer()


# ============================================================
# HEALTH / STARTUP
# ============================================================

polling_task = None

@app.on_event("startup")
async def startup():
    global polling_task
    await init_db()
    if not bot:
        log.error("BOT_TOKEN ENV yo‘q.")
        return
    me = await bot.get_me()
    log.info("Bot started: @%s", me.username)
    polling_task = asyncio.create_task(
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    )


@app.on_event("shutdown")
async def shutdown():
    global polling_task
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    if bot:
        await bot.session.close()


@app.get("/")
async def root():
    return {"status": "ok", "service": "nastroyka-bot"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000"))
    )
