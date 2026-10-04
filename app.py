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
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI
import uvicorn


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

DB_PATH = os.getenv("DB_PATH", "ruzvix.db")

SUPPORT = os.getenv("SUPPORT", "@ruzvix")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ruzvix")


# ============================================================
# BOT
# ============================================================

bot = None

if BOT_TOKEN:
    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

dp = Dispatcher()

app = FastAPI(
    title="Ruzvix Sensi Bot"
)


# ============================================================
# STATES
# ============================================================

class Form(StatesGroup):

    broadcast = State()

    brand = State()
    model = State()

    channel = State()

    add_ref = State()

    ban = State()

    text = State()


# ============================================================
# DATABASE
# ============================================================

async def db():

    connection = await aiosqlite.connect(DB_PATH)

    connection.row_factory = aiosqlite.Row

    await connection.execute(
        "PRAGMA journal_mode=WAL"
    )

    return connection


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
        sort INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        emoji TEXT DEFAULT '🟢',
        refs INTEGER NOT NULL,
        active INTEGER DEFAULT 1,
        sort INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT UNIQUE NOT NULL,
        title TEXT,
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        brand TEXT,
        model TEXT,
        package TEXT,
        refs_used INTEGER,
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

    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        action TEXT,
        target TEXT,
        created_at TEXT
    );

    """)

    defaults = [

        (
            "start_text",
            """🔥 <b>RUZVIX SENSI</b>

🎮 Professional Free Fire settings bot.

📱 Telefoningiz brendini tanlang:"""
        ),

        (
            "support",
            SUPPORT
        ),

        (
            "ref_enabled",
            "1"
        )

    ]

    for key, value in defaults:

        await c.execute(
            """
            INSERT OR IGNORE INTO settings(key,value)
            VALUES(?,?)
            """,
            (key, value)
        )

    packages = [
        ("Base", "🟢", 1, 1),
        ("Premium", "🟡", 2, 2),
        ("VIP", "🔴", 3, 3)
    ]

    for name, emoji, refs, sort in packages:

        await c.execute(
            """
            INSERT OR IGNORE INTO packages
            (name,emoji,refs,sort)
            VALUES(?,?,?,?)
            """,
            (
                name,
                emoji,
                refs,
                sort
            )
        )

    brands = [
        "iPhone",
        "Samsung",
        "Xiaomi",
        "Redmi",
        "POCO",
        "Realme",
        "Infinix",
        "Honor",
        "Tecno",
        "OPPO",
        "Vivo"
    ]

    for name in brands:

        await c.execute(
            "INSERT OR IGNORE INTO brands(name) VALUES(?)",
            (name,)
        )

    await c.commit()

    await c.close()




# ============================================================
# SQLITE HELPERS (aiosqlite compatibility)
# ============================================================

async def fetchone(connection, query, params=()):
    async with connection.execute(query, params) as cursor:
        return await cursor.fetchone()


async def fetchall(connection, query, params=()):
    async with connection.execute(query, params) as cursor:
        return await cursor.fetchall()


# ============================================================
# SETTINGS
# ============================================================

async def get_setting(
    key,
    default=""
):

    c = await db()

    row = await fetchone(c, 
        """
        SELECT value
        FROM settings
        WHERE key=?
        """,
        (key,)
    )

    await c.close()

    if row:
        return row[0]

    return default


async def set_setting(
    key,
    value
):

    c = await db()

    await c.execute(
        """
        INSERT INTO settings(key,value)
        VALUES(?,?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (
            key,
            value
        )
    )

    await c.commit()

    await c.close()


# ============================================================
# ADMIN CHECK
# ============================================================

async def is_admin(user_id):

    return user_id in ADMIN_IDS


async def audit(
    admin_id,
    action,
    target=""
):

    c = await db()

    await c.execute(
        """
        INSERT INTO audit
        (admin_id,action,target,created_at)
        VALUES(?,?,?,?)
        """,
        (
            admin_id,
            action,
            target,
            datetime.now(
                timezone.utc
            ).isoformat()
        )
    )

    await c.commit()

    await c.close()


# ============================================================
# USER
# ============================================================

