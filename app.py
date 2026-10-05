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
        ("iPhone", "📱", 1),
        ("Samsung", "📱", 2),
        ("Xiaomi", "📱", 3),
        ("Redmi", "📱", 4),
        ("POCO", "📱", 5),
        ("Realme", "📱", 6),
        ("Infinix", "📱", 7),
        ("Honor", "📱", 8),
        ("Tecno", "📱", 9),
        ("OPPO", "📱", 10),
        ("Vivo", "📱", 11),
    ]
    for name, emoji, sort in brand_data:
        await c.execute(
            "INSERT OR IGNORE INTO brands(name,emoji,sort) VALUES(?,?,?)",
            (name, emoji, sort)
        )

    # Keep the user-facing brand buttons consistent: every phone brand uses 📱.
    await c.execute("UPDATE brands SET emoji='📱'")

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
        builder.button(text=f"📱 {row[1]}", callback_data=f"brand:{row[0]}")
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

    welcome = await home_text()
    keyboard = await brands_kb()

    # Use the bot's own Telegram profile photo as the start banner when available.
    # This requires no extra image URL or paid storage.
    if bot:
        try:
            photos = await bot.get_user_profile_photos(bot.id, limit=1)
            if photos.total_count > 0 and photos.photos and photos.photos[0]:
                photo_id = photos.photos[0][-1].file_id
                return await message.answer_photo(
                    photo=photo_id,
                    caption=welcome,
                    reply_markup=keyboard
                )
        except Exception:
            pass

    await message.answer(welcome, reply_markup=keyboard)


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
        f"📱 <b>{row[0]}</b>\n\n"
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
        f"📱 <b>{row[2]} {row[1]}</b>\n\n"
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
        f"📱 <b>{brand_row[1]}</b>\n\nModelingizni tanlang:",
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
# ADMIN PANEL — EASY BUTTON CONTROL
# ============================================================

