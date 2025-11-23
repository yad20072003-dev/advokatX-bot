import os
import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile
)
from dotenv import load_dotenv
import openai
from docx import Document
from yookassa import Payment, Configuration

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
PAY_RETURN_URL = os.getenv("PAY_RETURN_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME")

openai.api_key = OPENAI_API_KEY
Configuration.account_id = SHOP_ID
Configuration.secret_key = SECRET_KEY

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)

FREE_LIMIT = 10
INDIVIDUAL_PRICE = 199
DOC_PRICE = 50
PACK_5 = 75
PACK_10 = 129
PACK_20 = 239

users = defaultdict(lambda: {
    "limit": FREE_LIMIT,
    "last_reset": datetime.now(),
    "mode": "free",
    "last_q": None,
    "last_a": None,
    "consult_active": False,
    "consult_until": None,
    "consult_msgs": 0
})

SYSTEM_PROMPT = """
Ты — «Адвокат X», профессиональный юридический помощник.
Ты отвечаешь официально, по закону РФ, чётко и без лишней воды.
Ты не являешься адвокатом или представителем в суде.
"""


async def ask_short(text):
    r = await asyncio.to_thread(openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ]
    )
    return r["choices"][0]["message"]["content"].strip()


async def ask_full(text):
    r = await asyncio.to_thread(openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Сделай подробный анализ ситуации:\n{text}"}
        ]
    )
    return r["choices"][0]["message"]["content"].strip()


def make_doc(text, uid):
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    fname = f"doc_{uid}_{datetime.now().timestamp()}.docx"
    doc.save(fname)
    return fname


def kb_main():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE} ₽)", callback_data="start_individual")],
        [InlineKeyboardButton(text="Меню", callback_data="menu")]
    ])
    return kb


def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Пополнить 5 сообщений — {PACK_5} ₽", callback_data="buy5")],
        [InlineKeyboardButton(text=f"Пополнить 10 сообщений — {PACK_10} ₽", callback_data="buy10")],
        [InlineKeyboardButton(text=f"Пополнить 20 сообщений — {PACK_20} ₽", callback_data="buy20")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="start_individual")]
    ])


def need_doc_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Подготовить документ — {DOC_PRICE} ₽", callback_data="paid_doc")]
    ])


def create_payment(amount, description, uid, service):
    payment = Payment.create({
        "amount": {
            "value": f"{amount:.2f}",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": PAY_RETURN_URL
        },
        "capture": True,
        "description": description,
        "metadata": {
            "user_id": uid,
            "service": service
        }
    })
    return payment.confirmation.confirmation_url


@dp.message(CommandStart())
async def start(message: Message):
    u = users[message.from_user.id]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    await message.answer(
        f"Здравствуйте! Я — Адвокат X.\n"
        f"Я помогу разобраться в вашей юридической ситуации.\n\n"
        f"Бесплатный лимит: {u['limit']} сообщений.\n"
        f"Выберите режим или опишите проблему.",
        reply_markup=kb_main()
    )


@dp.callback_query(F.data == "mode_basic")
async def mode_basic(call: CallbackQuery):
    users[call.from_user.id]["mode"] = "free"
    await call.message.answer("Режим обычных консультаций включён.")


@dp.callback_query(F.data == "menu")
async def menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())


@dp.callback_query(F.data == "start_individual")
async def start_individual(call: CallbackQuery):
    u = users[call.from_user.id]

    if u["consult_active"]:
        await call.message.answer("У вас уже активна индивидуальная консультация.")
        return

    url = create_payment(
        INDIVIDUAL_PRICE,
        "Individual consultation",
        call.from_user.id,
        "individual"
    )

    await call.message.answer(
        "После оплаты я проведу вас до полного решения вопроса.\n"
        "Пожалуйста, оплатите консультацию:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
        )
    )


@dp.callback_query(F.data == "paid_doc")
async def paid_doc(call: CallbackQuery):
    url = create_payment(
        DOC_PRICE,
        "Document",
        call.from_user.id,
        "doc"
    )
    await call.message.answer(
        "Оплатите подготовку документа:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
        )
    )


@dp.callback_query(F.data == "buy5")
async def buy5(call: CallbackQuery):
    url = create_payment(PACK_5, "5 messages", call.from_user.id, "pack5")
    await call.message.answer("Оплатите пакет:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
    ))


@dp.callback_query(F.data == "buy10")
async def buy10(call: CallbackQuery):
    url = create_payment(PACK_10, "10 messages", call.from_user.id, "pack10")
    await call.message.answer("Оплатите пакет:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
    ))


@dp.callback_query(F.data == "buy20")
async def buy20(call: CallbackQuery):
    url = create_payment(PACK_20, "20 messages", call.from_user.id, "pack20")
    await call.message.answer("Оплатите пакет:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
    ))


@dp.message(F.text)
async def message_handler(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    if u["consult_active"]:
        ans = await ask_full(message.text)
        u["last_q"] = message.text
        u["last_a"] = ans
        u["consult_msgs"] += 1
        await message.answer(ans, reply_markup=need_doc_button())
        return

    if u["limit"] <= 0:
        await message.answer(
            "Ваш дневной лимит исчерпан. Он обновится через сутки.",
            reply_markup=kb_menu()
        )
        return

    u["limit"] -= 1
    ans = await ask_short(message.text)
    u["last_q"] = message.text
    u["last_a"] = ans

    await message.answer(
        f"{ans}\n\nОсталось: {u['limit']} сообщений.",
        reply_markup=kb_menu()
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
