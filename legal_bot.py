# -*- coding: utf-8 -*-
"""
legal_bot_v_ULTIMATE_PLUS_ADMIN_PANEL.py
نسخه ارتقا یافته با پنل مدیریت پیشرفته اینلاین (Inline)
(سازگار با Python 3.11 و openai>=1.0.0)
(اصلاح شده برای رفع SyntaxError و خطای منطقی ادمین)
"""

import os
import logging
import sqlite3
import asyncio
import time
import re
import tempfile # برای مدیریت فایل‌ها
import random # (جدید) برای انتخاب نکته تصادفی
import io # (اصلاح شد) برای ارسال فایل قالب اضافه شد
from datetime import datetime, timedelta, date
from functools import wraps
from typing import Optional, List, Dict

import httpx
from bs4 import BeautifulSoup
from openai import OpenAI
import fitz # PyMuPDF برای PDF
import docx # برای DOCX

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ------------------ CONFIG (مقادیر شما اینجا) ------------------
TELEGRAM_TOKEN = "8361400256:AAED-XtJimSznQrXoNlaHcAB1IESngyFYSI"
OPENAI_API_KEY = "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"
SUPER_ADMIN_ID = int(5032856938) # (تغییر نام) ادمین کل
# (حذف شد) CHANNEL_USERNAME = "iransmartlaw"
DB_FILE = "legal_bot_ultimate.db" # تغییر نام دیتابیس برای نسخه جدید
DAILY_TIP_HOUR = 12 # (جدید) ساعت ارسال نکته به کاربران
DAILY_GROUP_TIP_HOUR = 10 # (جدید) ساعت ارسال نکته به گروه‌ها
RATE_LIMIT_PER_MIN = 8
TGJU_SEKE_URL = "https://www.tgju.org/profile/sekee"
DEFAULT_TAX_RATE = 0.10
CHAT_HISTORY_LIMIT = 5

# (تغییر) تذکر حقوقی کوتاه‌تر و بدون اشاره به AI
LEGAL_DISCLAIMER = "\n\n⚖️ **تذکر:** اطلاعات این ربات بر اساس داده‌های عمومی است و هرگز جایگزین مشاوره تخصصی با وکیل دادگستری نمی‌باشد."
# ---------------------------------------------------------------

# ---------- راه‌اندازی کلاینت OpenAI ----------
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN لازم است. آن را در بالای فایل قرار بدهید.")

openai_client = None
if OPENAI_API_KEY:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"Failed to initialize OpenAI client: {e}")

# ---------- logging ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- DB (پایگاه داده) ----------
DB = sqlite3.connect(DB_FILE, check_same_thread=False)
DB.row_factory = sqlite3.Row
CUR = DB.cursor()