async def admin_kb():
    """Simple 2-column admin dashboard."""
    builder = InlineKeyboardBuilder()
    buttons = [
        ("📊 Statistika", "astats"),
        ("👥 Foydalanuvchilar", "users"),
        ("📱 Telefonlar", "phones"),
        ("💎 Paketlar", "packs"),
        ("📢 Kanallar", "channels"),
        ("🎁 Referral", "refs"),
        ("🔐 Kodlar", "codes"),
        ("📣 Reklama", "broadcast"),
        ("🚫 Ban / Unban", "banmenu"),
        ("📝 Matnlar", "texts"),
        ("⚙️ Sozlamalar", "settings"),
        ("🧾 Audit", "auditlog"),
    ]
    for text, data in buttons:
        builder.button(text=text, callback_data=data)
    builder.adjust(2)
    return builder.as_markup()


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def admin_back_kb():
    return kb([[InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adminhome")]])


async def admin_guard(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo‘q.", show_alert=True)
        return False
    return True


@dp.message(Command("admin"))
async def admin(message: Message):
    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Ruxsat yo‘q.")
    await message.answer(
        "👑 <b>NASTROYKA ADMIN PANEL</b>\n\n"
        "Kerakli bo‘limni tanlang. Hammasi tugmalar orqali boshqariladi.",
        reply_markup=await admin_kb()
    )


@dp.callback_query(F.data == "adminhome")
async def admin_home(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    await callback.message.edit_text(
        "👑 <b>NASTROYKA ADMIN PANEL</b>\n\n"
        "⚡ Tezkor boshqaruv",
        reply_markup=await admin_kb()
    )
    await callback.answer()


# ------------------------- STATISTICS -------------------------

@dp.callback_query(F.data == "astats")
async def admin_stats(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
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
        f"👥 Foydalanuvchilar: <b>{users[0]}</b>\n"
        f"🟢 Faol: <b>{active[0]}</b>\n"
        f"🎁 Referral: <b>{refs[0]}</b>\n"
        f"🔐 Kodlar: <b>{codes[0]}</b>\n"
        f"✅ Ishlatilgan: <b>{used[0]}</b>\n"
        f"📱 Brendlar: <b>{brands[0]}</b>\n"
        f"📲 Modellar: <b>{models[0]}</b>\n"
        f"📢 Kanallar: <b>{channels[0]}</b>",
        reply_markup=kb([
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="astats")],
            [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adminhome")]
        ])
    )
    await callback.answer()


# ------------------------- USERS -----------------------------

@dp.callback_query(F.data == "users")
async def admin_users(callback: CallbackQuery, state: FSMContext):
    if not await admin_guard(callback):
        return
    c = await db()
    rows = await fetchall(c, "SELECT id,username,first_name,referrals,banned FROM users ORDER BY last_seen DESC LIMIT 20")
    await c.close()
    lines = ["👥 <b>FOYDALANUVCHILAR</b>", ""]
    if not rows:
        lines.append("Hozircha foydalanuvchi yo‘q.")
    else:
        for i, row in enumerate(rows, 1):
            status = "🚫" if row[4] else "🟢"
            lines.append(f"{i}. <code>{row[0]}</code> @{row[1] or '-'} • REF {row[3]} {status}")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb([
            [InlineKeyboardButton(text="🔎 User ID bo‘yicha topish", callback_data="user_search")],
            [InlineKeyboardButton(text="🚫 Ban / Unban", callback_data="banmenu")],
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="users")],
            [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adminhome")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "user_search")
async def user_search_start(callback: CallbackQuery, state: FSMContext):
    if not await admin_guard(callback):
        return
    await state.set_state(Form.search_user)
    await callback.message.edit_text(
        "🔎 <b>USER QIDIRISH</b>\n\n"
        "Telegram ID raqamini yuboring:\n\n"
        "Masalan: <code>7849637859</code>",
        reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="users")]])
    )
    await callback.answer()


@dp.message(Form.search_user)
async def user_search_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    if not (message.text or "").isdigit():
        return await message.answer("❌ Faqat Telegram ID raqamini yuboring.")
    uid = int(message.text)
    c = await db()
    row = await fetchone(c, "SELECT id,username,first_name,referrals,referred_by,banned,created_at,last_seen FROM users WHERE id=?", (uid,))
    code_count = await fetchone(c, "SELECT COUNT(*) FROM codes WHERE user_id=?", (uid,))
    await c.close()
    await state.clear()
    if not row:
        return await message.answer("❌ User topilmadi.", reply_markup=await admin_back_kb())
    await message.answer(
        "👤 <b>USER MA’LUMOTI</b>\n\n"
        f"🆔 <code>{row[0]}</code>\n"
        f"👤 @{row[1] or '-'}\n"
        f"📝 {row[2] or '-'}\n"
        f"🎁 Referral: <b>{row[3]}</b>\n"
        f"🚦 Holat: {'🚫 BAN' if row[5] else '🟢 ACTIVE'}\n"
        f"🔐 Kodlar: <b>{code_count[0]}</b>",
        reply_markup=kb([
            [InlineKeyboardButton(text="🚫 Ban", callback_data=f"banone:{uid}")],
            [InlineKeyboardButton(text="🟢 Unban", callback_data=f"unbanone:{uid}")],
            [InlineKeyboardButton(text="⬅️ Users", callback_data="users")]
        ])
    )


# ------------------------- PHONES ----------------------------

@dp.callback_query(F.data == "phones")
async def phones(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    c = await db()
    brands = await fetchall(c, "SELECT id,name,active FROM brands ORDER BY sort,id")
    models = await fetchone(c, "SELECT COUNT(*) FROM models WHERE active=1")
    await c.close()
    await callback.message.edit_text(
        "📱 <b>TELEFONLAR</b>\n\n"
        f"🏷 Brendlar: <b>{len(brands)}</b>\n"
        f"📲 Faol modellar: <b>{models[0]}</b>\n\n"
        "Kerakli amalni bosing:",
        reply_markup=kb([
            [InlineKeyboardButton(text="📱 Brendlarni ko‘rish", callback_data="phone_brands")],
            [InlineKeyboardButton(text="➕ Brend qo‘shish", callback_data="phone_add_brand")],
            [InlineKeyboardButton(text="📲 Model qo‘shish", callback_data="phone_add_model")],
            [InlineKeyboardButton(text="🗑 Brendni yashirish", callback_data="phone_hide_brand")],
            [InlineKeyboardButton(text="🔄 Brendni qaytarish", callback_data="phone_show_brand")],
            [InlineKeyboardButton(text="🗑 Modelni yashirish", callback_data="phone_hide_model")],
            [InlineKeyboardButton(text="🔄 Modelni qaytarish", callback_data="phone_show_model")],
            [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adminhome")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "phone_brands")
async def phone_brands(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    c = await db()
    rows = await fetchall(c, "SELECT id,name,active FROM brands ORDER BY sort,id")
    await c.close()
    buttons = []
    for row in rows:
        buttons.append([InlineKeyboardButton(text=f"{'🟢' if row[2] else '🔴'} {row[1]}", callback_data=f"phone_brand:{row[0]}")])
    buttons.append([InlineKeyboardButton(text="➕ Brend qo‘shish", callback_data="phone_add_brand")])
    buttons.append([InlineKeyboardButton(text="⬅️ Telefonlar", callback_data="phones")])
    await callback.message.edit_text("📱 <b>BRENDLAR</b>\n\nBrendni tanlang:", reply_markup=kb(buttons))
    await callback.answer()


@dp.callback_query(F.data.startswith("phone_brand:"))
async def phone_brand_detail(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    bid = int(callback.data.split(":")[1])
    c = await db()
    brand = await fetchone(c, "SELECT id,name,active FROM brands WHERE id=?", (bid,))
    models = await fetchall(c, "SELECT id,name,active FROM models WHERE brand_id=? ORDER BY sort,id", (bid,))
    await c.close()
    if not brand:
        return await callback.answer("Brend topilmadi", show_alert=True)
    buttons = []
    for row in models:
        buttons.append([InlineKeyboardButton(text=f"{'🟢' if row[2] else '🔴'} {row[1]}", callback_data=f"model_toggle:{row[0]}")])
    buttons += [
        [InlineKeyboardButton(text="➕ Shu brendga model qo‘shish", callback_data=f"phone_add_model:{bid}")],
        [InlineKeyboardButton(text="🟢/🔴 Brend holatini o‘zgartirish", callback_data=f"brand_toggle:{bid}")],
        [InlineKeyboardButton(text="⬅️ Brendlar", callback_data="phone_brands")]
    ]
    await callback.message.edit_text(
        f"📱 <b>{brand[1]}</b>\n\n"
        f"Holat: {'🟢 Ko‘rinadi' if brand[2] else '🔴 Yashirilgan'}\n"
        f"Modellar: <b>{len(models)}</b>\n\n"
        "Modelni bosib holatini almashtiring.",
        reply_markup=kb(buttons)
    )
    await callback.answer()


@dp.callback_query(F.data == "phone_add_brand")
async def phone_add_brand_start(callback: CallbackQuery, state: FSMContext):
    if not await admin_guard(callback):
        return
    await state.set_state(Form.brand)
    await state.update_data(admin_action="add")
    await callback.message.edit_text(
        "➕ <b>BREND QO‘SHISH</b>\n\n"
        "Brend nomini yuboring.\n\n"
        "Masalan: <code>Huawei</code>",
        reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="phones")]])
    )
    await callback.answer()


@dp.message(Form.brand)
async def phone_add_brand_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 40:
        return await message.answer("❌ Brend nomini to‘g‘ri yuboring.")
    c = await db()
    await c.execute("INSERT OR IGNORE INTO brands(name,emoji,sort) VALUES(?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM brands))", (name, "📱"))
    await c.commit(); await c.close()
    await state.clear()
    await audit(message.from_user.id, "add_brand", name)
    await message.answer(f"✅ <b>{name}</b> qo‘shildi.", reply_markup=await admin_back_kb())


@dp.callback_query(F.data.startswith("phone_add_model:"))
async def phone_add_model_from_brand(callback: CallbackQuery, state: FSMContext):
    if not await admin_guard(callback):
        return
    bid = int(callback.data.split(":")[1])
    c = await db(); brand = await fetchone(c, "SELECT name FROM brands WHERE id=?", (bid,)); await c.close()
    if not brand:
        return await callback.answer("Brend topilmadi", show_alert=True)
    await state.set_state(Form.model)
    await state.update_data(admin_brand_id=bid, admin_brand_name=brand[0])
    await callback.message.edit_text(
        f"📲 <b>{brand[0]}</b> uchun model qo‘shish\n\nModel nomini yuboring.\nMasalan: <code>{brand[0]} 25 Ultra</code>",
        reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"phone_brand:{bid}")]])
    )
    await callback.answer()