async def upsert_user(
    user,
    referrer: Optional[int] = None
):

    c = await db()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    existing = await fetchone(c, 
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (user.id,)
    )

    if not existing:

        if referrer == user.id:
            referrer = None

        await c.execute(
            """
            INSERT INTO users
            (
                id,
                username,
                first_name,
                referrals,
                referred_by,
                created_at,
                last_seen
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                user.id,
                user.username,
                user.first_name or "",
                0,
                referrer,
                now,
                now
            )
        )

    else:

        await c.execute(
            """
            UPDATE users

            SET username=?,
                first_name=?,
                last_seen=?

            WHERE id=?
            """,
            (
                user.username,
                user.first_name or "",
                now,
                user.id
            )
        )

    await c.commit()

    await c.close()


# ============================================================
# FINALIZE REFERRAL
# ============================================================

async def finalize_referral(
    user_id
):

    c = await db()

    row = await fetchone(c, 
        """
        SELECT referred_by
        FROM users
        WHERE id=?
        """,
        (user_id,)
    )

    if not row or not row[0]:

        await c.close()

        return None

    inviter_id = row[0]

    if inviter_id == user_id:

        await c.close()

        return None

    already = await fetchone(c, 
        """
        SELECT id
        FROM referrals
        WHERE invitee_id=?
        """,
        (user_id,)
    )

    if already:

        await c.close()

        return None

    inviter = await fetchone(c, 
        """
        SELECT id
        FROM users
        WHERE id=?
        AND banned=0
        """,
        (inviter_id,)
    )

    if not inviter:

        await c.close()

        return None

    now = datetime.now(
        timezone.utc
    ).isoformat()

    await c.execute(
        """
        INSERT INTO referrals
        (
            inviter_id,
            invitee_id,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            inviter_id,
            user_id,
            now
        )
    )

    await c.execute(
        """
        UPDATE users

        SET referrals=referrals+1

        WHERE id=?
        """,
        (inviter_id,)
    )

    await c.commit()

    await c.close()

    return inviter_id


# ============================================================
# BAN
# ============================================================

async def banned(user_id):

    c = await db()

    row = await fetchone(c, 
        """
        SELECT banned
        FROM users
        WHERE id=?
        """,
        (user_id,)
    )

    await c.close()

    return bool(
        row and row[0]
    )


# ============================================================
# REQUIRED CHANNELS
# ============================================================

async def channels_ok(
    user_id
):

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT chat_id,title
        FROM channels
        WHERE active=1
        """
    )

    await c.close()

    if not rows:

        return True

    for row in rows:

        try:

            member = await bot.get_chat_member(
                row[0],
                user_id
            )

            if member.status in (
                "left",
                "kicked"
            ):

                return False

        except Exception:

            return False

    return True


async def channel_kb():

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT chat_id,title
        FROM channels
        WHERE active=1
        """
    )

    await c.close()

    builder = InlineKeyboardBuilder()

    for row in rows:

        title = row[1] or row[0]

        builder.button(
            text=f"📢 {title}",
            url=(
                "https://t.me/"
                + row[0].lstrip("@")
            )
        )

    builder.button(
        text="✅ Obunani tekshirish",
        callback_data="checksub"
    )

    builder.adjust(1)

    return builder.as_markup()


# ============================================================
# USER KEYBOARDS
# ============================================================

async def brands_kb():

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT id,name,emoji
        FROM brands
        WHERE active=1
        ORDER BY sort,id
        """
    )

    await c.close()

    builder = InlineKeyboardBuilder()

    for row in rows:

        builder.button(
            text=f"{row[2]} {row[1]}",
            callback_data=f"brand:{row[0]}"
        )

    builder.button(
        text="⚙️ Maxsus sozlama",
        callback_data="custom"
    )

    builder.button(
        text="🎁 Referral",
        callback_data="myref"
    )

    builder.button(
        text="🆘 Yordam",
        callback_data="help"
    )

    builder.adjust(2)

    return builder.as_markup()


async def models_kb(
    brand_id
):

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT id,name
        FROM models
        WHERE brand_id=?
        AND active=1
        ORDER BY sort,id
        """,
        (brand_id,)
    )

    await c.close()

    builder = InlineKeyboardBuilder()

    for row in rows:

        builder.button(
            text=row[1],
            callback_data=f"model:{row[0]}"
        )

    builder.button(
        text="⬅️ Brendlar",
        callback_data="home"
    )

    builder.adjust(2)

    return builder.as_markup()