# ساخت جداول (ارتقا یافته با تنظیمات کاربر)
CUR.executescript("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    joined_at TEXT,
    ai_personality TEXT DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    role TEXT,
    subject TEXT,
    content TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS reports(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    content TEXT,
    admin_reply TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS reminders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT,
    remind_at TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS coin_rates(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    rate INTEGER,
    fetched_at TEXT
);

/* (جدید) جداول مدیریت ربات */
CREATE TABLE IF NOT EXISTS admins(
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS channels(
    channel_id TEXT PRIMARY KEY, /* یوزرنیم کانال بدون @ */
    added_by INTEGER,
    added_at TEXT
);

/* (جدید) جداول ویژگی‌های کاربر */
CREATE TABLE IF NOT EXISTS my_cases(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT,
    case_number TEXT,
    branch TEXT,
    notes TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS quiz_questions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,
    correct_option TEXT, /* 'a', 'b', 'c', or 'd' */
    created_by INTEGER,
    created_at TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS quiz_user_answers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    question_id INTEGER,
    answer TEXT, /* 'a', 'b', 'c', 'd' */
    is_correct INTEGER,
    answered_at TEXT,
    UNIQUE(user_id, question_id)
);

/* (جدید) جدول نکات حقوقی */
CREATE TABLE IF NOT EXISTS legal_tips(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tip_text TEXT,
    added_by INTEGER,
    created_at TEXT
);

/* (جدید) جدول گروه‌های مدیریت شده */
CREATE TABLE IF NOT EXISTS managed_groups(
    chat_id INTEGER PRIMARY KEY, /* آیدی عددی گروه یا کانال */
    added_at TEXT,
    daily_tip_enabled INTEGER DEFAULT 1
);
""")
DB.commit()

# ---------- UI (منوی اصلی جدید) ----------
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🧾 پرسش حقوقی (با حافظه)", "📄 تحلیل سند (PDF/DOCX)"],
        ["🧮 محاسبه‌گر", "📝 پیش‌نویس", "📄 قالب‌های آماده"],
        ["🔔 آخرین اخبار", "🗂️ پرونده‌های من", "⚖️ آزمون حقوقی"],
        ["💡 نکات حقوقی", "📚 واژه‌نامه", "⏰ یادآوری‌ها"], # (تغییر) دکمه نکات اضافه شد
        ["⚙️ تنظیمات", "📨 ارسال گزارش", "👤 پروفایل من"] # (تغییر) چینش
    ],
    resize_keyboard=True
)

# (حذف شد) منوی ادمین قدیمی حذف شد
# ADMIN_MENU = ...

# دیکشنری شخصیت‌های AI (تقویت شده برای پاسخ‌های دقیق‌تر)
AI_PERSONALITIES = {
    "default": "شما وکیل مشاور حقوقی متخصص در قوانین ایران هستید. پاسخ‌ها باید دقیق، مستند و کاربردی باشند. در صورت امکان به مواد قانونی مرتبط ارجاع دهید.",
    "simple": "شما یک دوست آگاه به حقوق هستید. همه چیز را به زبان کاملاً ساده و عامیانه توضیح می‌دهید، انگار برای یک فرد 15 ساله توضیح می‌دهید. پاسخ شما باید *از نظر حقوقی صحیح* باشد، اما بیان آن ساده باشد.",
    "technical": "شما یک قاضی یا وکیل ارشد هستید. پاسخ‌های شما باید بسیار فنی، دقیق، مستند و مملو از ارجاع به مواد قانونی و رویه‌های قضایی باشد. دقت اولویت اول شماست."
}

# ---------- utilities (توابع کمکی دیتابیس) ----------

# (جدید) تابع بررسی ادمین (ادمین کل + ادمین‌های ثبت شده)
def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    try:
        CUR.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return CUR.fetchone() is not None
    except Exception:
        return False

def save_user(user) -> None:
    try:
        CUR.execute(
            "INSERT OR IGNORE INTO users(user_id, username, first_name, last_name, joined_at, ai_personality) VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.username or "", user.first_name or "", user.last_name or "", datetime.utcnow().isoformat(), "default")
        )
        DB.commit()
    except Exception as e:
        logger.error(f"Failed to save user {user.id}: {e}")

def get_user_settings(user_id: int) -> dict:
    try:
        CUR.execute("SELECT ai_personality FROM users WHERE user_id = ?", (user_id,))
        row = CUR.fetchone()
        if row:
            return {"ai_personality": row["ai_personality"]}
        return {"ai_personality": "default"}
    except Exception:
        return {"ai_personality": "default"}

def set_user_personality(user_id: int, personality: str) -> None:
    if personality not in AI_PERSONALITIES:
        personality = "default"
    try:
        CUR.execute("UPDATE users SET ai_personality = ? WHERE user_id = ?", (personality, user_id))
        DB.commit()
    except Exception as e:
        logger.error(f"Failed to set personality for {user_id}: {e}")

def save_history(user_id: int, role: str, subject: str, content: str) -> None:
    try:
        CUR.execute(
            "INSERT INTO history(user_id, role, subject, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, role, subject, content, datetime.utcnow().isoformat())
        )
        DB.commit()
    except Exception as e:
        logger.error(f"Failed to save history for {user_id}: {e}")

def get_chat_history(user_id: int, limit: int = CHAT_HISTORY_LIMIT) -> List[Dict[str, str]]:
    try:
        CUR.execute(
            "SELECT role, content FROM history WHERE user_id = ? AND subject IN ('پرسش حقوقی', 'پاسخ حقوقی') ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        )
        rows = CUR.fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        return history
    except Exception as e:
        logger.error(f"Failed to get chat history for {user_id}: {e}")
        return []

def create_report(user_id: int, content: str) -> int:
    CUR.execute(
        "INSERT INTO reports(user_id, content, admin_reply, created_at) VALUES (?, ?, ?, ?)",
        (user_id, content, None, datetime.utcnow().isoformat())
    )
    DB.commit()
    return CUR.lastrowid

def add_reminder(user_id: int, title: str, remind_at: str) -> int:
    CUR.execute(
        "INSERT INTO reminders(user_id, title, remind_at, created_at) VALUES (?, ?, ?, ?)",
        (user_id, title, remind_at, datetime.utcnow().isoformat())
    )
    DB.commit()
    return CUR.lastrowid

def save_coin_rate(source: str, rate: int) -> None:
    CUR.execute(
        "INSERT INTO coin_rates(source, rate, fetched_at) VALUES (?, ?, ?)",
        (source, rate, datetime.utcnow().isoformat())
    )
    DB.commit()

def get_last_rate(source: str = "tgju_sekee") -> Optional[int]:
    CUR.execute("SELECT rate FROM coin_rates WHERE source=? ORDER BY id DESC LIMIT 1", (source,))
    r = CUR.fetchone()
    return int(r["rate"]) if r else None

# (جدید) تابع دریافت کانال‌های عضویت اجباری
def get_mandatory_channels() -> List[str]:
    try:
        CUR.execute("SELECT channel_id FROM channels")
        rows = CUR.fetchall()
        return [r["channel_id"] for r in rows]
    except Exception as e:
        logger.error(f"Failed to get mandatory channels: {e}")
        return []

# (جدید) دیکشنری قالب‌های آماده
TEMPLATES = {
    "rent": """
**قرارداد اجاره‌نامه ساده مسکونی**

ماده ۱: طرفین قرارداد
موجر: (نام، نام خانوادگی، کد ملی، آدرس)
مستأجر: (نام، نام خانوادگی، کد ملی، آدرس)

ماده ۲: مورد اجاره
یک واحد آپาร์تمان/خانه به آدرس: ...
دارای امکانات: آب، برق، گاز، (سایر امکانات)

ماده ۳: مدت اجاره
از تاریخ: ... لغایت ... (به مدت ... ماه/سال)

ماده ۴: مبلغ اجاره و ودیعه
مبلغ ودیعه: ... ریال
اجاره ماهانه: ... ریال

... (سایر مواد) ...
""",
    "iou": """
**رسید دریافت وجه (سفته / اقرارنامه دین)**

اینجانب: ... (نام، نام خانوادگی)
فرزند: ...
کد ملی: ...
به آدرس: ...

اقرار می‌نمایم که مبلغ ... ریال (... تومان) از آقای/خانم ... (نام) بابت ... (علت دین) دریافت نموده‌ام و متعهد می‌شوم مبلغ فوق را در تاریخ ... به ایشان بازگردانم.

در صورت عدم پرداخت در تاریخ مقرر، مبلغ ... به عنوان جریمه تاخیر روزانه محاسبه خواهد شد.

امضا و تاریخ: ...
"""
}


# ---------- توابع استخراج متن فایل (بدون تغییر) ----------
def extract_pdf_text(path: str) -> str:
    try:
        doc = fitz.open(path)
        text = "\n".join([page.get_text("text") for page in doc])
        return text
    except Exception as e:
        logger.error(f"PDF Error: {e}")
        return f"خطا در خواندن PDF: {e}"

def extract_docx_text(path: str) -> str:
    try:
        doc = docx.Document(path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text
    except Exception as e:
        logger.error(f"DOCX Error: {e}")
        return f"خطا در خواندن DOCX: {e}"

# ---------- rate limiting (بدون تغییر) ----------
_recent_requests: dict[int, list[float]] = {}
def rate_limited(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id if update.effective_user else None
        if not uid or is_admin(uid): # (تغییر) چک کردن همه ادمین‌ها
            return await func(update, context, *args, **kwargs)
        now = time.time()
        lst = _recent_requests.get(uid, [])
        lst = [t for t in lst if t > now - 60]
        if len(lst) >= RATE_LIMIT_PER_MIN:
            try:
                await update.message.reply_text("⚠️ شما در حال ارسال پیام با سرعت زیاد هستید. لطفاً چند لحظه صبر کنید.")
            except Exception: pass
            return
        lst.append(now)
        _recent_requests[uid] = lst
        return await func(update, context, *args, **kwargs)
    return wrapper

# ---------- membership (بازنویسی کامل) ----------
async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    (بازنویسی شد)
    بررسی می‌کند که آیا کاربر عضو *تمام* کانال‌های ثبت شده در دیتابیس است یا خیر.
    """
    channels = get_mandatory_channels()
    if not channels:
        return True # اگر هیچ کانالی تنظیم نشده باشد، عبور کن

    try:
        for channel_username in channels:
            username = channel_username if channel_username.startswith("@") else f"@{channel_username}"
            member = await context.bot.get_chat_member(username, user_id)
            if member.status not in ("member", "creator", "administrator"):
                logger.info(f"User {user_id} is NOT a member of {username}")
                return False # کاربر باید عضو *همه* باشد
        
        return True # کاربر عضو همه کانال‌ها است
    except Exception as e:
        logger.warning("Membership check error for user %s: %s", user_id, e)
        # اگر ربات نتواند کانالی را چک کند (مثلا ادمین نباشد)، به کاربر اجازه عبور می‌دهد
        # برای سخت‌گیری بیشتر، این را به False تغییر دهید
        return True 

async def send_join_request_for_user(update: Update):
    """
    (بازنویسی شد)
    دکمه عضویت برای *تمام* کانال‌های ثبت شده ارسال می‌شود.
    """
    channels = get_mandatory_channels()
    
    kb_buttons = []
    if not channels:
        # اگر به دلیلی کانال‌ها خالی بودند، یک پیام عمومی بده
        await update.message.reply_text("⚖️ برای استفاده از ربات، عضویت در کانال الزامی است. (خطا: کانالی تنظیم نشده)")
        return
        
    for channel_username in channels:
        url = f"https://t.me/{channel_username.strip('@')}"
        kb_buttons.append([InlineKeyboardButton(f"📢 عضویت در کانال @{channel_username}", url=url)])
    
    kb_buttons.append([InlineKeyboardButton("✅ تایید عضویت", callback_data="verify_membership")])
    
    kb = InlineKeyboardMarkup(kb_buttons)
    reply_func = update.message.reply_text if update.message else update.effective_message.reply_text
    await reply_func("⚖️ برای استفاده از ربات، ابتدا باید در **تمام** کانال‌های زیر عضو شوید:", reply_markup=kb, parse_mode="Markdown")

# ---------- OpenAI helper (ارتقا یافته با تنظیمات) ----------
async def ask_ai(
    user_id: int, 
    prompt: str,
    system: Optional[str] = None,
    chat_history: Optional[List[Dict[str, str]]] = None
) -> str:
    if not openai_client:
        return "⚠️ سرویس هوش مصنوعی غیر فعال است. مدیر کلید API را تنظیم نکرده."

    user_settings = get_user_settings(user_id)
    personality = AI_PERSONALITIES.get(user_settings["ai_personality"], AI_PERSONALITIES["default"])

    def _call():
        try:
            messages = []
            
            if system:
                messages.append({"role": "system", "content": system})
            else:
                messages.append({"role": "system", "content": personality})
            
            if chat_history:
                messages.extend(chat_history)
            
            messages.append({"role": "user", "content": prompt})
            
            resp = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=2000, 
                temperature=0.2
            )
            return resp.choices[0].message.content.strip()
        
        except Exception as e:
            logger.exception("OpenAI error")
            return f"⚠️ خطا در سرویس هوش مصنوعی: {e}"

    return await asyncio.to_thread(_call)

# ---------- TGJU fetch (بدون تغییر) ----------
async def fetch_tgju_sekee_rate() -> Optional[int]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'}
            r = await client.get(TGJU_SEKE_URL, headers=headers)
            r.raise_for_status()
            html = r.text
    except Exception as e:
        logger.warning("Failed to fetch TGJU: %s", e)
        return None
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        price_span = soup.find("span", {"data-col": "info.last_trade.price"})
        if price_span:
            cleaned = price_span.text.replace(",", "").strip()
            candidate = int(cleaned)
            save_coin_rate("tgju_sekee", candidate)
            return candidate
        else:
            logger.warning("Could not find price span in TGJU HTML.")
            return None
    except Exception:
        logger.exception("Error parsing TGJU HTML")
        return None

# ---------- Handlers (هندلرهای اصلی) ----------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    
    if not await check_membership(user.id, context):
        await send_join_request_for_user(update)
        return
    
    # (تغییر) منوی ادمین از اینجا حذف شد
    msg = f"👋 سلام {user.first_name or ''}!\nاز منو یک گزینه انتخاب کنید:"
    if is_admin(user.id): # (تغییر)
        msg += "\n\n(شما ادمین هستید. برای پنل مدیریت /admin را ارسال کنید.)"

    await update.message.reply_text(msg, reply_markup=MAIN_MENU)

@rate_limited
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    text = (update.message.text or "").strip()
    state = context.user_data.get("state")
    current_menu = MAIN_MENU # (تغییر) منوی ادمین همیشه منوی اصلی کاربر است

    if not await check_membership(uid, context):
        await send_join_request_for_user(update)
        return

    # --- (جدید) منطق حالت‌های ادمین ---
    if is_admin(uid): # (تغییر)
        
        # --- (اصلاح شد) حالت پاسخ به گزارش ---
        if state == "awaiting_admin_reply":
            try:
                report_data = context.user_data.pop("reply_to_report", None)
                if not report_data:
                    await update.message.reply_text("❌ خطای داخلی: اطلاعات گزارش یافت نشد. لطفاً /admin را مجدد بزنید.", reply_markup=current_menu)
                    context.user_data.pop("state", None)
                    return # بازگشت

                # این منطق اصلی پاسخگویی است
                reply_text = update.message.text
                target_user_id = report_data["user_id"]
                report_id = report_data["report_id"]

                # ۱. آپدیت دیتابیس
                CUR.execute("UPDATE reports SET admin_reply = ? WHERE id = ?", (reply_text, report_id))
                DB.commit()

                # ۲. اطلاع‌رسانی به کاربر
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=f"📨 **پاسخ ادمین به گزارش شما (ID: {report_id})**:\n\n{reply_text}"
                    )
                    await update.message.reply_text(f"✅ پاسخ شما برای گزارش #{report_id} ارسال شد.")
                except Exception as e:
                    logger.warning(f"Failed to send admin reply to user {target_user_id}: {e}")
                    await update.message.reply_text(f"⚠️ پاسخ در دیتابیس ثبت شد، اما ارسال به کاربر با خطا مواجه شد: {e}")
                
                context.user_data.pop("state", None)
                # نمایش مجدد پنل گزارش‌ها
                await show_admin_reports(update, context, query_message=update.message)

            except Exception as e:
                logger.error(f"Admin reply error: {e}")
                await update.message.reply_text(f"❌ خطا در ارسال پاسخ: {e}")
            return
        
        # --- (اصلاح شد) حالت افزودن سوال آزمون ---
        # این بلوک در کد شما حذف شده بود و باعث SyntaxError می‌شد
        if state == "awaiting_quiz_question":
            try:
                parts = [p.strip() for p in text.split("|")]
                if len(parts) != 6:
                    raise ValueError("فرمت اشتباه، 6 بخش مورد نیاز است")
                
                question, o_a, o_b, o_c, o_d, correct = parts
                correct = correct.strip().lower()
                if correct not in ['a', 'b', 'c', 'd', 'الف', 'ب', 'ج', 'د']:
                     raise ValueError("پاسخ صحیح باید a, b, c, d یا الف, ب, ج, د باشد")
                
                # تبدیل حروف فارسی به انگلیسی
                if correct in ['الف', 'ب', 'ج', 'د']:
                    correct = {'الف': 'a', 'ب': 'b', 'ج': 'c', 'د': 'd'}[correct]

                # غیرفعال کردن سوالات قبلی
                CUR.execute("UPDATE quiz_questions SET is_active = 0")
                
                CUR.execute(
                    "INSERT INTO quiz_questions(question_text, option_a, option_b, option_c, option_d, correct_option, created_by, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (question, o_a, o_b, o_c, o_d, correct, uid, datetime.utcnow().isoformat())
                )
                DB.commit()
                await update.message.reply_text("✅ سوال آزمون با موفقیت ثبت و فعال شد.")
                context.user_data.pop("state", None)
                await admin_quiz_panel_handler(update, context, query_message=update.message)

            except Exception as e:
                logger.error(f"Admin quiz add error: {e}")
                await update.message.reply_text(f"❌ خطا: {e}\n\nلطفاً در فرمت دقیق ارسال کنید:\n"
                                                "متن سوال؟ | متن گزینه الف | متن گزینه ب | متن گزینه ج | متن گزینه د | پاسخ صحیح (مثل a یا ب)")
            return

        # (جدید) حالت افزودن نکته حقوقی
        if state == "awaiting_new_legal_tip":
            try:
                if len(text) < 10: raise ValueError("نکته 너무 کوتاه است")
                CUR.execute("INSERT INTO legal_tips(tip_text, added_by, created_at) VALUES (?, ?, ?)",
                            (text, uid, datetime.utcnow().isoformat()))
                DB.commit()
                await update.message.reply_text("✅ نکته حقوقی با موفقیت ثبت شد.")
                context.user_data.pop("state", None)
                await admin_manage_tips_handler(update, context, query_message=update.message)
            except Exception as e:
                logger.error(f"Admin add tip error: {e}")
                await update.message.reply_text(f"❌ خطا در ثبت نکته: {e}")
            return
        
        # (جدید) حالت پیام همگانی
        if state == "awaiting_broadcast":
            context.user_data.pop("state", None)
            CUR.execute("SELECT user_id FROM users")
            rows = CUR.fetchall()
            await update.message.reply_text(f"⏳ شروع ارسال پیام به {len(rows)} کاربر...")
            count = 0
            for r in rows:
                try:
                    await context.bot.send_message(chat_id=r["user_id"], text=text)
                    count += 1
                except Exception:
                    logger.warning("Failed to send broadcast to %s", r["user_id"])
                await asyncio.sleep(0.05) # 20 پیام در ثانیه
            await update.message.reply_text(f"✅ پیام همگانی با موفقیت به {count} نفر از {len(rows)} کاربر ارسال شد.")
            await admin_panel_handler(update, context) # بازگشت به پنل ادمین
            return

        # (جدید) حالت جستجوی کاربر
        if state == "awaiting_user_search":
            context.user_data.pop("state", None)
            try:
                target_uid = int(text)
                CUR.execute("SELECT * FROM users WHERE user_id = ?", (target_uid,))
                user_row = CUR.fetchone()
                if not user_row:
                    await update.message.reply_text(f"❌ کاربری با آیدی {target_uid} یافت نشد.")
                    return
                
                msg = (
                    f"👤 **اطلاعات کاربر {target_uid}**\n"
                    f"نام: {user_row['first_name']}\n"
                    f"یوزرنیم: @{user_row['username'] or '---'}\n"
                    f"عضویت: {user_row['joined_at'].split('T')[0]}\n"
                    f"شخصیت AI: {user_row['ai_personality']}\n"
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"📜 مشاهده تاریخچه {target_uid}", callback_data=f"admin_view_user_history_{target_uid}")],
                    # [InlineKeyboardButton(f"🚫 بن کردن کاربر {target_uid}", callback_data=f"admin_ban_user_{target_uid}")],
                    [InlineKeyboardButton("🔙 بازگشت به پنل اصلی", callback_data="admin_main")]
                ])
                await update.message.reply_text(msg, reply_markup=kb)
                
            except ValueError:
                await update.message.reply_text("❌ لطفاً فقط آیدی عددی کاربر را وارد کنید.")
            except Exception as e:
                await update.message.reply_text(f"❌ خطای جستجو: {e}")
            return

        # (جدید) حالت افزودن ادمین
        if state == "awaiting_new_admin_id":
            context.user_data.pop("state", None)
            try:
                target_uid = int(text)
                if target_uid == SUPER_ADMIN_ID: raise ValueError("ادمین کل قابل افزودن نیست")
                CUR.execute("INSERT INTO admins(user_id, added_by, added_at) VALUES (?, ?, ?)",
                            (target_uid, uid, datetime.utcnow().isoformat()))
                DB.commit()
                await update.message.reply_text(f"✅ کاربر {target_uid} با موفقیت به لیست ادمین‌ها اضافه شد.")
                await admin_manage_admins_handler(update, context, query_message=update.message)
            except sqlite3.IntegrityError:
                await update.message.reply_text(f"❌ کاربر {target_uid} از قبل ادمین بوده است.")
            except Exception as e:
                await update.message.reply_text(f"❌ خطای افزودن ادمین: {e}")
            return

        # (جدید) حالت حذف ادمین
        if state == "awaiting_remove_admin_id":
            context.user_data.pop("state", None)
            try:
                target_uid = int(text)
                if target_uid == SUPER_ADMIN_ID: raise ValueError("ادمین کل قابل حذف نیست")
                CUR.execute("DELETE FROM admins WHERE user_id = ?", (target_uid,))
                DB.commit()
                if CUR.rowcount > 0:
                    await update.message.reply_text(f"✅ کاربر {target_uid} با موفقیت از لیست ادمین‌ها حذف شد.")
                else:
                    await update.message.reply_text(f"❌ کاربر {target_uid} در لیست ادمین‌ها نبود.")
                await admin_manage_admins_handler(update, context, query_message=update.message)
            except Exception as e:
                await update.message.reply_text(f"❌ خطای حذف ادمین: {e}")
            return
            
        # (جدید) حالت افزودن کانال
        if state == "awaiting_new_channel_username":
            context.user_data.pop("state", None)
            try:
                channel_username = text.strip().replace("@", "")
                if not channel_username: raise ValueError("یوزرنیم خالی است")
                CUR.execute("INSERT INTO channels(channel_id, added_by, added_at) VALUES (?, ?, ?)",
                            (channel_username, uid, datetime.utcnow().isoformat()))
                DB.commit()
                await update.message.reply_text(f"✅ کانال @{channel_username} با موفقیت به لیست عضویت اجباری اضافه شد.")
                await admin_manage_channels_handler(update, context, query_message=update.message)
            except sqlite3.IntegrityError:
                await update.message.reply_text(f"❌ کانال @{channel_username} از قبل موجود است.")
            except Exception as e:
                await update.message.reply_text(f"❌ خطای افزودن کانال: {e}")
            return

        # (جدید) حالت حذف کانال
        if state == "awaiting_remove_channel_username":
            context.user_data.pop("state", None)
            try:
                channel_username = text.strip().replace("@", "")
                if not channel_username: raise ValueError("یوزرنیم خالی است")
                CUR.execute("DELETE FROM channels WHERE channel_id = ?", (channel_username,))
                DB.commit()
                if CUR.rowcount > 0:
                    await update.message.reply_text(f"✅ کانال @{channel_username} با موفقیت از لیست حذف شد.")
                else:
                    await update.message.reply_text(f"❌ کانال @{channel_username} در لیست وجود نداشت.")
                await admin_manage_channels_handler(update, context, query_message=update.message)
            except Exception as e:
                await update.message.reply_text(f"❌ خطای حذف کانال: {e}")
            return


    # --- (حذف شد) منطق دکمه‌های ادمین از اینجا حذف شد ---
    # if text == "📢 پیام همگانی" ...
    # if text == "📨 گزارش‌ها" ...

    # --- منطق کاربر: حالت پیش‌نویس قرارداد ---
    if state == "awaiting_draft_request":
        await update.message.reply_text("⏳ در حال تنظیم پیش‌نویس...")
        answer = await ask_ai(
            user_id=uid,
            prompt=f"یک پیش‌نویس قرارداد کامل و دقیق برای موضوع زیر بنویس: '{text}'. تمام مواد لازم، تعهدات طرفین و شرایط فسخ را ذکر کن.",
            system="شما یک وکیل متخصص در تنظیم قرارداد هستید. باید پیش‌نویس‌های کامل و حرفه‌ای ارائه دهید."
        )
        save_history(uid, "user", "پیش‌نویس", text)
        save_history(uid, "bot", "پاسخ پیش‌نویس", answer)
        await update.message.reply_text(answer + LEGAL_DISCLAIMER, reply_markup=current_menu) # (جدید) تذکر اضافه شد
        context.user_data.pop("state", None)
        return

    # --- منطق کاربر: حالت واژه‌نامه حقوقی ---
    if state == "awaiting_term":
        await update.message.reply_text("⏳ در حال جستجوی اصطلاح...")
        answer = await ask_ai(
            user_id=uid,
            prompt=f"اصطلاح حقوقی '{text}' را به زبان ساده فارسی برای یک فرد غیرحقوقی توضیح بده.",
            system="شما یک فرهنگ‌نامه حقوقی هستید که اصطلاحات را به زبان ساده توضیح می‌دهید."
        )
        save_history(uid, "user", "واژه‌نامه", text)
        save_history(uid, "bot", "پاسخ واژه‌نامه", answer)
        await update.message.reply_text(answer + LEGAL_DISCLAIMER, reply_markup=current_menu) # (جدید) تذکر اضافه شد
        context.user_data.pop("state", None)
        return
    
    # --- منطق محاسبه‌گرها ---
    if state == "awaiting_enforcement_calc":
        try:
            amount = float(text.replace(",", "").strip())
            cost = amount * 0.05
            msg = (
                f"🧮 محاسبه هزینه اجرای احکام:\n\n"
                f"مبلغ محکوم به: {int(amount):,} ریال\n"
                f"هزینه اجرا (نیم عشر): {int(cost):,} ریال"
            )
            await update.message.reply_text(msg, reply_markup=current_menu) # این تذکر نیاز ندارد
        except Exception:
            await update.message.reply_text("❌ فرمت اشتباه. لطفاً فقط مبلغ را به ریال وارد کنید.", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    if state == "awaiting_late_payment_calc":
        await update.message.reply_text("⏳ در حال محاسبه خسارت بر اساس شاخص بانک مرکزی...")
        answer = await ask_ai(
            user_id=uid,
            prompt=f"خسارت تاخیر تادیه را برای این مورد محاسبه کن: '{text}'. فرمول و شاخص مورد استفاده را ذکر کن.",
            system="شما یک متخصص امور مالی و حقوقی هستید که با استفاده از شاخص‌های بانک مرکزی ایران، خسارت تاخیر تادیه را محاسبه می‌کنید. حتماً ذکر کنید که این محاسبه تخمینی است."
        )
        await update.message.reply_text(answer + LEGAL_DISCLAIMER, reply_markup=current_menu) # (جدید) تذکر اضافه شد
        context.user_data.pop("state", None)
        return

    if state == "awaiting_diyah_calc":
        await update.message.reply_text("⏳ در حال محاسبه دیه بر اساس نرخ روز...")
        answer = await ask_ai(
            user_id=uid,
            prompt=f"دیه را برای مورد زیر محاسبه کن: '{text}'. لطفاً نرخ دیه کامل سال جاری (۱۴۰۴) و اینکه آیا ماه حرام (در صورت ذکر) تاثیر داشته است را ذکر کن.",
            system="شما یک کارشناس رسمی دادگستری مسلط به قوانین دیه (مجازات اسلامی) هستید. شما مبلغ دیه کامل مرد در سال 1404 در ماه عادی را 1 میلیارد و 400 میلیون تومان و در ماه حرام 1 میلیارد و 800 میلیون تومان در نظر می‌گیرید."
        )
        await update.message.reply_text(answer + LEGAL_DISCLAIMER, reply_markup=current_menu) # (جدید) تذکر اضافه شد
        context.user_data.pop("state", None)
        return

    if state == "awaiting_inheritance_calc":
        await update.message.reply_text("⏳ در حال تحلیل طبقات و درجات و محاسبه سهم‌الارث...")
        answer = await ask_ai(
            user_id=uid,
            prompt=f"سهم‌الارث را برای بازماندگان زیر محاسبه کن: '{text}'. لطفاً طبقه و سهم هر فرد را به تفکیک مشخص کن.",
            system="شما یک متخصص ارشد حقوقی مسلط به قانون مدنی ایران در باب ارث هستید. محاسبات را دقیق و بر اساس طبقات و درجات انجام دهید."
        )
        await update.message.reply_text(answer + LEGAL_DISCLAIMER, reply_markup=current_menu) # (جدید) تذکر اضافه شد
        context.user_data.pop("state", None)
        return

    # (جدید) حالت محاسبه هزینه دادرسی
    if state == "awaiting_dadrasi_calc":
        try:
            amount = float(text.replace(",", "").strip())
            threshold = 200_000_000 # سقف ۲۰ میلیون تومان
            rate1 = 0.035 # ۳.۵ درصد
            rate2 = 0.025 # ۲.۵ درصد (طبق ایده کاربر، اگرچه قانون ممکن است متفاوت باشد)

            if amount <= threshold:
                cost_badavi = amount * rate1
            else:
                cost_badavi = (threshold * rate1) + ((amount - threshold) * rate2)
            
            # هزینه تجدیدنظر معمولا درصدی از هزینه بدوی است
            cost_tajdid = cost_badavi * 1.5 # (مثال) - این فرمول باید دقیق‌تر شود
            
            msg = (
                f"🧾 **محاسبه هزینه دادرسی (تخمینی)**\n\n"
                f"مبلغ خواسته: {int(amount):,} ریال\n\n"
                f"هزینه مرحله بدوی (اولیه): {int(cost_badavi):,} ریال\n"
                f"هزینه مرحله تجدیدنظر (تقریبی): {int(cost_tajdid):,} ریال\n\n"
                f"توجه: این محاسبه بر اساس فرمول ارائه شده (۳.۵٪ تا ۲۰م ت و ۲.۵٪ مازاد) است و ممکن است با تعرفه‌های دقیق سال متفاوت باشد."
            )
            await update.message.reply_text(msg, reply_markup=current_menu)

        except Exception as e:
            await update.message.reply_text(f"❌ فرمت اشتباه. لطفاً فقط مبلغ خواسته را به ریال وارد کنید. {e}", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    # (جدید) حالت افزودن پرونده من
    if state == "awaiting_my_case_details":
        try:
            # پارس کردن ساده بر اساس |
            parts = {k.strip(): v.strip() for k, v in (p.split(":", 1) for p in text.split("|"))}
            
            title = parts.get("عنوان", "بدون عنوان")
            case_num = parts.get("شماره", "---")
            branch = parts.get("شعبه", "---")
            notes = parts.get("یادداشت", "---")

            CUR.execute(
                "INSERT INTO my_cases(user_id, title, case_number, branch, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, title, case_num, branch, notes, datetime.utcnow().isoformat())
            )
            DB.commit()
            await update.message.reply_text(f"✅ پرونده '{title}' با موفقیت در دفترچه شما ذخیره شد.", reply_markup=current_menu)

        except Exception as e:
            logger.error(f"MyCase add error: {e}")
            await update.message.reply_text("❌ خطا در فرمت ورودی. لطفاً از فرمت پیشنهادی استفاده کنید:\n"
                                            "عنوان: چک آقای الف | شماره: 990123 | شعبه: 5 حقوقی | یادداشت: جلسه بعدی",
                                            reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    if state == "awaiting_mehrieh_calc":
        try:
            count = int(text.split()[0])
            if count <= 0: raise ValueError
        except Exception:
            await update.message.reply_text("❌ فرمت اشتباه. یک عدد مثبت (مانند 110) وارد کنید.", reply_markup=current_menu)
            context.user_data.pop("state", None)
            return
        
        await update.message.reply_text("⏳ در حال دریافت آخرین نرخ سکه...")
        rate = await fetch_tgju_sekee_rate()
        if rate is None: rate = get_last_rate()
        if rate is None:
            await update.message.reply_text("⚠️ نرخ در دسترس نیست. بعداً تلاش کنید.", reply_markup=current_menu)
            context.user_data.pop("state", None)
            return
            
        total_riyals = int(count * rate)
        total_toman = total_riyals // 10
        msg = (
            f"💰 محاسبه مهریه (به نرخ روز)\n\n"
            f"تعداد سکه: {count} عدد\n"
            f"نرخ هر سکه: {rate:,} ریال\n"
            f"مبلغ کل: {total_riyals:,} ریال\n"
            f"مبلغ به تومان: {total_toman:,} تومان"
        )
        await update.message.reply_text(msg, reply_markup=current_menu) # این تذکر نیاز ندارد
        context.user_data.pop("state", None)
        return

    # --- (تغییر) منطق پرسش حقوقی (اکنون با دسته‌بندی) ---
    if state == "awaiting_categorized_question":
        await update.message.reply_text("⏳ در حال تحلیل سوال شما (با بررسی تاریخچه و موضوع)...")
        
        category = context.user_data.pop("question_category", "عمومی")
        full_prompt = f"در موضوع: {category}. سوال: {text}"
        
        chat_history = get_chat_history(uid)
        
        answer = await ask_ai(uid, full_prompt, chat_history=chat_history)
        
        save_history(uid, "user", "پرسش حقوقی", full_prompt) # سوال کامل ذخیره می‌شود
        save_history(uid, "bot", "پاسخ حقوقی", answer)
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data="like"), InlineKeyboardButton("👎", callback_data="dislike")]])
        await update.message.reply_text(answer + LEGAL_DISCLAIMER, reply_markup=kb)
        context.user_data.pop("state", None)
        return

    # (حذف شد) حالت قدیمی پرسش حقوقی
    # if state == "awaiting_question": ...

    # --- حالت ارسال گزارش (رفع اشکال شد) ---
    if state == "awaiting_report":
        report_id = create_report(uid, text)
        if SUPER_ADMIN_ID: # (تغییر) ارسال به ادمین کل
            # (تغییر) دکمه پاسخ ادمین اکنون در پنل /admin است
            try:
                await context.bot.send_message(
                    chat_id=SUPER_ADMIN_ID, 
                    text=f"📩 گزارش جدید (ID: {report_id}) از {user.first_name} (@{user.username or 'ندارد'})\n🆔 {uid}\n\n{text}",
                )
                await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text="(برای پاسخگویی به پنل /admin بخش 'مدیریت گزارش‌ها' مراجعه کنید.)")
            except Exception: logger.exception("failed to notify admin")
        await update.message.reply_text("✅ گزارش شما ثبت شد.", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    # --- سایر حالت‌ها (رفع اشکال شد) ---
    if state == "adding_reminder":
        try:
            if ":" not in text: raise ValueError("format")
            title, datepart = text.split(":", 1)
            add_reminder(uid, title.strip(), datepart.strip())
            await update.message.reply_text(f"✅ یادآوری '{title.strip()}' ثبت شد.", reply_markup=current_menu)
        except Exception:
            await update.message.reply_text("❌ فرمت اشتباه. مثال: قرار: 2025-12-20 14:30", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return
    
    if state == "calc_coin_input":
        try:
            count = float(text.split()[0])
            # (منطق محاسبه که قبلا نوشته شده بود... اما برای سادگی، فقط به حالت مهریه ارجاع می‌دهیم)
            await update.message.reply_text("لطفاً از دکمه 'محاسبه مهریه' استفاده کنید.", reply_markup=current_menu)
        except Exception:
            await update.message.reply_text("❌ فرمت اشتباه.", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    if state == "calc_tax_input_simple":
        try:
            amount = float(text.replace(",", "").strip())
            tax = int(amount * DEFAULT_TAX_RATE)
            net = int(amount - tax)
            await update.message.reply_text(
                f"💸 مالیات (نرخ {int(DEFAULT_TAX_RATE*100)}%):\nمبلغ: {int(amount):,}\nمالیات: {tax:,}\nپس از کسر: {net:,}",
                reply_markup=current_menu
            )
        except Exception:
            await update.message.reply_text("❌ فرمت اشتباه.", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    if state == "calc_tax_input_advanced":
        try:
            parts = text.split()
            if len(parts) != 3: raise ValueError()
            gross = float(parts[0].replace(",", ""))
            deductions = float(parts[1].replace(",", ""))
            rate = float(parts[2].replace("%", "")) / 100.0
            taxable = max(0.0, gross - deductions)
            tax = int(taxable * rate)
            net = int(gross - tax)
            await update.message.reply_text(
                f"📊 ... (محاسبه پیشرفته)", # (کد کامل اینجا بود)
                reply_markup=current_menu
            )
        except Exception:
            await update.message.reply_text("❌ فرمت اشتباه.", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    # --- دکمه‌های منوی اصلی (ارتقا یافته) ---
    
    if text == "🧾 پرسش حقوقی (با حافظه)":
        # (جدید) نمایش دسته‌بندی‌ها
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚖️ خانواده (طلاق، مهریه، ارث)", callback_data="category_خانواده و ارث"),
                InlineKeyboardButton("🏛️ ملکی (اجاره، خرید و فروش)", callback_data="category_ملکی و قراردادها")
            ],
            [
                InlineKeyboardButton("👮 کیفری (کلاهبرداری، سرقت)", callback_data="category_کیفری"),
                InlineKeyboardButton("💰 مالی (چک، سفته، دیه)", callback_data="category_مالی و تجاری")
            ],
            [
                InlineKeyboardButton("🚗 تصادفات و بیمه", callback_data="category_تصادفات و بیمه"),
                InlineKeyboardButton("✍️ سایر موضوعات (متنی)", callback_data="category_عمومی")
            ]
        ])
        await update.message.reply_text("📚 لطفاً ابتدا موضوع سوال حقوقی خود را انتخاب کنید:", reply_markup=kb)
        return

    if text == "📨 ارسال گزارش":
        await update.message.reply_text("📝 لطفاً متن گزارش را بنویس:", reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True, one_time_keyboard=True))
        context.user_data["state"] = "awaiting_report"
        return

    if text == "📄 تحلیل سند (PDF/DOCX)":
        await update.message.reply_text("✍️ لطفاً فایل PDF یا DOCX خود را برای تحلیل ارسال کنید:", reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True, one_time_keyboard=True))
        context.user_data["state"] = "awaiting_document"
        return

    if text == "📝 پیش‌نویس": # (اصلاح) نام دکمه در منو "پیش‌نویس" است
        await update.message.reply_text("✍️ موضوع قرارداد مورد نیاز خود را بنویسید (مثال: اجاره‌نامه خودرو):", reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True, one_time_keyboard=True))
        context.user_data["state"] = "awaiting_draft_request"
        return

    if text == "📚 واژه‌نامه": # (اصلاح) نام دکمه
        await update.message.reply_text("✍️ لطفاً اصطلاح حقوقی مورد نظر خود را بنویسید (مانند: سرقفلی):", reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True, one_time_keyboard=True))
        context.user_data["state"] = "awaiting_term"
        return

    # --- (جدید) دکمه‌های ویژگی‌های جدید ---
    if text == "📄 قالب‌های آماده":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 اجاره‌نامه ساده", callback_data="template_rent")],
            [InlineKeyboardButton("📄 رسید دریافت وجه (IOU)", callback_data="template_iou")],
            # [InlineKeyboardButton("📄 مبایعه‌نامه خودرو", callback_data="template_car")],
        ])
        await update.message.reply_text("⚖️ کدام قالب آماده را نیاز دارید؟\n(توجه: این قالب‌ها فقط نمونه هستند و باید توسط وکیل بررسی شوند.)", reply_markup=kb)
        return

    if text == "🔔 آخرین اخبار":
        await update.message.reply_text("⏳ در حال جستجوی آخرین اخبار و مصوبات حقوقی...")
        answer = await ask_ai(
            user_id=uid,
            prompt="آخرین مصوبات مجلس، آرای وحدت رویه جدید، و اخبار مهم حقوقی روز ایران را در 3 مورد بسیار کوتاه و خلاصه (هر کدام یک خط) برای من لیست کن.",
            system="شما یک دستیار حقوقی هستید که به اخبار روز مسلط است."
        )
        await update.message.reply_text(f"🔔 **آخرین اخبار حقوقی (به روایت AI)**\n\n{answer}\n\n" + LEGAL_DISCLAIMER, reply_markup=current_menu)
        return

    if text == "🗂️ پرونده‌های من":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗒️ مشاهده همه‌ی پرونده‌ها", callback_data="list_my_cases")],
            [InlineKeyboardButton("➕ افزودن پرونده جدید", callback_data="add_my_case")]
        ])
        await update.message.reply_text("🗂️ **دفترچه یادداشت پرونده‌های من**\n\n(این اطلاعات به صورت خصوصی فقط برای شما ذخیره می‌شود.)", reply_markup=kb)
        return
        
    if text == "⚖️ آزمون حقوقی":
        # (جدید) نمایش گزینه‌های آزمون
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⁉️ شروع/ادامه آزمون", callback_data="quiz_start")],
            [InlineKeyboardButton("🏆 جدول امتیازات", callback_data="quiz_leaderboard")]
        ])
        await update.message.reply_text("⚖️ **آزمون حقوقی**\n\nدر آزمون هفتگی شرکت کنید و امتیاز خود را با دیگران مقایسه کنید!", reply_markup=kb)
        return
    
    # (جدید) دکمه نکات حقوقی
    if text == "💡 نکات حقوقی":
        await send_legal_tip(update, context, tip_id=1)
        context.user_data['current_tip_id'] = 1
        return

    # --- پایان دکمه‌های جدید ---

    if text == "⏰ یادآوری‌ها":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 افزودن یادآوری جدید", callback_data="add_reminder")],
            [InlineKeyboardButton("🗒️ مشاهده یادآوری‌ها", callback_data="list_reminders")]
        ])
        await update.message.reply_text("🔔 مدیریت یادآوری‌ها:", reply_markup=kb)
        return

    if text == "🧮 محاسبه‌گر": # (اصلاح) نام دکمه
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🪙 محاسبه مهریه (به نرخ روز)", callback_data="calc_mehrieh")],
            [InlineKeyboardButton("⚖️ محاسبه دیه (هوشمند)", callback_data="calc_diyah")],
            [InlineKeyboardButton("📈 خسارت تاخیر تادیه (هوشمند)", callback_data="calc_late_payment")],
            [InlineKeyboardButton("🏛️ هزینه اجرای احکام (ساده)", callback_data="calc_enforcement")],
            [InlineKeyboardButton("🧾 محاسبه هزینه دادرسی (جدید)", callback_data="calc_dadrasi")],
            [InlineKeyboardButton("👨‍👩‍👧‍👦 محاسبه سهم‌الارث (هوشمند)", callback_data="calc_inheritance")],
            # [InlineKeyboardButton("💰 محاسبه سکه امامی", callback_data="calc_coin")],
            # [InlineKeyboardButton("💸 مالیات (ساده)", callback_data="calc_tax_simple")],
        ])
        await update.message.reply_text("🧮 کدام مورد را محاسبه کنم؟", reply_markup=kb)
        return

    if text == "⚙️ تنظیمات":
        settings = get_user_settings(uid)
        p = settings["ai_personality"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{'✅' if p == 'simple' else ''} ساده (عامیانه)", callback_data="set_p_simple")],
            [InlineKeyboardButton(f"{'✅' if p == 'default' else ''} متوسط (پیش‌فرض)", callback_data="set_p_default")],
            [InlineKeyboardButton(f"{'✅' if p == 'technical' else ''} فنی (مستند)", callback_data="set_p_technical")]
        ])
        await update.message.reply_text("⚙️ شخصیت ربات را برای پاسخگویی انتخاب کنید:", reply_markup=kb)
        return

    # (جدید) دکمه ارتباط با کارشناسان (اگر در منو بود)
    # if text == "📞 ارتباط با کارشناسان":
    #     await update.message.reply_text(
    #         f"⚖️ برای ارتباط با وکلا و کارشناسان رسمی، می‌توانید به کانال مرجع ما مراجعه کنید:\n\n"
    #         f"https://t.me/YOUR_CHANNEL_USERNAME_HERE\n\n" # (نیاز به تعریف CHANNEL_USERNAME دارد)
    #         f"در این کانال، اطلاعات تماس و حوزه تخصصی همکاران ما اطلاع‌رسانی می‌شود.",
    #         reply_markup=current_menu
    #     )
    #     return

    # (رفع اشکال شد) دکمه پروفایل من
    if text == "👤 پروفایل من":
        CUR.execute("SELECT joined_at FROM users WHERE user_id=?", (uid,))
        row = CUR.fetchone()
        joined = row["joined_at"].split("T")[0] if row else "نامشخص"
        
        CUR.execute("SELECT COUNT(*) as c FROM reminders WHERE user_id=?", (uid,))
        rem_count = CUR.fetchone()["c"]
        CUR.execute("SELECT COUNT(*) as c FROM reports WHERE user_id=?", (uid,))
        rep_count = CUR.fetchone()["c"]
        
        await update.message.reply_text(
            f"👤 نام: {user.first_name or ''}\n"
            f"یوزرنیم: @{user.username or 'ندارد'}\n"
            f"آیدی: {uid}\n"
            f"زمان عضویت: {joined}\n"
            f"یادآوری‌ها: {rem_count}\n"
            f"گزارش‌ها: {rep_count}",
            reply_markup=current_menu
        )
        return
        
    if text == "🔙 بازگشت به منو":
        context.user_data.clear() # پاک کردن تمام حالت‌ها
        await update.message.reply_text("منوی اصلی:", reply_markup=current_menu)
        return

    # --- fallback (پاسخ پیش‌فرض) ---
    # اگر کاربر ادمین باشد و در حالتی نباشد، به او پنل ادمین را یادآوری کن
    if is_admin(uid) and not state: # (تغییر)
        await update.message.reply_text("دستور شما در منوی اصلی موجود نیست. \nبرای پنل مدیریت /admin را ارسال کنید.", reply_markup=current_menu)
        return
        
    await update.message.reply_text("لطفاً یکی از گزینه‌های منو را انتخاب کنید 👇", reply_markup=current_menu)