@dp.callback_query(F.data == "phone_add_model")
async def phone_add_model_start(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    await callback.message.edit_text("📲 Avval model qaysi brendga tegishli ekanini tanlang:", reply_markup=await phone_brand_select_kb("phone_add_model"))
    await callback.answer()


async def phone_brand_select_kb(action):
    c = await db(); rows = await fetchall(c, "SELECT id,name FROM brands WHERE active=1 ORDER BY sort,id"); await c.close()
    buttons = [[InlineKeyboardButton(text=f"📱 {r[1]}", callback_data=f"{action}:{r[0]}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="⬅️ Telefonlar", callback_data="phones")])
    return kb(buttons)


@dp.message(Form.model)
async def phone_add_model_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data(); bid = data.get("admin_brand_id"); brand_name = data.get("admin_brand_name", "")
    name = (message.text or "").strip()
    if not bid or len(name) < 2:
        return await message.answer("❌ Model nomini to‘g‘ri yuboring.")
    c = await db(); await c.execute("INSERT OR IGNORE INTO models(brand_id,name,sort) VALUES(?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM models WHERE brand_id=?))", (bid,name,bid)); await c.commit(); await c.close()
    await state.clear(); await audit(message.from_user.id,"add_model",f"{brand_name}: {name}")
    await message.answer(f"✅ <b>{name}</b> qo‘shildi.", reply_markup=await admin_back_kb())


@dp.callback_query(F.data == "phone_hide_brand")
async def phone_hide_brand(callback: CallbackQuery):
    if not await admin_guard(callback): return
    await callback.message.edit_text("🗑 Yashiriladigan brendni tanlang:", reply_markup=await phone_brand_select_kb("hide_brand")); await callback.answer()


@dp.callback_query(F.data == "phone_show_brand")
async def phone_show_brand(callback: CallbackQuery):
    if not await admin_guard(callback): return
    c=await db(); rows=await fetchall(c,"SELECT id,name FROM brands WHERE active=0 ORDER BY id"); await c.close()
    buttons=[[InlineKeyboardButton(text=f"🔄 {r[1]}",callback_data=f"show_brand:{r[0]}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="⬅️ Telefonlar",callback_data="phones")])
    await callback.message.edit_text("🔄 Qaytariladigan brendni tanlang:",reply_markup=kb(buttons)); await callback.answer()


@dp.callback_query(F.data.startswith("hide_brand:"))
async def hide_brand(callback: CallbackQuery):
    if not await admin_guard(callback): return
    bid=int(callback.data.split(":")[1]); c=await db(); await c.execute("UPDATE brands SET active=0 WHERE id=?",(bid,)); await c.commit(); await c.close(); await callback.answer("🗑 Yashirildi"); await phone_brands(callback)


@dp.callback_query(F.data.startswith("show_brand:"))
async def show_brand(callback: CallbackQuery):
    if not await admin_guard(callback): return
    bid=int(callback.data.split(":")[1]); c=await db(); await c.execute("UPDATE brands SET active=1 WHERE id=?",(bid,)); await c.commit(); await c.close(); await callback.answer("🔄 Qaytarildi"); await phone_brands(callback)


@dp.callback_query(F.data.startswith("brand_toggle:"))
async def brand_toggle(callback: CallbackQuery):
    if not await admin_guard(callback): return
    bid=int(callback.data.split(":")[1]); c=await db(); await c.execute("UPDATE brands SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(bid,)); await c.commit(); await c.close(); await phone_brand_detail(callback)


@dp.callback_query(F.data.startswith("model_toggle:"))
async def model_toggle(callback: CallbackQuery):
    if not await admin_guard(callback): return
    mid=int(callback.data.split(":")[1]); c=await db(); row=await fetchone(c,"SELECT brand_id FROM models WHERE id=?",(mid,)); await c.execute("UPDATE models SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(mid,)); await c.commit(); await c.close();
    if row: callback.data=f"phone_brand:{row[0]}"; await phone_brand_detail(callback)
    else: await callback.answer("Model topilmadi",show_alert=True)


@dp.callback_query(F.data == "phone_hide_model")
async def phone_hide_model(callback: CallbackQuery):
    if not await admin_guard(callback): return
    await callback.message.edit_text("🗑 Yashiriladigan modelni tanlang:", reply_markup=await model_select_kb(0)); await callback.answer()


@dp.callback_query(F.data == "phone_show_model")
async def phone_show_model(callback: CallbackQuery):
    if not await admin_guard(callback): return
    await callback.message.edit_text("🔄 Qaytariladigan modelni tanlang:", reply_markup=await model_select_kb(1)); await callback.answer()


async def model_select_kb(active):
    c=await db(); rows=await fetchall(c,"SELECT m.id,b.name,m.name FROM models m JOIN brands b ON b.id=m.brand_id WHERE m.active=? ORDER BY b.sort,m.sort LIMIT 80",(active,)); await c.close()
    buttons=[[InlineKeyboardButton(text=f"{'🗑' if active else '🔄'} {r[1]} • {r[2]}",callback_data=f"model_set:{r[0]}:{1-active}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="⬅️ Telefonlar",callback_data="phones")])
    return kb(buttons)


@dp.callback_query(F.data.startswith("model_set:"))
async def model_set(callback: CallbackQuery):
    if not await admin_guard(callback): return
    _,mid,val=callback.data.split(":"); c=await db(); await c.execute("UPDATE models SET active=? WHERE id=?",(int(val),int(mid))); await c.commit(); await c.close(); await callback.answer("✅ Saqlandi"); await phones(callback)


# ------------------------- PACKAGES --------------------------

@dp.callback_query(F.data == "packs")
async def packages_admin(callback: CallbackQuery):
    if not await admin_guard(callback): return
    c=await db(); rows=await fetchall(c,"SELECT id,name,emoji,refs,description,active FROM packages ORDER BY sort,id"); await c.close()
    buttons=[[InlineKeyboardButton(text=f"{'🟢' if r[5] else '🔴'} {r[1]} • {r[3]} ref",callback_data=f"pack_edit:{r[0]}")] for r in rows]
    buttons += [[InlineKeyboardButton(text="➕ Paket qo‘shish",callback_data="pack_add")],[InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adminhome")]]
    await callback.message.edit_text("💎 <b>PAKETLAR</b>\n\nPaketni tanlang yoki yangi paket qo‘shing.",reply_markup=kb(buttons)); await callback.answer()


@dp.callback_query(F.data.startswith("pack_edit:"))
async def pack_edit(callback: CallbackQuery):
    if not await admin_guard(callback): return
    pid=int(callback.data.split(":")[1]); c=await db(); r=await fetchone(c,"SELECT id,name,emoji,refs,description,active FROM packages WHERE id=?",(pid,)); await c.close()
    if not r: return await callback.answer("Paket topilmadi",show_alert=True)
    await callback.message.edit_text(
        f"💎 <b>{r[1]}</b>\n\n🔢 Referral: <b>{r[3]}</b>\n🟢 Holat: <b>{'Yoqilgan' if r[5] else 'O‘chirilgan'}</b>\n📝 {r[4] or 'Tavsif yo‘q'}",
        reply_markup=kb([
            [InlineKeyboardButton(text="✏️ Nomini o‘zgartirish",callback_data=f"pack_name:{pid}")],
            [InlineKeyboardButton(text="🔢 Referral sonini o‘zgartirish",callback_data=f"pack_refs:{pid}")],
            [InlineKeyboardButton(text="📝 Tavsifni o‘zgartirish",callback_data=f"pack_desc:{pid}")],
            [InlineKeyboardButton(text="🟢/🔴 Yoqish / o‘chirish",callback_data=f"pack_toggle:{pid}")],
            [InlineKeyboardButton(text="⬅️ Paketlar",callback_data="packs")]
        ])
    ); await callback.answer()


@dp.callback_query(F.data == "pack_add")
async def pack_add(callback: CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.package); await state.update_data(pack_action="add")
    await callback.message.edit_text("➕ <b>YANGI PAKET</b>\n\nShu ko‘rinishda yuboring:\n<code>VIP | 3 | VIP daraja</code>\n\nNomi | Referral | Tavsif",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="packs")]])); await callback.answer()


@dp.message(Form.package)
async def pack_add_save(message:Message,state:FSMContext):
    if not await is_admin(message.from_user.id): return
    data=(message.text or "").split("|",2)
    if len(data)!=3 or not data[1].strip().isdigit(): return await message.answer("❌ Noto‘g‘ri format. Masalan: VIP | 3 | VIP daraja")
    name=data[0].strip(); refs=int(data[1].strip()); desc=data[2].strip(); c=await db(); await c.execute("INSERT INTO packages(name,emoji,refs,description,sort) VALUES(?,?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM packages))",(name,"💎",refs,desc)); await c.commit(); await c.close(); await state.clear(); await message.answer(f"✅ <b>{name}</b> qo‘shildi.",reply_markup=await admin_back_kb())


async def pack_prompt(callback:CallbackQuery,pid:int,field:str,label:str,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.setting); await state.update_data(pack_id=pid,pack_field=field)
    await callback.message.edit_text(f"✏️ <b>{label}</b>\n\nYangi qiymatni yuboring.",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data=f"pack_edit:{pid}")]])); await callback.answer()


@dp.callback_query(F.data.startswith("pack_name:"))
async def pack_name(callback:CallbackQuery,state:FSMContext): await pack_prompt(callback,int(callback.data.split(":")[1]),"name","Yangi nom",state)
@dp.callback_query(F.data.startswith("pack_refs:"))
async def pack_refs(callback:CallbackQuery,state:FSMContext): await pack_prompt(callback,int(callback.data.split(":")[1]),"refs","Referral soni",state)
@dp.callback_query(F.data.startswith("pack_desc:"))
async def pack_desc(callback:CallbackQuery,state:FSMContext): await pack_prompt(callback,int(callback.data.split(":")[1]),"description","Tavsif",state)


@dp.message(Form.setting)
async def pack_setting_save(message:Message,state:FSMContext):
    if not await is_admin(message.from_user.id): return
    d=await state.get_data(); pid=d.get("pack_id"); field=d.get("pack_field"); value=(message.text or "").strip()
    if field=="refs":
        if not value.isdigit(): return await message.answer("❌ Faqat raqam yuboring.")
        value=int(value)
    if field not in {"name","refs","description"}: return await state.clear()
    c=await db(); await c.execute(f"UPDATE packages SET {field}=? WHERE id=?",(value,pid)); await c.commit(); await c.close(); await state.clear(); await message.answer("✅ Paket saqlandi.",reply_markup=await admin_back_kb())


@dp.callback_query(F.data.startswith("pack_toggle:"))
async def pack_toggle(callback:CallbackQuery):
    if not await admin_guard(callback): return
    pid=int(callback.data.split(":")[1]); c=await db(); await c.execute("UPDATE packages SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(pid,)); await c.commit(); await c.close(); await callback.answer("✅ Holat o‘zgardi"); await pack_edit(callback)


# ------------------------- CHANNELS --------------------------

@dp.callback_query(F.data == "channels")
async def admin_channels(callback:CallbackQuery):
    if not await admin_guard(callback): return
    c=await db(); rows=await fetchall(c,"SELECT id,chat_id,title,link,active FROM channels ORDER BY id"); await c.close()
    buttons=[[InlineKeyboardButton(text=f"{'🟢' if r[4] else '🔴'} {r[2] or r[1]}",callback_data=f"channel_edit:{r[0]}")] for r in rows]
    buttons += [[InlineKeyboardButton(text="➕ Kanal qo‘shish",callback_data="channel_add")],[InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adminhome")]]
    await callback.message.edit_text("📢 <b>MAJBURIY OBUNA</b>\n\nKanalni tanlang yoki yangi kanal qo‘shing.\n\n⚠️ Bot kanalga admin bo‘lishi kerak.",reply_markup=kb(buttons)); await callback.answer()


@dp.callback_query(F.data == "channel_add")
async def channel_add(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.channel)
    await callback.message.edit_text("➕ <b>KANAL QO‘SHISH</b>\n\nYuboring:\n<code>@kanal | Kanal nomi</code>\n\nLink avtomatik yaratiladi.",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="channels")]])); await callback.answer()


@dp.message(Form.channel)
async def channel_save(message:Message,state:FSMContext):
    if not await is_admin(message.from_user.id): return
    parts=[x.strip() for x in (message.text or "").split("|",1)]
    chat_id=parts[0] if parts else ""
    if not chat_id.startswith("@"): return await message.answer("❌ Kanal @username ko‘rinishida bo‘lsin.")
    title=parts[1] if len(parts)>1 and parts[1] else chat_id
    link=f"https://t.me/{chat_id[1:]}"
    c=await db(); await c.execute("INSERT OR REPLACE INTO channels(chat_id,title,link,active) VALUES(?,?,?,1)",(chat_id,title,link)); await c.commit(); await c.close(); await state.clear(); await message.answer("✅ Kanal qo‘shildi.",reply_markup=await admin_back_kb())


@dp.callback_query(F.data.startswith("channel_edit:"))
async def channel_edit(callback:CallbackQuery):
    if not await admin_guard(callback): return
    cid=int(callback.data.split(":")[1]); c=await db(); r=await fetchone(c,"SELECT id,chat_id,title,link,active FROM channels WHERE id=?",(cid,)); await c.close()
    if not r: return await callback.answer("Kanal topilmadi",show_alert=True)
    await callback.message.edit_text(f"📢 <b>{r[2] or r[1]}</b>\n\n{r[1]}\nHolat: {'🟢 Yoqilgan' if r[4] else '🔴 O‘chirilgan'}",reply_markup=kb([
        [InlineKeyboardButton(text="🟢/🔴 Yoqish / o‘chirish",callback_data=f"channel_toggle:{cid}")],
        [InlineKeyboardButton(text="🗑 O‘chirish",callback_data=f"channel_delete:{cid}")],
        [InlineKeyboardButton(text="⬅️ Kanallar",callback_data="channels")]
    ])); await callback.answer()


@dp.callback_query(F.data.startswith("channel_toggle:"))
async def channel_toggle(callback:CallbackQuery):
    if not await admin_guard(callback): return
    cid=int(callback.data.split(":")[1]); c=await db(); await c.execute("UPDATE channels SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(cid,)); await c.commit(); await c.close(); await callback.answer("✅ Saqlandi"); await channel_edit(callback)


@dp.callback_query(F.data.startswith("channel_delete:"))
async def channel_delete(callback:CallbackQuery):
    if not await admin_guard(callback): return
    cid=int(callback.data.split(":")[1]); c=await db(); await c.execute("DELETE FROM channels WHERE id=?",(cid,)); await c.commit(); await c.close(); await callback.answer("🗑 O‘chirildi"); await admin_channels(callback)


# ------------------------- REFERRAL --------------------------

@dp.callback_query(F.data == "refs")
async def admin_refs(callback:CallbackQuery):
    if not await admin_guard(callback): return
    enabled=await get_setting("ref_enabled","1")
    await callback.message.edit_text(
        "🎁 <b>REFERRAL</b>\n\n"
        f"Holat: <b>{'🟢 YOQILGAN' if enabled=='1' else '🔴 O‘CHIRILGAN'}</b>\n\n"
        "Referralni yoqish/o‘chirish yoki userga qo‘shish uchun tugmani bosing.",
        reply_markup=kb([
            [InlineKeyboardButton(text="🔄 Yoqish / o‘chirish",callback_data="toggle_ref")],
            [InlineKeyboardButton(text="➕ Userga referral qo‘shish",callback_data="ref_add")],
            [InlineKeyboardButton(text="➖ Userdan referral ayirish",callback_data="ref_remove")],
            [InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adminhome")]
        ])
    ); await callback.answer()


@dp.callback_query(F.data == "toggle_ref")
async def toggle_ref(callback:CallbackQuery):
    if not await admin_guard(callback): return
    current=await get_setting("ref_enabled","1"); new="0" if current=="1" else "1"; await set_setting("ref_enabled",new); await callback.answer("✅ Saqlandi"); await admin_refs(callback)


@dp.callback_query(F.data == "ref_add")
async def ref_add(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.add_ref); await state.update_data(ref_sign=1)
    await callback.message.edit_text("➕ <b>REFERRAL QO‘SHISH</b>\n\nYuboring: <code>USER_ID | SONI</code>\nMasalan: <code>7849637859 | 5</code>",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="refs")]])); await callback.answer()


@dp.callback_query(F.data == "ref_remove")
async def ref_remove(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.add_ref); await state.update_data(ref_sign=-1)
    await callback.message.edit_text("➖ <b>REFERRAL AYIRISH</b>\n\nYuboring: <code>USER_ID | SONI</code>",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="refs")]])); await callback.answer()


