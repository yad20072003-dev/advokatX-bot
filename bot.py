import os
import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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
    "consult_active": False
})

SYSTEM_PROMPT = "Ты — Адвокат X, юридический помощник по законодательству РФ."


async def ask_short(text):
    r = await asyncio.to_thread(
        openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ]
    )
    return r["choices"][0]["message"]["content"].strip()


async def ask_full(text):
    r = await asyncio.to_thread(
        openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Развернуто ответь на вопрос: {text}"}
        ]
    )
    return r["choices"][0]["message"]["content"].strip()


def create_doc(text, uid):
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    filename = f"doc_{uid}_{datetime.now().timestamp()}.docx"
    doc.save(filename)
    return filename


def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="basic")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE} ₽)", callback_data="consult")],
        [InlineKeyboardButton(text="Меню", callback_data="menu")]
    ])


def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"5 сообщений – {PACK_5} ₽", callback_data="buy5")],
        [InlineKeyboardButton(text=f"10 сообщений – {PACK_10} ₽", callback_data="buy10")],
        [InlineKeyboardButton(text=f"20 сообщений – {PACK_20} ₽", callback_data="buy20")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация – {INDIVIDUAL_PRICE} ₽", callback_data="consult")]
    ])


def create_payment(amount, desc, uid, service):
    payment = Payment.create({
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": PAY_RETURN_URL},
        "capture": True,
        "description": desc,
        "metadata": {"user_id": uid, "service": service}
    })
    return payment.confirmation.confirmation_url


@dp.message(CommandStart())
async def start(message: Message):
    u = users[message.from_user.id]

    if datetime.now() - u["last_reset"] > timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    await message.answer(
        f"Здравствуйте! Я — Адвокат X.\n"
        f"Ваш бесплатный лимит: {u['limit']} сообщений.",
        reply_markup=kb_main()
    )


@dp.callback_query(F.data == "basic")
async def basic(call: CallbackQuery):
    users[call.from_user.id]["mode"] = "free"
    await call.message.answer("Режим обычных консультаций включён.")


@dp.callback_query(F.data == "menu")
async def menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())


@dp.callback_query(F.data == "consult")
async def buy_consult(call: CallbackQuery):
    url = create_payment(INDIVIDUAL_PRICE, "Individual consultation", call.from_user.id, "consult")
    await call.message.answer(
        "Оплатите индивидуальную консультацию:",
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
async def messages(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] > timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    if u["consult_active"]:
        ans = await ask_full(message.text)
        await message.answer(ans)
        return

    if u["limit"] <= 0:
        await message.answer("Лимит закончился.", reply_markup=kb_menu())
        return

    u["limit"] -= 1
    ans = await ask_short(message.text)
    await message.answer(ans, reply_markup=kb_menu())


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())