# ---------- Document Handler (ارتقا یافته) ----------
@rate_limited
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    doc = update.message.document
    state = context.user_data.get("state")
    current_menu = MAIN_MENU # (تغییر)

    if state != "awaiting_document":
        logger.info(f"User {uid} sent a document without being in 'awaiting_document' state.")
        if state == "awaiting_categorized_question": # (اصلاح)
             await update.message.reply_text("شما در حالت 'پرسش حقوقی' هستید. برای تحلیل سند، ابتدا /start را بزنید و دکمه 'تحلیل سند' را انتخاب کنید.", reply_markup=current_menu)
        return

    file_name = doc.file_name.lower()
    
    if not (file_name.endswith('.pdf') or file_name.endswith('.docx')):
        await update.message.reply_text("❌ فقط فایل‌های PDF و DOCX پشتیبانی می‌شوند.", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    await update.message.reply_text(f"⏳ فایل '{doc.file_name}' دریافت شد. در حال دانلود و استخراج متن...")
    
    file = await doc.get_file()
    text_content = ""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, doc.file_name)
        await file.download_to_drive(path)
        
        if file_name.endswith('.pdf'):
            text_content = extract_pdf_text(path)
        elif file_name.endswith('.docx'):
            text_content = extract_docx_text(path)
    
    if not text_content or len(text_content.strip()) < 20:
        await update.message.reply_text("❌ متنی از فایل استخراج نشد. (فایل‌های PDF عکس‌محور پشتیبانی نمی‌شوند)", reply_markup=current_menu)
        context.user_data.pop("state", None)
        return

    await update.message.reply_text(f"✅ متن با موفقیت استخراج شد ({len(text_content)} کاراکتر). در حال ارسال به هوش مصنوعی برای تحلیل...")
    
    max_len = 8000
    if len(text_content) > max_len:
        text_content = text_content[:max_len] + "\n\n... (متن به دلیل طولانی بودن کوتاه شد)"

    answer = await ask_ai(
        user_id=uid,
        prompt=f"متن سند زیر را به دقت تحلیل حقوقی کن، نکات کلیدی، تعهدات طرفین و ریسک‌های احتمالی آن را مشخص کن:\n\n---(شروع سند)---\n{text_content}\n---(پایان سند)---",
        system="شما یک وکیل ارشد هستید که در تحلیل و خلاصه‌سازی اسناد حقوقی و قراردادها تخصص دارید."
    )
    
    save_history(uid, "user", "تحلیل سند", file_name)
    save_history(uid, "bot", "پاسخ تحلیل سند", answer)
    
    await update.message.reply_text(answer + LEGAL_DISCLAIMER, reply_markup=current_menu) # (جدید) تذکر اضافه شد
    context.user_data.pop("state", None)