@dp.message(Form.add_ref)
async def ref_save(message:Message,state:FSMContext):
    if not await is_admin(message.from_user.id): return
    d=await state.get_data()
    try:
        uid_s,n_s=[x.strip() for x in (message.text or "").split("|",1)]; uid=int(uid_s); amount=int(n_s)*int(d.get("ref_sign",1));
    except Exception: return await message.answer("❌ Format: USER_ID | SONI")
    c=await db(); result=await c.execute("UPDATE users SET referrals=MAX(0,referrals+?) WHERE id=?",(amount,uid)); await c.commit(); await c.close(); await state.clear()
    if result.rowcount==0: return await message.answer("❌ User topilmadi.",reply_markup=await admin_back_kb())
    await message.answer("✅ Referral o‘zgartirildi.",reply_markup=await admin_back_kb())


# ------------------------- BAN / UNBAN -----------------------

@dp.callback_query(F.data == "banmenu")
async def banmenu(callback:CallbackQuery):
    if not await admin_guard(callback): return
    await callback.message.edit_text("🚫 <b>BAN / UNBAN</b>\n\nKerakli amalni tanlang.",reply_markup=kb([
        [InlineKeyboardButton(text="🚫 Ban qilish",callback_data="ban_start")],
        [InlineKeyboardButton(text="🟢 Unban qilish",callback_data="unban_start")],
        [InlineKeyboardButton(text="🔎 User topish",callback_data="user_search")],
        [InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adminhome")]
    ])); await callback.answer()


@dp.callback_query(F.data == "ban_start")
async def ban_start(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.ban); await state.update_data(ban_value=1)
    await callback.message.edit_text("🚫 Ban qilinadigan user ID ni yuboring.",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="banmenu")]])); await callback.answer()


