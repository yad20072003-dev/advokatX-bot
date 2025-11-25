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
    InlineKeyboardButton
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

openai.api_key = OPENAI_API_KEY
Configuration.account_id = SHOP_ID
Configuration.secret_key = SECRET_KEY

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

FREE_LIMIT = 10
PRICE_INDIV = 199
PRICE_DOC = 50
PACK5 = 75
PACK10 = 129
PACK20 = 239

users = defaultdict(lambda: {
    "free": FREE_LIMIT,
    "paid": 0,
    "last_reset": datetime.now(),
    "consult": False
})

SYSTEM_PROMPT = """
Ты — «Адвокат X», профессиональный юрист РФ.
Отвечай кратко, чётко, юридически корректно.
"""


def create_doc(text, uid):
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    name = f"doc_{uid}_{int(datetime.now().timestamp())}.docx"
    doc.save(name)
    return name


async def ask_short(text):
    r = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ]
    )
    return r["choices"][0]["message"]["content"]


async def ask_full(text):
    r = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Подробно разберись:\n\n{text}"}
        ]
    )
    return r["choices"][0]["message"]["content"]


def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="basic")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {PRICE_INDIV} ₽", callback_data="buy_indiv")],
        [InlineKeyboardButton(text="Меню", callback_data="menu")]
    ])


def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"5 сообщений — {PACK5} ₽", callback_data="buy5")],
        [InlineKeyboardButton(text=f"10 сообщений — {PACK10} ₽", callback_data="buy10")],
        [InlineKeyboardButton(text=f"20 сообщений — {PACK20} ₽", callback_data="buy20")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {PRICE_INDIV} ₽", callback_data="buy_indiv")]
    ])


def kb_doc():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Подготовить документ — {PRICE_DOC} ₽", callback_data="buy_doc")]
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
        "metadata": {"user": uid, "service": service}
    })
    return payment.confirmation.confirmation_url


@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["free"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    await message.answer(
        "Здравствуйте! Я — Адвокат X.\n"
        "Отвечаю по закону РФ и помогаю выстроить стратегию.\n\n"
        f"Бесплатно сегодня: {u['free']} сообщений.\n"
        f"Оплачено: {u['paid']}.\n",
        reply_markup=kb_main()
    )


@dp.callback_query(F.data == "basic")
async def basic(call: CallbackQuery):
    await call.message.answer("Обычный режим включён.")
    await call.answer()


@dp.callback_query(F.data == "menu")
async def menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())
    await call.answer()


@dp.callback_query(F.data == "buy_indiv")
async def buy_indiv(call: CallbackQuery):
    url = create_payment(PRICE_INDIV, "Individual consultation", call.from_user.id, "indiv")
    await call.message.answer("Оплатите консультацию:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
    ))
    await call.answer()


@dp.callback_query(F.data == "buy_doc")
async def buy_doc(call: CallbackQuery):
    url = create_payment(PRICE_DOC, "Document", call.from_user.id, "doc")
    await call.message.answer("Оплатите документ:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
    ))
    await call.answer()


@dp.callback_query(F.data.in_(["buy5", "buy10", "buy20"]))
async def buy_pack(call: CallbackQuery):
    uid = call.from_user.id
    if call.data == "buy5":
        amount = PACK5
        service = "pack5"
    elif call.data == "buy10":
        amount = PACK10
        service = "pack10"
    else:
        amount = PACK20
        service = "pack20"

    url = create_payment(amount, "Message pack", uid, service)
    await call.message.answer("Оплатите пакет:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
    ))
    await call.answer()


@dp.message(F.text)
async def text_handler(message: Message):
    uid = message.from_user.id
    u = users[uid]
    text = message.text

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["free"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    if u["consult"]:
        ans = await ask_full(text)
        await message.answer(ans, reply_markup=kb_doc())
        return

    if u["free"] <= 0 and u["paid"] <= 0:
        await message.answer(
            "У вас закончились сообщения.\nВыберите пакет:",
            reply_markup=kb_menu()
        )
        return

    if u["free"] > 0:
        u["free"] -= 1
    else:
        u["paid"] -= 1

    ans = await ask_short(text)
    await message.answer(ans, reply_markup=kb_menu())


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())