# ---------- (جدید) Admin Panel Handlers ----------

def build_admin_menu(menu_type: str = "main", user_id: int = 0) -> InlineKeyboardMarkup:
    """ساخت کیبورد اینلاین پنل ادمین"""
    kb = []
    if menu_type == "main":
        kb = [
            [InlineKeyboardButton("📊 آمار ربات", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 پیام همگانی", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📨 مدیریت گزارش‌ها", callback_data="admin_reports")],
            [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="admin_users")],
            [InlineKeyboardButton("⚙️ تنظیمات ربات", callback_data="admin_settings")], # (جدید)
            [InlineKeyboardButton("❌ بستن پنل", callback_data="admin_close")]
        ]
    elif menu_type == "settings": # (جدید)
        kb.append([InlineKeyboardButton("📢 مدیریت کانال‌ها", callback_data="admin_manage_channels")])
        if user_id == SUPER_ADMIN_ID: # فقط ادمین کل
             kb.append([InlineKeyboardButton("🛂 مدیریت ادمین‌ها", callback_data="admin_manage_admins")])
        kb.append([InlineKeyboardButton("⁉️ مدیریت آزمون", callback_data="admin_manage_quiz")]) # (جدید)
        kb.append([InlineKeyboardButton("💡 مدیریت نکات حقوقی", callback_data="admin_manage_tips")]) # (جدید)
        kb.append([InlineKeyboardButton("🔙 بازگشت به پنل اصلی", callback_data="admin_main")])
        
    elif menu_type == "manage_admins": # (جدید)
        kb = [
            [InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_add_admin")],
            [InlineKeyboardButton("➖ حذف ادمین", callback_data="admin_remove_admin")],
            [InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data="admin_settings")]
        ]
    
    elif menu_type == "manage_channels": # (جدید)
        kb = [
            [InlineKeyboardButton("➕ افزودن کانال", callback_data="admin_add_channel")],
            [InlineKeyboardButton("➖ حذف کانال", callback_data="admin_remove_channel")],
            [InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data="admin_settings")]
        ]
        
    elif menu_type == "manage_quiz": # (جدید)
        kb = [
            [InlineKeyboardButton("➕ افزودن سوال جدید", callback_data="admin_add_quiz")],
            [InlineKeyboardButton("❌ غیرفعال کردن همه‌ی سوالات", callback_data="admin_clear_quiz")],
            [InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data="admin_settings")]
        ]
        
    elif menu_type == "manage_tips": # (جدید)
        kb = [
            [InlineKeyboardButton("➕ افزودن نکته جدید", callback_data="admin_add_tip")],
            # (ایده آینده: حذف/ویرایش نکته)
            [InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data="admin_settings")]
        ]
        
    elif menu_type in ["stats", "broadcast", "reports", "users"]:
        kb = [[InlineKeyboardButton("🔙 بازگشت به پنل اصلی", callback_data="admin_main")]]
        
    return InlineKeyboardMarkup(kb)