@dp.callback_query(F.data == "unban_start")
async def unban_start(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.ban); await state.update_data(ban_value=0)
    await callback.message.edit_text("🟢 Unban qilinadigan user ID ni yuboring.",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="banmenu")]])); await callback.answer()


@dp.message(Form.ban)
async def ban_save(message:Message,state:FSMContext):
    if not await is_admin(message.from_user.id): return
    if not (message.text or "").isdigit(): return await message.answer("❌ Faqat User ID yuboring.")
    d=await state.get_data(); uid=int(message.text); value=int(d.get("ban_value",1)); c=await db(); result=await c.execute("UPDATE users SET banned=? WHERE id=?",(value,uid)); await c.commit(); await c.close(); await state.clear()
    if result.rowcount==0: return await message.answer("❌ User topilmadi.",reply_markup=await admin_back_kb())
    await message.answer("🚫 User bloklandi." if value else "✅ User blokdan chiqarildi.",reply_markup=await admin_back_kb())


@dp.callback_query(F.data.startswith("banone:"))
async def banone(callback:CallbackQuery):
    if not await admin_guard(callback): return
    uid=int(callback.data.split(":")[1]); c=await db(); await c.execute("UPDATE users SET banned=1 WHERE id=?",(uid,)); await c.commit(); await c.close(); await callback.answer("🚫 Ban qilindi"); await admin_users(callback)