async def packages_kb():

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT id,name,emoji,refs
        FROM packages
        WHERE active=1
        ORDER BY sort,id
        """
    )

    await c.close()

    builder = InlineKeyboardBuilder()

    for row in rows:

        builder.button(
            text=(
                f"{row[2]} {row[1]} "
                f"({row[3]} referral)"
            ),
            callback_data=f"pkg:{row[0]}"
        )

    builder.button(
        text="⬅️ Orqaga",
        callback_data="home"
    )

    builder.adjust(1)

    return builder.as_markup()


# ============================================================
# ADMIN KEYBOARD
# ============================================================

async def admin_kb():

    builder = InlineKeyboardBuilder()

    buttons = [

        ("📊 Statistika", "astats"),
        ("👥 Users", "users"),

        ("📱 Telefonlar", "phones"),
        ("💎 Paketlar", "packs"),

        ("📢 Majburiy obuna", "channels"),
        ("📣 Reklama", "broadcast"),

        ("🔐 Kodlar", "codes"),
        ("🎁 Referral", "refs"),

        ("🚫 Ban", "banmenu"),
        ("📝 Matnlar", "texts"),

        ("⚙️ Sozlamalar", "settings")

    ]

    for text, callback in buttons:

        builder.button(
            text=text,
            callback_data=callback
        )

    builder.adjust(2)

    return builder.as_markup()


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start(
    message: Message
):

    user = message.from_user

    if await banned(user.id):

        return await message.answer(
            "🚫 Siz botdan foydalanish "
            "huquqidan mahrumsiz."
        )

    referrer = None

    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )

    if (
        len(parts) == 2
        and parts[1].isdigit()
    ):

        referrer = int(
            parts[1]
        )

    await upsert_user(
        user,
        referrer
    )

    if not await channels_ok(
        user.id
    ):

        return await message.answer(
            "🔒 <b>Davom etish uchun "
            "majburiy kanallarga obuna bo‘ling.</b>",
            reply_markup=await channel_kb()
        )

    inviter = await finalize_referral(
        user.id
    )

    if inviter:

        try:

            await bot.send_message(
                inviter,
                "🎉 Yangi referral!\n"
                "Sizga <b>+1 referral</b> qo‘shildi."
            )

        except Exception:

            pass

    await message.answer(
        await get_setting(
            "start_text"
        ),
        reply_markup=await brands_kb()
    )


# ============================================================
# SUBSCRIPTION CHECK
# ============================================================

@dp.callback_query(
    F.data == "checksub"
)
async def checksub(
    callback: CallbackQuery
):

    if not await channels_ok(
        callback.from_user.id
    ):

        return await callback.answer(
            "❌ Hali barcha kanallarga "
            "obuna bo‘lmagansiz.",
            show_alert=True
        )

    inviter = await finalize_referral(
        callback.from_user.id
    )

    if inviter:

        try:

            await bot.send_message(
                inviter,
                "🎉 Yangi referral!\n"
                "Sizga <b>+1 referral</b> qo‘shildi."
            )

        except Exception:

            pass

    await callback.message.edit_text(
        await get_setting(
            "start_text"
        ),
        reply_markup=await brands_kb()
    )

    await callback.answer(
        "✅ Tasdiqlandi"
    )


# ============================================================
# HOME
# ============================================================

@dp.callback_query(
    F.data == "home"
)
async def home(
    callback: CallbackQuery
):

    await callback.message.edit_text(
        await get_setting(
            "start_text"
        ),
        reply_markup=await brands_kb()
    )

    await callback.answer()


# ============================================================
# BRAND
# ============================================================

@dp.callback_query(
    F.data.startswith("brand:")
)
async def brand(
    callback: CallbackQuery
):

    if not await channels_ok(
        callback.from_user.id
    ):

        return await callback.answer(
            "🔒 Avval obuna bo‘ling.",
            show_alert=True
        )

    brand_id = int(
        callback.data.split(":")[1]
    )

    c = await db()

    row = await fetchone(c, 
        """
        SELECT name
        FROM brands
        WHERE id=?
        """,
        (brand_id,)
    )

    await c.close()

    if not row:

        return await callback.answer(
            "Brend topilmadi.",
            show_alert=True
        )

    await callback.message.edit_text(
        f"📱 <b>{row[0]}</b>\n\n"
        "Modelni tanlang:",
        reply_markup=await models_kb(
            brand_id
        )
    )

    await callback.answer()


# ============================================================
# MODEL
# ============================================================

@dp.callback_query(
    F.data.startswith("model:")
)
async def model(
    callback: CallbackQuery
):

    model_id = int(
        callback.data.split(":")[1]
    )

    c = await db()

    row = await fetchone(c, 
        """
        SELECT
            m.name,
            b.name

        FROM models m

        JOIN brands b
        ON b.id=m.brand_id

        WHERE m.id=?
        """,
        (model_id,)
    )

    await c.close()

    if not row:

        return await callback.answer(
            "Model topilmadi.",
            show_alert=True
        )

    await set_setting(
        f"sel:{callback.from_user.id}",
        str(model_id)
    )

    await callback.message.edit_text(
        f"⚙️ <b>{row[0]}</b>\n\n"
        "Sozlama darajasini tanlang:",
        reply_markup=await packages_kb()
    )

    await callback.answer()


# ============================================================
# PACKAGE
# ============================================================

@dp.callback_query(
    F.data.startswith("pkg:")
)
async def package(
    callback: CallbackQuery
):

    package_id = int(
        callback.data.split(":")[1]
    )

    model_id = await get_setting(
        f"sel:{callback.from_user.id}"
    )

    if not model_id:

        return await callback.answer(
            "Modelni qayta tanlang.",
            show_alert=True
        )

    c = await db()

    package_row = await fetchone(c, 
        """
        SELECT name,emoji,refs
        FROM packages
        WHERE id=?
        """,
        (package_id,)
    )

    model_row = await fetchone(c, 
        """
        SELECT
            m.name,
            b.name

        FROM models m

        JOIN brands b
        ON b.id=m.brand_id

        WHERE m.id=?
        """,
        (int(model_id),)
    )

    user_row = await fetchone(c, 
        """
        SELECT referrals
        FROM users
        WHERE id=?
        """,
        (callback.from_user.id,)
    )

    await c.close()

    if not package_row or not model_row:

        return await callback.answer(
            "Ma'lumot topilmadi.",
            show_alert=True
        )

    current_refs = user_row[0]

    required_refs = package_row[2]

    if current_refs < required_refs:

        need = required_refs - current_refs

        me = await bot.me()

        link = (
            f"https://t.me/"
            f"{me.username}"
            f"?start="
            f"{callback.from_user.id}"
        )

        return await callback.message.edit_text(

            f"❌ <b>Referral yetarli emas!</b>\n\n"

            f"📱 {model_row[1]} "
            f"{model_row[0]}\n"

            f"{package_row[1]} "
            f"Paket: <b>{package_row[0]}</b>\n\n"

            f"👥 Sizda: <b>{current_refs}</b>\n"
            f"🎯 Kerak: <b>{required_refs}</b>\n"
            f"➕ Yana kerak: <b>{need}</b>\n\n"

            f"🔗 <b>Referral linkingiz:</b>\n"
            f"{link}\n\n"

            "Do‘stlaringiz ushbu link orqali "
            "kirib, majburiy kanallarga "
            "obuna bo‘lgandan keyin referral "
            "hisoblanadi.",

            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Orqaga",
                            callback_data="home"
                        )
                    ]
                ]
            )
        )

    # ========================================================
    # CODE
    # ========================================================

    code = (
        "RVX-"
        +
        "".join(
            secrets.choice(
                string.ascii_uppercase
                + string.digits
            )
            for _ in range(10)
        )
    )

    c = await db()

    result = await c.execute(
        """
        UPDATE users

        SET referrals=referrals-?

        WHERE id=?
        AND referrals>=?
        """,
        (
            required_refs,
            callback.from_user.id,
            required_refs
        )
    )

    if result.rowcount != 1:

        await c.close()

        return await callback.answer(
            "❌ Referral yetarli emas.",
            show_alert=True
        )

    await c.execute(
        """
        INSERT INTO codes
        (
            code,
            user_id,
            brand,
            model,
            package,
            refs_used,
            created_at
        )

        VALUES(?,?,?,?,?,?,?)
        """,
        (
            code,
            callback.from_user.id,
            model_row[1],
            model_row[0],
            package_row[0],
            required_refs,
            datetime.now(
                timezone.utc
            ).isoformat()
        )
    )

    await c.commit()

    await c.close()

    support = await get_setting(
        "support",
        SUPPORT
    )

    await callback.message.edit_text(

        f"🎉 <b>SO‘ROV TASDIQLANDI!</b>\n\n"

        f"📱 Telefon:\n"
        f"<b>{model_row[1]} "
        f"{model_row[0]}</b>\n\n"

        f"{package_row[1]} "
        f"Paket: <b>{package_row[0]}</b>\n"

        f"👥 Sarflandi: "
        f"<b>{required_refs} referral</b>\n\n"

        f"🔐 <b>MAXFIY KOD</b>\n\n"

        f"<code>{code}</code>\n\n"

        "⚠️ Ushbu kod bir martalik.\n\n"

        f"📩 Kodni <b>{support}</b> ga yuboring.\n\n"

        "Admin kodni tekshiradi va "
        "sizga to‘liq nastroykani beradi."
    )

    await callback.answer(
        "✅ Kod yaratildi."
    )


# ============================================================
# REFERRAL
# ============================================================

@dp.callback_query(
    F.data == "myref"
)
async def myref(
    callback: CallbackQuery
):

    c = await db()

    row = await fetchone(c, 
        """
        SELECT referrals
        FROM users
        WHERE id=?
        """,
        (callback.from_user.id,)
    )

    await c.close()

    me = await bot.me()

    refs = row[0] if row else 0

    link = (
        f"https://t.me/"
        f"{me.username}"
        f"?start="
        f"{callback.from_user.id}"
    )

    await callback.message.edit_text(

        f"🎁 <b>REFERRAL MARKAZ</b>\n\n"

        f"👥 Sizning referral: "
        f"<b>{refs}</b>\n\n"

        f"🔗 Sizning linkingiz:\n"
        f"{link}\n\n"

        "⚡ Referral faqat yangi "
        "foydalanuvchi majburiy obunani "
        "bajargandan keyin hisoblanadi.",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="home"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# HELP
# ============================================================

@dp.callback_query(
    F.data == "help"
)
async def help_button(
    callback: CallbackQuery
):

    support = await get_setting(
        "support",
        SUPPORT
    )

    await callback.message.edit_text(

        "🆘 <b>YORDAM</b>\n\n"

        "Botdan foydalanish:\n"
        "1️⃣ Telefon brendini tanlang\n"
        "2️⃣ Modelni tanlang\n"
        "3️⃣ Paketni tanlang\n"
        "4️⃣ Kerakli referralni yig‘ing\n"
        "5️⃣ Maxfiy kodni oling\n"
        "6️⃣ Kodni admin supportga yuboring\n\n"

        f"📩 Support: {support}",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="home"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# CUSTOM SETTINGS
# ============================================================

@dp.callback_query(
    F.data == "custom"
)
async def custom(
    callback: CallbackQuery
):

    support = await get_setting(
        "support",
        SUPPORT
    )

    await callback.message.edit_text(

        "⚙️ <b>MAXSUS SOZLAMA</b>\n\n"

        "Telefoningiz yoki modelingiz "
        "ro‘yxatda bo‘lmasa, supportga "
        "murojaat qiling.\n\n"

        f"📩 {support}",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="home"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN COMMAND
# ============================================================

@dp.message(
    Command("admin")
)
async def admin(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return await message.answer(
            "⛔ Ruxsat yo‘q."
        )

    await message.answer(

        "👑 <b>RUZVIX CONTROL CENTER</b>\n\n"

        "Barcha boshqaruv shu yerda.",

        reply_markup=await admin_kb()
    )


# ============================================================
# ADMIN STATS
# ============================================================

@dp.callback_query(
    F.data == "astats"
)
async def admin_stats(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    c = await db()

    users = await fetchone(c, 
        "SELECT COUNT(*) FROM users"
    )

    active = await fetchone(c, 
        """
        SELECT COUNT(*)
        FROM users
        WHERE banned=0
        """
    )

    refs = await fetchone(c, 
        """
        SELECT COALESCE(
            SUM(referrals),0
        )
        FROM users
        """
    )

    codes = await fetchone(c, 
        "SELECT COUNT(*) FROM codes"
    )

    used = await fetchone(c, 
        """
        SELECT COUNT(*)
        FROM codes
        WHERE used=1
        """
    )

    await c.close()

    await callback.message.edit_text(

        "📊 <b>STATISTIKA</b>\n\n"

        f"👥 Users: <b>{users[0]}</b>\n"
        f"🟢 Active: <b>{active[0]}</b>\n"
        f"🎁 Referral qoldiq: <b>{refs[0]}</b>\n"
        f"🔐 Codes: <b>{codes[0]}</b>\n"
        f"✅ Used: <b>{used[0]}</b>",

        reply_markup=await admin_kb()
    )

    await callback.answer()


# ============================================================
# ADMIN USERS
# ============================================================

@dp.callback_query(
    F.data == "users"
)
async def admin_users(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT
            id,
            username,
            referrals,
            banned

        FROM users

        ORDER BY last_seen DESC

        LIMIT 20
        """
    )

    await c.close()

    text = "👥 <b>USERS</b>\n\n"

    for i, row in enumerate(
        rows,
        start=1
    ):

        status = (
            "🚫"
            if row[3]
            else
            "🟢"
        )

        text += (
            f"{i}. "
            f"<code>{row[0]}</code> "
            f"@{row[1] or '-'} "
            f"| REF: {row[2]} "
            f"| {status}\n"
        )

    await callback.message.edit_text(

        text,

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin",
                        callback_data="adminhome"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN PHONES
# ============================================================

@dp.callback_query(
    F.data == "phones"
)
async def phones(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    c = await db()

    brands = await fetchall(c, 
        """
        SELECT id,name,active
        FROM brands
        ORDER BY id
        """
    )

    models = await fetchall(c, 
        """
        SELECT
            m.id,
            b.name,
            m.name,
            m.active

        FROM models m

        JOIN brands b
        ON b.id=m.brand_id

        ORDER BY b.id,m.id

        LIMIT 50
        """
    )

    await c.close()

    text = "📱 <b>TELEFON BOSHQARUVI</b>\n\n"

    text += "🏷 <b>BRENDLAR</b>\n"

    for row in brands:

        text += (
            f"{row[0]} — "
            f"{row[1]} "
            f"{'🟢' if row[2] else '🔴'}\n"
        )

    text += "\n📲 <b>MODELLAR</b>\n"

    for row in models:

        text += (
            f"{row[0]} — "
            f"{row[1]} / "
            f"{row[2]} "
            f"{'🟢' if row[3] else '🔴'}\n"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="➕ Brend",
                    callback_data="addbrand"
                ),

                InlineKeyboardButton(
                    text="➕ Model",
                    callback_data="addmodel"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🗑 Brend",
                    callback_data="delbrand"
                ),

                InlineKeyboardButton(
                    text="🗑 Model",
                    callback_data="delmodel"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Admin",
                    callback_data="adminhome"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# ADD BRAND
# ============================================================

@dp.callback_query(
    F.data == "addbrand"
)
async def add_brand(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await state.set_state(
        Form.brand
    )

    await callback.message.answer(
        "➕ Yangi brend nomini yuboring.\n\n"
        "Masalan:\n"
        "<code>OnePlus</code>"
    )

    await callback.answer()


@dp.message(
    Form.brand
)
async def save_brand(
    message: Message,
    state: FSMContext
):

    name = message.text.strip()

    c = await db()

    await c.execute(
        """
        INSERT OR IGNORE INTO brands(name)
        VALUES(?)
        """,
        (name,)
    )

    await c.commit()

    await c.close()

    await state.clear()

    await audit(
        message.from_user.id,
        "add_brand",
        name
    )

    await message.answer(
        "✅ Brend qo‘shildi."
    )


# ============================================================
# ADD MODEL
# ============================================================

@dp.callback_query(
    F.data == "addmodel"
)
async def add_model(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await state.set_state(
        Form.model
    )

    await callback.message.answer(

        "➕ Model qo‘shish\n\n"

        "Format:\n"
        "<code>BRAND_ID | Model nomi</code>\n\n"

        "Masalan:\n"
        "<code>1 | iPhone 17 Pro Max</code>"
    )

    await callback.answer()


@dp.message(
    Form.model
)
async def save_model(
    message: Message,
    state: FSMContext
):

    try:

        brand_id, name = [
            x.strip()
            for x in message.text.split(
                "|",
                1
            )
        ]

        brand_id = int(
            brand_id
        )

    except Exception:

        return await message.answer(
            "❌ Format noto‘g‘ri."
        )

    c = await db()

    await c.execute(
        """
        INSERT INTO models
        (
            brand_id,
            name
        )
        VALUES(?,?)
        """,
        (
            brand_id,
            name
        )
    )

    await c.commit()

    await c.close()

    await state.clear()

    await audit(
        message.from_user.id,
        "add_model",
        name
    )

    await message.answer(
        "✅ Model qo‘shildi."
    )


# ============================================================
# DELETE BRAND
# ============================================================

@dp.callback_query(
    F.data == "delbrand"
)
async def delete_brand_help(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await callback.message.answer(
        "🗑 Brendni yashirish:\n\n"
        "<code>/delbrand BRAND_ID</code>"
    )

    await callback.answer()


@dp.message(
    Command("delbrand")
)
async def delete_brand(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        return await message.answer(
            "/delbrand BRAND_ID"
        )

    brand_id = int(
        parts[1]
    )

    c = await db()

    await c.execute(
        """
        UPDATE brands
        SET active=0
        WHERE id=?
        """,
        (brand_id,)
    )

    await c.commit()

    await c.close()

    await audit(
        message.from_user.id,
        "delete_brand",
        str(brand_id)
    )

    await message.answer(
        "✅ Brend yashirildi."
    )


# ============================================================
# DELETE MODEL
# ============================================================

@dp.callback_query(
    F.data == "delmodel"
)
async def delete_model_help(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await callback.message.answer(
        "🗑 Modelni yashirish:\n\n"
        "<code>/delmodel MODEL_ID</code>"
    )

    await callback.answer()


@dp.message(
    Command("delmodel")
)
async def delete_model(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        return await message.answer(
            "/delmodel MODEL_ID"
        )

    model_id = int(
        parts[1]
    )

    c = await db()

    await c.execute(
        """
        UPDATE models
        SET active=0
        WHERE id=?
        """,
        (model_id,)
    )

    await c.commit()

    await c.close()

    await message.answer(
        "✅ Model yashirildi."
    )


# ============================================================
# PACKAGES
# ============================================================

@dp.callback_query(
    F.data == "packs"
)
async def packages_admin(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT
            id,
            emoji,
            name,
            refs,
            active

        FROM packages

        ORDER BY sort,id
        """
    )

    await c.close()

    text = "💎 <b>PAKETLAR</b>\n\n"

    for row in rows:

        text += (
            f"{row[0]}. "
            f"{row[1]} "
            f"<b>{row[2]}</b> — "
            f"{row[3]} referral "
            f"{'🟢' if row[4] else '🔴'}\n"
        )

    text += (
        "\n✏️ O‘zgartirish:\n"
        "<code>/setpack ID | Nomi | Emoji | Referral</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin",
                        callback_data="adminhome"
                    )
                ]
            ]
        )
    )

    await callback.answer()


@dp.message(
    Command("setpack")
)
async def set_package(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return

    try:

        data = message.text.split(
            maxsplit=1
        )[1]

        pid, name, emoji, refs = [
            x.strip()
            for x in data.split(
                "|",
                3
            )
        ]

        pid = int(pid)
        refs = int(refs)

    except Exception:

        return await message.answer(
            "/setpack ID | Nomi | Emoji | Referral"
        )

    c = await db()

    await c.execute(
        """
        UPDATE packages

        SET name=?,
            emoji=?,
            refs=?

        WHERE id=?
        """,
        (
            name,
            emoji,
            refs,
            pid
        )
    )

    await c.commit()

    await c.close()

    await audit(
        message.from_user.id,
        "set_package",
        str(pid)
    )

    await message.answer(
        "✅ Paket yangilandi."
    )


# ============================================================
# REQUIRED CHANNELS ADMIN
# ============================================================

@dp.callback_query(
    F.data == "channels"
)
async def admin_channels(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT
            id,
            chat_id,
            title,
            active

        FROM channels
        """
    )

    await c.close()

    text = "📢 <b>MAJBURIY OBUNA</b>\n\n"

    if not rows:

        text += "Hozircha kanal yo‘q.\n"

    for row in rows:

        text += (
            f"{row[0]}. "
            f"{row[2] or row[1]} — "
            f"{'🟢' if row[3] else '🔴'}\n"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="➕ Kanal",
                    callback_data="addchannel"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🗑 Kanal",
                    callback_data="delchannel"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Admin",
                    callback_data="adminhome"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(
    F.data == "addchannel"
)
async def add_channel(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await state.set_state(
        Form.channel
    )

    await callback.message.answer(

        "📢 Kanal username yoki ID yuboring.\n\n"

        "Masalan:\n"
        "<code>@mychannel</code>\n\n"

        "Bot kanalga admin bo‘lishi kerak."
    )

    await callback.answer()


@dp.message(
    Form.channel
)
async def save_channel(
    message: Message,
    state: FSMContext
):

    chat_id = message.text.strip()

    title = chat_id

    try:

        chat = await bot.get_chat(
            chat_id
        )

        title = chat.title or chat_id

    except Exception:

        pass

    c = await db()

    await c.execute(
        """
        INSERT OR IGNORE INTO channels
        (
            chat_id,
            title
        )
        VALUES(?,?)
        """,
        (
            chat_id,
            title
        )
    )

    await c.commit()

    await c.close()

    await state.clear()

    await message.answer(
        "✅ Majburiy kanal qo‘shildi."
    )


@dp.callback_query(
    F.data == "delchannel"
)
async def delete_channel_help(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await callback.message.answer(
        "🗑 Kanalni o‘chirish:\n\n"
        "<code>/delchannel CHANNEL_ID</code>"
    )

    await callback.answer()


@dp.message(
    Command("delchannel")
)
async def delete_channel(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        return await message.answer(
            "/delchannel CHANNEL_ID"
        )

    channel_id = int(
        parts[1]
    )

    c = await db()

    await c.execute(
        """
        UPDATE channels
        SET active=0
        WHERE id=?
        """,
        (channel_id,)
    )

    await c.commit()

    await c.close()

    await message.answer(
        "✅ Kanal olib tashlandi."
    )


# ============================================================
# BROADCAST / REKLAMA
# ============================================================

@dp.callback_query(
    F.data == "broadcast"
)
async def broadcast_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    await state.set_state(
        Form.broadcast
    )

    await callback.message.answer(

        "📣 <b>REKLAMA YUBORISH</b>\n\n"

        "Endi yuborgan xabaringiz "
        "barcha bloklanmagan userlarga "
        "copy qilib yuboriladi.\n\n"

        "Matn, rasm, video yoki boshqa "
        "Telegram xabar yuborishingiz mumkin."
    )

    await callback.answer()


@dp.message(
    Form.broadcast
)
async def do_broadcast(
    message: Message,
    state: FSMContext
):

    c = await db()

    users = await fetchall(c, 
        """
        SELECT id
        FROM users
        WHERE banned=0
        """
    )

    await c.close()

    success = 0
    failed = 0

    for row in users:

        try:

            await bot.copy_message(
                chat_id=row[0],
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )

            success += 1

        except Exception:

            failed += 1

        await asyncio.sleep(
            0.04
        )

    await state.clear()

    await message.answer(
        "📣 <b>REKLAMA YAKUNLANDI</b>\n\n"
        f"✅ Yetkazildi: {success}\n"
        f"❌ Yetkazilmadi: {failed}"
    )


# ============================================================
# CODES ADMIN
# ============================================================

@dp.callback_query(
    F.data == "codes"
)
async def admin_codes(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    c = await db()

    rows = await fetchall(c, 
        """
        SELECT
            code,
            user_id,
            brand,
            model,
            package,
            used

        FROM codes

        ORDER BY id DESC

        LIMIT 30
        """
    )

    await c.close()

    text = "🔐 <b>SO‘NGGI KODLAR</b>\n\n"

    for row in rows:

        text += (
            f"<code>{row[0]}</code>\n"
            f"👤 {row[1]}\n"
            f"📱 {row[2]} {row[3]}\n"
            f"💎 {row[4]}\n"
            f"{'✅ Ishlatilgan' if row[5] else '🟢 Yangi'}\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin",
                        callback_data="adminhome"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# REFERRAL ADMIN
# ============================================================

@dp.callback_query(
    F.data == "refs"
)
async def admin_refs(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    await callback.message.edit_text(

        "🎁 <b>REFERRAL BOSHQARUVI</b>\n\n"

        "Userga referral qo‘shish:\n"
        "<code>+123456789 | 5</code>\n\n"

        "Referral ayirish:\n"
        "<code>-123456789 | 2</code>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕/➖ Referral",
                        callback_data="editref"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="⬅️ Admin",
                        callback_data="adminhome"
                    )
                ]
            ]
        )
    )

    await callback.answer()


@dp.callback_query(
    F.data == "editref"
)
async def edit_ref(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        Form.add_ref
    )

    await callback.message.answer(
        "Format:\n"
        "<code>USER_ID | +5</code>\n\n"
        "yoki\n\n"
        "<code>USER_ID | -5</code>"
    )

    await callback.answer()


@dp.message(
    Form.add_ref
)
async def save_ref(
    message: Message,
    state: FSMContext
):

    try:

        user_id, amount = [
            x.strip()
            for x in message.text.split(
                "|",
                1
            )
        ]

        user_id = int(
            user_id
        )

        amount = int(
            amount
        )

    except Exception:

        return await message.answer(
            "❌ Format noto‘g‘ri."
        )

    c = await db()

    await c.execute(
        """
        UPDATE users

        SET referrals=
            MAX(0,referrals+?)

        WHERE id=?
        """,
        (
            amount,
            user_id
        )
    )

    await c.commit()

    await c.close()

    await state.clear()

    await audit(
        message.from_user.id,
        "edit_referral",
        f"{user_id}:{amount}"
    )

    await message.answer(
        "✅ Referral o‘zgartirildi."
    )


# ============================================================
# BAN
# ============================================================

@dp.callback_query(
    F.data == "banmenu"
)
async def ban_menu(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await callback.message.edit_text(

        "🚫 <b>BAN BOSHQARUVI</b>\n\n"

        "Ban qilish:\n"
        "<code>/ban USER_ID</code>\n\n"

        "Unban:\n"
        "<code>/unban USER_ID</code>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin",
                        callback_data="adminhome"
                    )
                ]
            ]
        )
    )

    await callback.answer()


@dp.message(
    Command("ban")
)
async def ban_user(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        return await message.answer(
            "/ban USER_ID"
        )

    user_id = int(
        parts[1]
    )

    c = await db()

    await c.execute(
        """
        UPDATE users
        SET banned=1
        WHERE id=?
        """,
        (user_id,)
    )

    await c.commit()

    await c.close()

    await message.answer(
        "🚫 User bloklandi."
    )


@dp.message(
    Command("unban")
)
async def unban_user(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        return await message.answer(
            "/unban USER_ID"
        )

    user_id = int(
        parts[1]
    )

    c = await db()

    await c.execute(
        """
        UPDATE users
        SET banned=0
        WHERE id=?
        """,
        (user_id,)
    )

    await c.commit()

    await c.close()

    await message.answer(
        "✅ User blokdan chiqarildi."
    )


# ============================================================
# TEXT SETTINGS
# ============================================================

@dp.callback_query(
    F.data == "texts"
)
async def admin_texts(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await callback.message.edit_text(

        "📝 <b>MATNLAR</b>\n\n"
        "Bot matnlarini shu yerdan boshqarish mumkin.",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="✏️ Start matni",
                        callback_data="setstart"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="✏️ Support",
                        callback_data="setsupport"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="⬅️ Admin",
                        callback_data="adminhome"
                    )
                ]
            ]
        )
    )

    await callback.answer()


@dp.callback_query(
    F.data == "setstart"
)
async def set_start_text(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        Form.text
    )

    await state.update_data(
        key="start_text"
    )

    await callback.message.answer(
        "📝 Yangi start matnini yuboring."
    )

    await callback.answer()


@dp.callback_query(
    F.data == "setsupport"
)
async def set_support(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        Form.text
    )

    await state.update_data(
        key="support"
    )

    await callback.message.answer(
        "📩 Yangi support username yuboring.\n\n"
        "Masalan: @ruzvix"
    )

    await callback.answer()


@dp.message(
    Form.text
)
async def save_text(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    key = data.get(
        "key",
        "start_text"
    )

    await set_setting(
        key,
        message.text
    )

    await state.clear()

    await message.answer(
        "✅ Sozlama saqlandi."
    )


# ============================================================
# ADMIN SETTINGS
# ============================================================

@dp.callback_query(
    F.data == "settings"
)
async def admin_settings(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return

    await callback.message.edit_text(

        "⚙️ <b>GLOBAL SOZLAMALAR</b>\n\n"

        "🔐 Adminlar Render ENV orqali.\n"
        "📢 Majburiy obuna — alohida bo‘limda.\n"
        "📱 Telefonlar — alohida bo‘limda.\n"
        "💎 Paketlar — alohida bo‘limda.\n"
        "📣 Reklama — alohida bo‘limda.\n"
        "🎁 Referral — alohida bo‘limda.\n\n"

        "Referral faqat yangi user "
        "majburiy obunani bajargandan "
        "keyin hisoblanadi.",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin",
                        callback_data="adminhome"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN HOME
# ============================================================

@dp.callback_query(
    F.data == "adminhome"
)
async def admin_home(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔",
            show_alert=True
        )

    await callback.message.edit_text(
        "👑 <b>RUZVIX CONTROL CENTER</b>",
        reply_markup=await admin_kb()
    )

    await callback.answer()


# ============================================================
# HEALTH SERVER
# ============================================================

polling_task = None


@app.on_event("startup")
async def startup():

    global polling_task

    await init_db()

    if not bot:

        log.error(
            "BOT_TOKEN ENV yo‘q."
        )

        return

    me = await bot.get_me()

    log.info(
        "Bot started: @%s",
        me.username
    )

    polling_task = asyncio.create_task(
        dp.start_polling(
            bot,
            allowed_updates=
            dp.resolve_used_update_types()
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

    return {
        "status": "ok",
        "service": "ruzvix-sensi-bot"
    }


@app.get("/health")
async def health():

    return {
        "status": "healthy"
    }


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        )
    )