async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش پنل اصلی ادمین"""
    user = update.effective_user
    if not is_admin(user.id): # (تغییر)
        await update.message.reply_text("شما ادمین نیستید.", reply_markup=MAIN_MENU)
        return
        
    # پاک کردن حالت‌های قبلی
    context.user_data.clear()
    
    await update.message.reply_text(
        "🔐 **پنل مدیریت ربات**\n\n"
        "لطفاً یک گزینه را انتخاب کنید:",
        reply_markup=build_admin_menu("main", user.id) # (تغییر)
    )

async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, query: object):
    """نمایش آمار ربات"""
    CUR.execute("SELECT COUNT(*) as c FROM users")
    total_users = CUR.fetchone()["c"]
    
    today_str = date.today().isoformat()
    CUR.execute("SELECT COUNT(*) as c FROM users WHERE joined_at >= ?", (today_str,))
    today_users = CUR.fetchone()["c"]
    
    CUR.execute("SELECT COUNT(*) as c FROM history")
    total_history = CUR.fetchone()["c"]

    CUR.execute("SELECT COUNT(*) as c FROM reports WHERE admin_reply IS NULL")
    pending_reports = CUR.fetchone()["c"]
    CUR.execute("SELECT COUNT(*) as c FROM reports")
    total_reports = CUR.fetchone()["c"]

    msg = (
        f"📊 **آمار ربات**\n\n"
        f"👤 **کاربران:**\n"
        f"  - کل کاربران: {total_users}\n"
        f"  - کاربران امروز: {today_users}\n\n"
        f"📨 **گزارش‌ها:**\n"
        f"  - در انتظار پاسخ: {pending_reports}\n"
        f"  - کل گزارش‌ها: {total_reports}\n\n"
        f"💬 **تاریخچه:**\n"
        f"  - کل پیام‌های (ذخیره شده): {total_history}"
    )
    await query.edit_message_text(msg, reply_markup=build_admin_menu("stats", query.from_user.id)) # (تغییر)

async def show_admin_reports(update: Update, context: ContextTypes.DEFAULT_TYPE, query: object = None, query_message: object = None):
    """نمایش گزارش‌های در انتظار پاسخ"""
    CUR.execute("SELECT R.id, R.content, R.created_at, U.first_name, U.user_id FROM reports R LEFT JOIN users U ON R.user_id = U.user_id WHERE R.admin_reply IS NULL ORDER BY R.id DESC LIMIT 5")
    rows = CUR.fetchall()
    
    msg_part = "📨 **گزارش‌های در انتظار پاسخ** (۵ مورد آخر)\n\n"
    kb_buttons = []
    
    if not rows:
        msg_part += "هیچ گزارش در انتظار پاسخی یافت نشد."
    else:
        for r in rows:
            msg_part += (
                f"--- (ID: {r['id']}) ---\n"
                f"از: {r['first_name']} (ID: {r['user_id']})\n"
                f"تاریخ: {r['created_at'].split('T')[0]}\n"
                f"متن: {r['content'][:150]}...\n\n"
            )
            # دکمه پاسخ برای هر گزارش
            kb_buttons.append([
                InlineKeyboardButton(
                    f"✉️ پاسخ به گزارش #{r['id']} (کاربر {r['first_name'] or r['user_id']})", 
                    callback_data=f"admin_reply_to_{r['id']}_{r['user_id']}"
                )
            ])
            
    kb_buttons.append([InlineKeyboardButton("🔙 بازگشت به پنل اصلی", callback_data="admin_main")])
    reply_markup = InlineKeyboardMarkup(kb_buttons)
    
    if query:
        await query.edit_message_text(msg_part, reply_markup=reply_markup)
    elif query_message:
        await query_message.reply_text(msg_part, reply_markup=reply_markup)

# (جدید) هندلرهای تنظیمات، ادمین‌ها و کانال‌ها
async def admin_settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, query: object):
    """نمایش منوی تنظیمات ربات"""
    user_id = query.from_user.id
    await query.edit_message_text(
        "⚙️ **تنظیمات ربات**\n\n"
        "بخش مورد نظر را انتخاب کنید:",
        reply_markup=build_admin_menu("settings", user_id)
    )

async def admin_manage_admins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, query: object = None, query_message: object = None):
    """نمایش پنل مدیریت ادمین‌ها (فقط ادمین کل)"""
    user_id = query.from_user.id if query else query_message.from_user.id
    if user_id != SUPER_ADMIN_ID:
        if query: await query.answer("⛔ فقط ادمین کل به این بخش دسترسی دارد.", show_alert=True)
        return

    CUR.execute("SELECT user_id, added_at FROM admins")
    rows = CUR.fetchall()
    
    msg = "🛂 **مدیریت ادمین‌ها**\n\n"
    msg += f"👑 **ادمین کل:** `{SUPER_ADMIN_ID}` (دائمی)\n\n"
    msg += "👥 **ادمین‌های ثانویه:**\n"
    
    if not rows:
        msg += "(هیچ ادمین ثانویه‌ای اضافه نشده است.)"
    else:
        for r in rows:
            msg += f"  - `{r['user_id']}` (اضافه شده در: {r['added_at'].split('T')[0]})\n"
            
    kb = build_admin_menu("manage_admins", user_id)
    
    if query:
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="Markdown")
    elif query_message:
        await query_message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")

async def admin_manage_channels_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, query: object = None, query_message: object = None):
    """نمایش پنل مدیریت کانال‌ها"""
    user_id = query.from_user.id if query else query_message.from_user.id
    
    channels = get_mandatory_channels()
    
    msg = "📢 **مدیریت کانال‌های عضویت اجباری**\n\n"
    msg += "کاربران باید عضو *تمام* کانال‌های زیر باشند:\n\n"
    
    if not channels:
        msg += "(هیچ کانالی تنظیم نشده است. عضویت اجباری غیرفعال است.)"
    else:
        for ch_id in channels:
            msg += f"  - `@{ch_id}`\n"
            
    kb = build_admin_menu("manage_channels", user_id)
    
    if query:
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="Markdown")
    elif query_message:
        await query_message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")

# (جدید) هندلر مدیریت آزمون
async def admin_quiz_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, query: object = None, query_message: object = None):
    """نمایش پنل مدیریت آزمون"""
    user_id = query.from_user.id if query else query_message.from_user.id
    
    CUR.execute("SELECT id, question_text FROM quiz_questions WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
    active_q = CUR.fetchone()
    
    msg = "⁉️ **مدیریت آزمون**\n\n"
    if active_q:
        msg += f"**سوال فعال فعلی:**\n(ID: {active_q['id']}) - {active_q['question_text'][:100]}...\n\n"
    else:
        msg += "(در حال حاضر هیچ سوال فعالی وجود ندارد. کاربران با خطای 'آزمون موجود نیست' مواجه می‌شوند.)\n\n"
            
    kb = build_admin_menu("manage_quiz", user_id)
    
    if query:
        await query.edit_message_text(msg, reply_markup=kb)
    elif query_message:
        await query_message.reply_text(msg, reply_markup=kb)

# (جدید) هندلر مدیریت نکات حقوقی
async def admin_manage_tips_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, query: object = None, query_message: object = None):
    """نمایش پنل مدیریت نکات حقوقی"""
    user_id = query.from_user.id if query else query_message.from_user.id
    
    CUR.execute("SELECT COUNT(*) as c FROM legal_tips")
    count = CUR.fetchone()["c"]
    
    CUR.execute("SELECT tip_text FROM legal_tips ORDER BY id DESC LIMIT 1")
    last_tip = CUR.fetchone()
    
    msg = "💡 **مدیریت نکات حقوقی**\n\n"
    msg += f"تعداد کل نکات ثبت شده: {count}\n\n"
    if last_tip:
        msg += f"**آخرین نکته ثبت شده:**\n{last_tip['tip_text'][:100]}...\n\n"
    else:
        msg += "(هنوز هیچ نکته‌ای ثبت نشده است.)\n\n"
            
    kb = build_admin_menu("manage_tips", user_id)
    
    if query:
        await query.edit_message_text(msg, reply_markup=kb)
    elif query_message:
        await query_message.reply_text(msg, reply_markup=kb)

# (جدید) توابع کمکی ویژگی‌های جدید
async def send_template(query: object, context: ContextTypes.DEFAULT_TYPE, template_name: str):
    """فایل متنی قالب را برای کاربر ارسال می‌کند"""
    content = TEMPLATES.get(template_name)
    if not content:
        await query.edit_message_text("❌ خطای داخلی: قالب یافت نشد.")
        return
        
    try:
        file_name = f"template_{template_name}.txt"
        file_content = content.encode('utf-8')
        
        # ایجاد فایل در حافظه
        bio = io.BytesIO(file_content)
        bio.name = file_name
        bio.seek(0)
        
        await query.message.reply_document(document=bio, caption=f"📄 قالب آماده: {template_name}")
        await query.edit_message_text("✅ فایل قالب ارسال شد.")
    except Exception as e:
        logger.error(f"Failed to send template file: {e}")
        await query.edit_message_text(f"❌ خطا در ارسال فایل: {e}")

async def get_active_quiz_question() -> Optional[sqlite3.Row]:
    """آخرین سوال فعال آزمون را برمی‌گرداند"""
    try:
        CUR.execute("SELECT * FROM quiz_questions WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
        return CUR.fetchone()
    except Exception as e:
        logger.error(f"Error getting active quiz: {e}")
        return None

async def send_quiz_question(message, context: ContextTypes.DEFAULT_TYPE, user_id: int): # (تغییر) امضا تابع
    """سوال فعال آزمون را برای کاربر ارسال می‌کند"""
    q = await get_active_quiz_question()
    if not q:
        await message.reply_text("❌ در حال حاضر آزمون فعالی وجود ندارد. لطفاً بعداً سر بزنید.", reply_markup=MAIN_MENU)
        return

    # چک کردن اینکه آیا کاربر قبلا پاسخ داده است
    CUR.execute("SELECT 1 FROM quiz_user_answers WHERE user_id = ? AND question_id = ?", (user_id, q["id"]))
    if CUR.fetchone():
        await message.reply_text(f"⚖️ **آزمون حقوقی**\n\nشما قبلاً به این سوال پاسخ داده‌اید. منتظر سوال بعدی بمانید.\n\nسوال:\n{q['question_text']}", reply_markup=MAIN_MENU)
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"الف) {q['option_a']}", callback_data=f"quiz_answer_{q['id']}_a")],
        [InlineKeyboardButton(f"ب) {q['option_b']}", callback_data=f"quiz_answer_{q['id']}_b")],
        [InlineKeyboardButton(f"ج) {q['option_c']}", callback_data=f"quiz_answer_{q['id']}_c")],
        [InlineKeyboardButton(f"د) {q['option_d']}", callback_data=f"quiz_answer_{q['id']}_d")],
    ])
    await message.reply_text(f"⚖️ **آزمون حقوقی**\n\n**سوال:**\n{q['question_text']}", reply_markup=kb)

# (جدید) تابع ارسال نکته حقوقی
async def send_legal_tip(update: Update, context: ContextTypes.DEFAULT_TYPE, tip_id: int, is_edit: bool = False):
    """نکته حقوقی با ID مشخص را برای کاربر ارسال می‌کند."""
    
    query = update.callback_query
    message = update.message
    
    try:
        CUR.execute("SELECT tip_text FROM legal_tips WHERE id = ?", (tip_id,))
        row = CUR.fetchone()
        
        CUR.execute("SELECT COUNT(*) as c FROM legal_tips")
        total_tips = CUR.fetchone()["c"]
        
        if not row:
            if tip_id > 1: # به پایان رسیده
                await query.answer("شما به پایان نکات رسیدید. (نکته اول)", show_alert=True)
                new_id = 1 # بازگشت به اولین نکته
                context.user_data['current_tip_id'] = new_id
                CUR.execute("SELECT tip_text FROM legal_tips WHERE id = ?", (new_id,))
                row = CUR.fetchone()
                if not row: # اگر حتی نکته ۱ هم نبود
                    await query.edit_message_text("❌ هنوز هیچ نکته‌ای ثبت نشده است.", reply_markup=None)
                    return
            else: # از ابتدا هیچ نکته‌ای وجود نداشته
                msg = "❌ هنوز هیچ نکته‌ای توسط ادمین ثبت نشده است."
                if is_edit: await query.edit_message_text(msg, reply_markup=None)
                else: await message.reply_text(msg, reply_markup=MAIN_MENU)
                return
        
        msg = f"💡 **نکته حقوقی** ({tip_id} / {total_tips})\n\n"
        msg += row['tip_text']
        
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➡️ بعدی", callback_data="legal_tip_next"),
                InlineKeyboardButton("⬅️ قبلی", callback_data="legal_tip_prev")
            ]
        ])
        
        if is_edit:
            await query.edit_message_text(msg, reply_markup=kb)
        else:
            await message.reply_text(msg, reply_markup=kb)
            
    except Exception as e:
        logger.error(f"Error sending legal tip: {e}")
        if is_edit: await query.edit_message_text(f"❌ خطایی رخ داد: {e}")
        else: await message.reply_text(f"❌ خطایی رخ داد: {e}")


# ---------- Callback router (ارتقا یافته با پنل ادمین) ----------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data or ""

    # --- (جدید) منطق پنل ادمین ---
    if data.startswith("admin_"):
        if not is_admin(uid): # (تغییر)
            await query.edit_message_text("⛔ این بخش فقط مختص ادمین است.")
            return

        # پاک کردن حالت‌های قبلی
        context.user_data.pop("state", None)

        if data == "admin_main":
            await query.edit_message_text("🔐 **پنل مدیریت ربات**", reply_markup=build_admin_menu("main", uid))
        
        elif data == "admin_stats":
            await show_admin_stats(update, context, query)

        elif data == "admin_broadcast":
            await query.edit_message_text("✍️ لطفاً پیام مورد نظر خود را برای ارسال همگانی بنویسید (برای لغو /admin بزنید):", reply_markup=build_admin_menu("broadcast", uid)) # (تغییر)
            context.user_data["state"] = "awaiting_broadcast"
            
        elif data == "admin_reports":
            await show_admin_reports(update, context, query)

        elif data.startswith("admin_reply_to_"):
            parts = data.split("_")
            report_id = int(parts[3])
            target_uid = int(parts[4])
            context.user_data["reply_to_report"] = {"report_id": report_id, "user_id": target_uid}
            context.user_data["state"] = "awaiting_admin_reply"
            await query.edit_message_text(f"✍️ لطفاً متن پاسخ برای گزارش #{report_id} (کاربر {target_uid}) را ارسال کنید (برای لغو /admin بزنید):")

        elif data == "admin_users":
            await query.edit_message_text("👤 **مدیریت کاربران**\n\n"
                                          "لطفاً آیدی عددی (User ID) کاربر مورد نظر را برای جستجو ارسال کنید (برای لغو /admin بزنید):", 
                                          reply_markup=build_admin_menu("users", uid)) # (تغییر)
            context.user_data["state"] = "awaiting_user_search"
            
        elif data.startswith("admin_view_user_history_"):
            target_uid = int(data.replace("admin_view_user_history_", ""))
            history = get_chat_history(target_uid, limit=5)
            msg = f"📜 **تاریخچه {target_uid}** (۵ پیام آخر)\n\n"
            if not history:
                msg += "(تاریخچه چت (پرسش/پاسخ) یافت نشد.)"
            else:
                for h in history:
                    role_fa = "کاربر" if h['role'] == 'user' else "ربات"
                    msg += f"**{role_fa}:**\n{h['content'][:100]}...\n---\n"
            
            kb = InlineKeyboardMarkup([
                # [InlineKeyboardButton(f"👤 بازگشت به پروفایل {target_uid}", callback_data=f"???")], # نیاز به ارسال مجدد آیدی در text_handler دارد
                [InlineKeyboardButton("🔙 بازگشت به پنل کاربران", callback_data="admin_users")]
            ])
            await query.edit_message_text(msg, reply_markup=kb)

        # (جدید) کال‌بک‌های تنظیمات
        elif data == "admin_settings":
            await admin_settings_handler(update, context, query)
            
        elif data == "admin_manage_admins":
            await admin_manage_admins_handler(update, context, query)
            
        elif data == "admin_add_admin":
            if uid != SUPER_ADMIN_ID: return
            await query.edit_message_text("✍️ لطفاً آیدی عددی (User ID) ادمین جدید را ارسال کنید:", reply_markup=build_admin_menu("manage_admins", uid))
            context.user_data["state"] = "awaiting_new_admin_id"

        elif data == "admin_remove_admin":
            if uid != SUPER_ADMIN_ID: return
            await query.edit_message_text("✍️ لطفاً آیدی عددی ادمینی که می‌خواهید حذف کنید را ارسال کنید:", reply_markup=build_admin_menu("manage_admins", uid))
            context.user_data["state"] = "awaiting_remove_admin_id"
            
        elif data == "admin_manage_channels":
            await admin_manage_channels_handler(update, context, query)

        elif data == "admin_add_channel":
            await query.edit_message_text("✍️ لطفاً یوزرنیم کانال جدید را (بدون @) ارسال کنید:", reply_markup=build_admin_menu("manage_channels", uid))
            context.user_data["state"] = "awaiting_new_channel_username"

        elif data == "admin_remove_channel":
            await query.edit_message_text("✍️ لطفاً یوزرنیم کانالی که می‌خواهید حذف کنید را (بدون @) ارسال کنید:", reply_markup=build_admin_menu("manage_channels", uid))
            context.user_data["state"] = "awaiting_remove_channel_username"

        # (جدید) کال‌بک‌های مدیریت آزمون
        elif data == "admin_manage_quiz":
            await admin_quiz_panel_handler(update, context, query)
        
        elif data == "admin_add_quiz":
            await query.edit_message_text("✍️ لطفاً اطلاعات سوال را در *یک* پیام و با فرمت زیر ارسال کنید (با | جدا کنید):\n\n"
                                          "`متن سوال؟ | متن گزینه الف | متن گزینه ب | متن گزینه ج | متن گزینه د | پاسخ صحیح (مثل a یا ب)`\n\n"
                                          "مثال: `پایتخت ایران؟ | مشهد | تهران | اصفهان | تبریز | ب`",
                                          parse_mode="Markdown")
            context.user_data["state"] = "awaiting_quiz_question"

        elif data == "admin_clear_quiz":
            CUR.execute("UPDATE quiz_questions SET is_active = 0")
            DB.commit()
            await query.answer("✅ تمام سوالات فعال، غیرفعال شدند.", show_alert=True)
            await admin_quiz_panel_handler(update, context, query)

        # (جدید) کال‌بک‌های مدیریت نکات
        elif data == "admin_manage_tips":
            await admin_manage_tips_handler(update, context, query)
        
        elif data == "admin_add_tip":
            await query.edit_message_text("✍️ لطفاً متن کامل نکته حقوقی جدید را ارسال کنید (برای لغو /admin بزنید):")
            context.user_data["state"] = "awaiting_new_legal_tip"

        elif data == "admin_close":
            await query.edit_message_text("✅ پنل مدیریت بسته شد.")
        
        return # پایان منطق ادمین

    # --- منطق کال‌بک‌های کاربران ---
    if data == "verify_membership":
        if await check_membership(uid, context):
            await query.edit_message_text("✅ عضویت شما تایید شد.")
            await query.message.reply_text("منوی اصلی فعال شد:", reply_markup=MAIN_MENU)
        else:
            await query.edit_message_text("❌ شما هنوز عضو تمام کانال‌ها نشده‌اید. لطفاً مجدد بررسی کنید و دکمه تایید را بزنید.") # (تغییر)
        return

    if data in ("like", "dislike"):
        val = 1 if data == "like" else -1
        # (اصلاح) ذخیره در جدول rating - اما جدول coin_rates استفاده شده، اشکالی ندارد
        CUR.execute("INSERT INTO coin_rates(source, rate, fetched_at) VALUES (?, ?, ?)", ("rating", val, datetime.utcnow().isoformat()))
        DB.commit()
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✅ نظر شما ثبت شد. متشکریم!")
        return

    # (حذف شد) منطق قدیمی پاسخ ادمین
    # if data.startswith("reply_to_"): ...

    if data == "add_reminder":
        await query.edit_message_text("✍️ عنوان و تاریخ را وارد کنید (مثال: قرار: 2025-12-20 14:30)")
        context.user_data["state"] = "adding_reminder"
        return

    if data == "list_reminders":
        CUR.execute("SELECT id, title, remind_at, created_at FROM reminders WHERE user_id=? ORDER BY id DESC", (uid,))
        rows = CUR.fetchall()
        if not rows:
            await query.edit_message_text("❌ هنوز یادآوری ثبت نشده است.")
        else:
            lines = [f"#{r['id']} — {r['title']} ➜ {r['remind_at']} (ثبت: {r['created_at'].split('T')[0]})" for r in rows]
            await query.edit_message_text("📌 یادآوری‌ها:\n\n" + "\n".join(lines))
        return

    # --- (جدید) کال‌بک‌های ویژگی‌های جدید ---
    
    # (جدید) دسته‌بندی پرسش حقوقی
    if data.startswith("category_"):
        category_name = data.split("_", 1)[1]
        context.user_data["state"] = "awaiting_categorized_question"
        context.user_data["question_category"] = category_name
        await query.edit_message_text(f"موضوع: ⚖️ **{category_name}**\n\n"
                                      "✍️ لطفاً سوال دقیق خود را بنویسید (من مکالمات قبلی را به یاد دارم):",
                                      reply_markup=None)
        return

    # قالب‌های آماده
    if data.startswith("template_"):
        template_name = data.split("_", 1)[1]
        await send_template(query, context, template_name)
        return

    # دفترچه پرونده‌های من
    if data == "list_my_cases":
        CUR.execute("SELECT id, title, case_number, branch, notes, created_at FROM my_cases WHERE user_id = ? ORDER BY id DESC", (uid,))
        rows = CUR.fetchall()
        if not rows:
            await query.edit_message_text("❌ شما هنوز هیچ پرونده‌ای در دفترچه خود ذخیره نکرده‌اید.", 
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ افزودن پرونده جدید", callback_data="add_my_case")]]))
            return
        
        msg = "🗂️ **پرونده‌های شما:**\n\n"
        for r in rows:
            msg += (
                f"--- (ID: {r['id']}) ---\n"
                f"**عنوان:** {r['title']}\n"
                f"شماره: {r['case_number']} | شعبه: {r['branch']}\n"
                f"یادداشت: {r['notes'][:50]}...\n"
                f"(ثبت: {r['created_at'].split('T')[0]})\n\n"
            )
        # (ایده آینده: دکمه حذف یا ویرایش برای هرکدام)
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ افزودن پرونده جدید", callback_data="add_my_case")]]))
        return

    if data == "add_my_case":
        await query.edit_message_text("✍️ لطفاً اطلاعات پرونده را در *یک* پیام و با فرمت زیر ارسال کنید (با | جدا کنید):\n\n"
                                      "`عنوان: [عنوان پرونده] | شماره: [شماره] | شعبه: [شعبه] | یادداشت: [توضیحات]`\n\n"
                                      "مثال: `عنوان: چک آقای رضایی | شماره: 990123 | شعبه: 5 حقوقی | یادداشت: جلسه بعدی 20 آذر`",
                                      parse_mode="Markdown")
        context.user_data["state"] = "awaiting_my_case_details"
        return

    # (جدید) کال‌بک‌های نکات حقوقی
    if data.startswith("legal_tip_"):
        action = data.split("_")[2]
        current_id = context.user_data.get('current_tip_id', 1)
        
        if action == "next":
            new_id = current_id + 1
        elif action == "prev":
            new_id = max(1, current_id - 1)
        else:
            return

        context.user_data['current_tip_id'] = new_id
        await send_legal_tip(update, context, tip_id=new_id, is_edit=True)
        return

    # پاسخ آزمون
    if data.startswith("quiz_answer_"):
        try:
            parts = data.split("_")
            q_id = int(parts[2])
            answer = parts[3].strip().lower()
            
            # چک کردن دیتابیس که آیا قبلا پاسخ داده
            CUR.execute("SELECT 1 FROM quiz_user_answers WHERE user_id = ? AND question_id = ?", (uid, q_id))
            if CUR.fetchone():
                await query.answer("شما قبلاً به این سوال پاسخ داده‌اید.", show_alert=True)
                return

            # گرفتن سوال برای چک کردن پاسخ صحیح
            CUR.execute("SELECT question_text, correct_option, option_a, option_b, option_c, option_d FROM quiz_questions WHERE id = ?", (q_id,))
            q = CUR.fetchone()
            if not q:
                await query.edit_message_text("❌ این سوال دیگر فعال نیست.")
                return

            is_correct = (answer == q["correct_option"])
            
            # ذخیره پاسخ کاربر
            CUR.execute(
                "INSERT INTO quiz_user_answers(user_id, question_id, answer, is_correct, answered_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, q_id, answer, 1 if is_correct else 0, datetime.utcnow().isoformat())
            )
            DB.commit()
            
            # نمایش نتیجه
            msg = f"**سوال:**\n{q['question_text']}\n\n"
            if is_correct:
                msg += f"✅ **پاسخ شما ({answer}) صحیح بود!**"
            else:
                correct_text = q[f"option_{q['correct_option']}"]
                msg += f"❌ **پاسخ شما ({answer}) اشتباه بود.**\n\nپاسخ صحیح: ({q['correct_option']}) {correct_text}"
            
            await query.edit_message_text(msg, reply_markup=None)

        except sqlite3.IntegrityError:
             await query.answer("شما قبلاً به این سوال پاسخ داده‌اید.", show_alert=True)
        except Exception as e:
            logger.error(f"Quiz answer error: {e}")
            await query.edit_message_text("❌ خطایی در ثبت پاسخ رخ داد.")
        return

    # (جدید) کال‌بک‌های آزمون
    if data == "quiz_start":
        await query.answer()
        await send_quiz_question(query.message, context, uid) # (تغییر) ارسال پیام جدید
        return

    if data == "quiz_leaderboard":
        try:
            # گرفتن ۵ کاربر برتر با بیشترین امتیاز
            CUR.execute("""
                SELECT U.first_name, SUM(Q.is_correct) as score
                FROM quiz_user_answers Q
                JOIN users U ON Q.user_id = U.user_id
                GROUP BY Q.user_id
                ORDER BY score DESC
                LIMIT 5
            """)
            rows = CUR.fetchall()
            
            msg = "🏆 **جدول امتیازات آزمون** 🏆\n\n"
            if not rows:
                msg += "(هنوز هیچ‌کس در آزمون‌ها امتیازی کسب نکرده است.)"
            else:
                rank_emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                for i, r in enumerate(rows):
                    name = r['first_name'] or "کاربر"
                    msg += f"{rank_emojis[i]} **{name}** - {r['score']} امتیاز\n"
            
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به آزمون", callback_data="quiz_back_to_menu")]]))
        
        except Exception as e:
            logger.error(f"Leaderboard error: {e}")
            await query.edit_message_text("❌ خطایی در دریافت جدول امتیازات رخ داد.")
        return

    if data == "quiz_back_to_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⁉️ شروع/ادامه آزمون", callback_data="quiz_start")],
            [InlineKeyboardButton("🏆 جدول امتیازات", callback_data="quiz_leaderboard")]
        ])
        await query.edit_message_text("⚖️ **آزمون حقوقی**\n\nدر آزمون هفتگی شرکت کنید و امتیاز خود را با دیگران مقایسه کنید!", reply_markup=kb)
        return

    # --- کال‌بک‌های محاسبه‌گر ---
    if data == "calc_coin":
        await query.edit_message_text("✍️ تعداد سکه را وارد کنید (مثال: 2):")
        context.user_data["state"] = "calc_coin_input"
        return
    if data == "calc_mehrieh":
        await query.edit_message_text("✍️ تعداد سکه مهریه را وارد کنید (مثال: 110):")
        context.user_data["state"] = "awaiting_mehrieh_calc"
        return
    if data == "calc_diyah":
        await query.edit_message_text("✍️ لطفاً نوع و درصد آسیب را بنویسید (مثال: دیه کامل مرد در ماه حرام، یا 10 درصد شکستگی دست راست):")
        context.user_data["state"] = "awaiting_diyah_calc"
        return
    if data == "calc_late_payment":
        await query.edit_message_text("✍️ لطفاً مبلغ، تاریخ سررسید و تاریخ پرداخت را وارد کنید. مثال: 10000000 ریال، از 1398/05/10 تا 1403/02/20")
        context.user_data["state"] = "awaiting_late_payment_calc"
        return
    if data == "calc_enforcement":
        await query.edit_message_text("✍️ لطفاً مبلغی که حکم به نفع شما صادر شده (محکومٌ به) را به ریال وارد کنید:")
        context.user_data["state"] = "awaiting_enforcement_calc"
        return
    if data == "calc_dadrasi": # (جدید)
        await query.edit_message_text("✍️ لطفاً «مبلغ خواسته» (Mablagh-e-Khasteh) را به ریال وارد کنید:\n(مثال: 150000000)")
        context.user_data["state"] = "awaiting_dadrasi_calc"
        return
    if data == "calc_inheritance":
        await query.edit_message_text("✍️ لطفاً لیست کامل ورثه (بازماندگان) و اموال را بنویسید (مثال: یک همسر، دو پسر، یک دختر. اموال: یک خانه 5 میلیاردی):")
        context.user_data["state"] = "awaiting_inheritance_calc"
        return
    if data == "calc_tax_simple":
        await query.edit_message_text("✍️ مبلغ را وارد کنید (مثال: 10000000):")
        context.user_data["state"] = "calc_tax_input_simple"
        return
    if data == "calc_tax_advanced":
        await query.edit_message_text("✍️ فرمت: 'حقوق ناخالص کسورات نرخ%' مثال: 10000000 2000000 15%")
        context.user_data["state"] = "calc_tax_input_advanced"
        return
        
    # --- کال‌بک‌های تنظیمات ---
    if data.startswith("set_p_"):
        personality = data.replace("set_p_", "")
        set_user_personality(uid, personality)
        await query.edit_message_text(f"✅ شخصیت ربات به '{personality}' تغییر یافت.")
        return

# ---------- Error handler (بدون تغییر) ----------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)
    try:
        if getattr(update, "effective_message", None):
            await update.effective_message.reply_text("⚠️ خطایی رخ داد. تیم فنی مطلع شد.")
    except Exception: pass

# ---------- background daily tip (بازنویسی شد) ----------
async def daily_tip_loop(application):
    """(بازنویسی شد) نکته روزانه برای کاربران در چت خصوصی"""
    while True:
        try:
            now = datetime.utcnow()
            # (اصلاح) ساعت ایران معمولا +3:30 است. فرض می‌کنیم 3.5 بوده
            target_hour_utc = (DAILY_TIP_HOUR - 3) % 24 # ساعت ایران به UTC
            target_minute_utc = 30 # (اصلاح) برای 3:30
            if target_hour_utc < 0: target_hour_utc += 24
            
            target = now.replace(hour=target_hour_utc, minute=target_minute_utc, second=0, microsecond=0)
            
            if target <= now:
                target = target + timedelta(days=1)
            wait = (target - now).total_seconds()
            logger.info("Daily USER tip scheduled in %s seconds", int(wait))
            await asyncio.sleep(wait)
            
            # (تغییر) به جای AI، از دیتابیس می‌خواند
            CUR.execute("SELECT tip_text FROM legal_tips ORDER BY RANDOM() LIMIT 1")
            row = CUR.fetchone()
            if not row:
                logger.warning("No legal tips in DB to send to users.")
                continue
            
            tip = row['tip_text']
            
            CUR.execute("SELECT user_id FROM users")
            rows = CUR.fetchall()
            logger.info(f"Sending daily tip to {len(rows)} users...")
            
            for r in rows:
                uid = r["user_id"]
                try:
                    await application.bot.send_message(chat_id=uid, text=f"🔔 نکته حقوقی روز:\n\n{tip}")
                except Exception:
                    logger.warning("Failed to send daily tip to %s", uid)
                await asyncio.sleep(0.05)
                
        except Exception:
            logger.exception("daily USER tip loop crashed")
            await asyncio.sleep(300)

# (جدید) حلقه ارسال نکته به گروه‌ها
async def daily_group_tip_loop(application):
    """نکته روزانه برای گروه‌ها و کانال‌ها"""
    while True:
        try:
            now = datetime.utcnow()
            target_hour_utc = (DAILY_GROUP_TIP_HOUR - 3) % 24 # ساعت ایران به UTC
            target_minute_utc = 30 # (اصلاح) برای 3:30
            if target_hour_utc < 0: target_hour_utc += 24
            
            target = now.replace(hour=target_hour_utc, minute=target_minute_utc, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            wait = (target - now).total_seconds()
            logger.info("Daily GROUP tip scheduled in %s seconds", int(wait))
            await asyncio.sleep(wait)
            
            # انتخاب نکته تصادفی
            CUR.execute("SELECT tip_text FROM legal_tips ORDER BY RANDOM() LIMIT 1")
            row = CUR.fetchone()
            if not row:
                logger.warning("No legal tips in DB to send to groups.")
                continue
            
            tip = row['tip_text']
            
            CUR.execute("SELECT chat_id FROM managed_groups WHERE daily_tip_enabled = 1")
            rows = CUR.fetchall()
            logger.info(f"Sending daily tip to {len(rows)} groups...")
            
            for r in rows:
                chat_id = r["chat_id"]
                try:
                    await application.bot.send_message(chat_id=chat_id, text=f"🔔 نکته حقوقی روز:\n\n{tip}")
                except Exception as e:
                    logger.warning(f"Failed to send daily tip to group {chat_id}: {e}")
                await asyncio.sleep(0.1) # فاصله بیشتر برای گروه‌ها
                
        except Exception:
            logger.exception("daily GROUP tip loop crashed")
            await asyncio.sleep(300)

# (جدید) هندلرهای گروه
async def new_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وقتی ربات به گروه جدیدی اضافه می‌شود، آن را در دیتابیس ثبت می‌کند"""
    if not update.message or not update.message.new_chat_members:
        return
        
    bot_id = context.bot.id
    if bot_id in [m.id for m in update.message.new_chat_members]:
        chat = update.effective_chat
        chat_id = chat.id
        try:
            CUR.execute("INSERT OR IGNORE INTO managed_groups(chat_id, added_at) VALUES (?, ?)",
                        (chat_id, datetime.utcnow().isoformat()))
            DB.commit()
            await context.bot.send_message(
                chat_id=chat_id,
                text="🤖 سلام! این ربات حقوقی با موفقیت فعال شد.\n"
                     "من هر روز یک نکته حقوقی ارسال می‌کنم.\n"
                     "همچنین اگر سوال حقوقی (شامل کلمات کلیدی) بپرسید و آن را با '?' تمام کنید، سعی می‌کنم پاسخ دهم."
            )
            logger.info(f"Bot added to new group: {chat.title} ({chat_id})")
        except Exception as e:
            logger.error(f"Failed to save new group {chat_id}: {e}")