@dp.callback_query(F.data.startswith("unbanone:"))
async def unbanone(callback:CallbackQuery):
    if not await admin_guard(callback): return
    uid=int(callback.data.split(":")[1]); c=await db(); await c.execute("UPDATE users SET banned=0 WHERE id=?",(uid,)); await c.commit(); await c.close(); await callback.answer("✅ Unban qilindi"); await admin_users(callback)


# ------------------------- BROADCAST -------------------------

@dp.callback_query(F.data == "broadcast")
async def broadcast_start(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.broadcast)
    await callback.message.edit_text("📣 <b>REKLAMA YUBORISH</b>\n\nKeyingi yuborgan xabaringiz barcha bloklanmagan userlarga yuboriladi.\n\nMatn, rasm yoki video yuborishingiz mumkin.",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="adminhome")]])); await callback.answer()


@dp.message(Command("cancel"))
async def cancel(message:Message,state:FSMContext):
    if await is_admin(message.from_user.id): await state.clear(); await message.answer("✅ Bekor qilindi.",reply_markup=await admin_back_kb())


@dp.message(Form.broadcast)
async def do_broadcast(message:Message,state:FSMContext):
    if not await is_admin(message.from_user.id): return
    c=await db(); users=await fetchall(c,"SELECT id FROM users WHERE banned=0"); await c.close(); success=0; failed=0
    for row in users:
        try: await bot.copy_message(chat_id=row[0],from_chat_id=message.chat.id,message_id=message.message_id); success+=1
        except Exception: failed+=1
        await asyncio.sleep(0.05)
    await state.clear(); await message.answer(f"📣 <b>YAKUNLANDI</b>\n\n✅ Yetkazildi: <b>{success}</b>\n❌ Xato: <b>{failed}</b>",reply_markup=await admin_back_kb())


# ------------------------- CODES ----------------------------

@dp.callback_query(F.data == "codes")
async def admin_codes(callback:CallbackQuery):
    if not await admin_guard(callback): return
    c=await db(); rows=await fetchall(c,"SELECT code,user_id,brand,model,package,used FROM codes ORDER BY id DESC LIMIT 20"); await c.close()
    lines=["🔐 <b>MAXFIY KODLAR</b>",""]
    if not rows: lines.append("Hozircha kod yo‘q.")
    for r in rows: lines += [f"<code>{r[0]}</code> — {'✅ Ishlatilgan' if r[5] else '🟢 Yangi'}",f"👤 {r[1]} • 📱 {r[2]} {r[3]} • 💎 {r[4]}",""]
    await callback.message.edit_text("\n".join(lines),reply_markup=kb([[InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adminhome")]])); await callback.answer()


# ------------------------- TEXTS / SETTINGS ------------------

@dp.callback_query(F.data == "texts")
async def texts(callback:CallbackQuery):
    if not await admin_guard(callback): return
    start=await get_setting("start_text"); support=await get_setting("support",SUPPORT)
    await callback.message.edit_text("📝 <b>MATNLAR</b>\n\n📌 Start matni mavjud\n📩 Support: <b>"+support+"</b>",reply_markup=kb([
        [InlineKeyboardButton(text="✏️ Start matnini o‘zgartirish",callback_data="setstart")],
        [InlineKeyboardButton(text="✏️ Supportni o‘zgartirish",callback_data="setsupport")],
        [InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adminhome")]
    ])); await callback.answer()


@dp.callback_query(F.data == "setstart")
async def setstart_button(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.text); await state.update_data(text_key="start_text")
    await callback.message.edit_text("✏️ <b>START MATNI</b>\n\nYangi matnni yuboring.",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="texts")]])); await callback.answer()


@dp.callback_query(F.data == "setsupport")
async def setsupport_button(callback:CallbackQuery,state:FSMContext):
    if not await admin_guard(callback): return
    await state.set_state(Form.text); await state.update_data(text_key="support")
    await callback.message.edit_text("✏️ <b>SUPPORT</b>\n\nMasalan: <code>@ruzvix</code>",reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish",callback_data="texts")]])); await callback.answer()


@dp.message(Form.text)
async def text_save(message:Message,state:FSMContext):
    if not await is_admin(message.from_user.id): return
    d=await state.get_data(); key=d.get("text_key")
    if key not in {"start_text","support"}: return await state.clear()
    value=(message.text or "").strip()
    if not value: return await message.answer("❌ Bo‘sh matn yuborib bo‘lmaydi.")
    await set_setting(key,value); await state.clear(); await message.answer("✅ Saqlandi.",reply_markup=await admin_back_kb())


@dp.callback_query(F.data == "settings")
async def settings(callback:CallbackQuery):
    if not await admin_guard(callback): return
    ref=await get_setting("ref_enabled","1"); support=await get_setting("support",SUPPORT)
    await callback.message.edit_text(
        "⚙️ <b>SOZLAMALAR</b>\n\n"
        f"🎁 Referral: <b>{'🟢 Yoqilgan' if ref=='1' else '🔴 O‘chirilgan'}</b>\n"
        f"📩 Support: <b>{support}</b>\n\n"
        "Admin ID Render → Environment → ADMIN_IDS orqali boshqariladi.",
        reply_markup=kb([
            [InlineKeyboardButton(text="🎁 Referralni yoq / o‘chir",callback_data="toggle_ref")],
            [InlineKeyboardButton(text="📝 Matnlar",callback_data="texts")],
            [InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adminhome")]
        ])
    ); await callback.answer()


@dp.callback_query(F.data == "toggle_ref")
async def toggle_ref(callback:CallbackQuery):
    if not await admin_guard(callback): return
    current=await get_setting("ref_enabled","1"); new="0" if current=="1" else "1"; await set_setting("ref_enabled",new); await callback.answer("✅ Saqlandi"); await settings(callback)


@dp.callback_query(F.data == "auditlog")
async def auditlog(callback:CallbackQuery):
    if not await admin_guard(callback): return
    c=await db(); rows=await fetchall(c,"SELECT admin_id,action,target,created_at FROM audit ORDER BY id DESC LIMIT 20"); await c.close()
    lines=["🧾 <b>AUDIT</b>",""]
    for r in rows: lines.append(f"👤 {r[0]} • <b>{r[1]}</b> • {r[2] or '-'}")
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