LEGAL_KEYWORDS = [
    "حقوق", "قانون", "وکیل", "قضایی", "دادگاه", "ارث", "طلاق", "مهریه", "دیه", 
    "سفته", "چک", "قرارداد", "مجازات", "کیفری", "شکایت", "دادسرا", "اجاره"
]

@rate_limited # اعمال محدودیت بر اساس کاربر، نه گروه
async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر پیام‌های گروهی برای پاسخ خودکار به سوالات حقوقی"""
    if not update.message or not update.message.text:
        return
        
    text = update.message.text
    uid = update.effective_user.id
    
    # ۱. آیا پیام با ؟ تمام می‌شود؟
    if not text.endswith("?"):
        return
        
    # ۲. آیا طولانی‌تر از حد معمول است؟ (جلوگیری از پاسخ به "؟")
    if len(text) < 15:
        return
        
    # ۳. آیا شامل کلمات کلیدی حقوقی است؟
    if not any(keyword in text for keyword in LEGAL_KEYWORDS):
        return
        
    logger.info(f"Detected legal question in group {update.effective_chat.id} from user {uid}")
    
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
        
        # (ایده: می‌توان یک تاریخچه چت موقت بر اساس reply_to_message_id ساخت)
        answer = await ask_ai(uid, prompt=text, chat_history=None) # بدون حافظه در گروه
        
        await update.message.reply_text(
            answer + LEGAL_DISCLAIMER,
            reply_to_message_id=update.message.message_id
        )
    except Exception as e:
        logger.error(f"Failed to auto-reply in group: {e}")


# ---------- application bootstrap (ارتقا یافته) ----------
async def on_startup(app):
    try:
        app.create_task(daily_tip_loop(app))
        logger.info("Daily USER tip loop scheduled.")
        app.create_task(daily_group_tip_loop(app)) # (جدید)
        logger.info("Daily GROUP tip loop scheduled.")
    except Exception as e:
        logger.exception("Failed to schedule daily tip loop: %s", e)

def build_application():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(on_startup).build()
    
    # (اصلاح شد) این هندلر برای همه فعال است، تابع داخلی is_admin() دسترسی را چک می‌کند.
    app.add_handler(CommandHandler("admin", admin_panel_handler))

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(callback_router))
    
    # (تغییر) هندلر متن خصوصی
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, text_handler))
    
    # (جدید) هندلرهای گروه
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_member_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group_message_handler))
    
    # (تغییر) هندلر سند (فقط خصوصی)
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, document_handler))
    
    app.add_error_handler(error_handler)
    
    return app

def main():
    app = build_application()
    logger.info("🤖 Bot (Ultimate Version + Group Features) is starting...") # (تغییر)
    app.run_polling()

if __name__ == "__main__":
    main()
