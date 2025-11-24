import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice
)
from dotenv import load_dotenv
import openai

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BOT_USERNAME = os.getenv("BOT_USERNAME")

openai.api_key = OPENAI_API_KEY

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

FREE_LIMIT = 10
INDIVIDUAL_PRICE = 199
DOC_PRICE = 50
PACK_5 = 75
PACK_10 = 129
PACK_20 = 239

users = defaultdict(lambda: {
    "limit": FREE_LIMIT,
    "last_reset": datetime.now(),
    "consult_active": False,
})

SYSTEM_PROMPT = """
Ты — «Адвокат X», профессиональный юридический помощник.
Говоришь официально, строго по закону, кратко и без воды.
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
            {"role": "user", "content": f"Дай полный подробный разбор:\n{text}"}
        ]
    )
    return r["choices"][0]["message"]["content"].strip()


def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
        [InlineKeyboardButton(text="Индивидуальная консультация", callback_data="start_individual")],
        [InlineKeyboardButton(text="Меню", callback_data="menu")]
    ])


def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 сообщений — 75 ₽", callback_data="buy5")],
        [InlineKeyboardButton(text="10 сообщений — 129 ₽", callback_data="buy10")],
        [InlineKeyboardButton(text="20 сообщений — 239 ₽", callback_data="buy20")],
        [InlineKeyboardButton(text="Индивидуальная консультация — 199 ₽", callback_data="start_individual")]
    ])


async def send_invoice(message: Message, title, description, amount, payload):
    prices = [LabeledPrice(label=title, amount=amount * 100)]
    await bot.send_invoice(
        chat_id=message.chat.id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # пусто, потому что Telegram сам знает Юкассу
        currency="RUB",
        prices=prices,
        need_email=False
    )


@dp.message(CommandStart())
async def start(message: Message):
    u = users[message.from_user.id]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    await message.answer(
        f"Здравствуйте! Я — Адвокат X.\n"
        f"Ваш бесплатный лимит: {u['limit']} сообщений.\n"
        f"Опишите проблему или выберите режим.",
        reply_markup=kb_main()
    )


@dp.callback_query(F.data == "menu")
async def menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())


@dp.callback_query(F.data == "start_individual")
async def start_individual(call: CallbackQuery):
    await send_invoice(
        call.message,
        "Индивидуальная консультация",
        "Подробный разбор, ведём до решения.",
        INDIVIDUAL_PRICE,
        payload="consult"
    )


@dp.callback_query(F.data == "buy5")
async def buy_5(call: CallbackQuery):
    await send_invoice(call.message, "5 сообщений", "Пополнение лимита", PACK_5, "pack5")


@dp.callback_query(F.data == "buy10")
async def buy_10(call: CallbackQuery):
    await send_invoice(call.message, "10 сообщений", "Пополнение лимита", PACK_10, "pack10")


@dp.callback_query(F.data == "buy20")
async def buy_20(call: CallbackQuery):
    await send_invoice(call.message, "20 сообщений", "Пополнение лимита", PACK_20, "pack20")


@dp.message(F.text)
async def handler(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    if u["limit"] <= 0:
        await message.answer("Лимит закончился.", reply_markup=kb_menu())
        return

    u["limit"] -= 1
    ans = await ask_short(message.text)
    await message.answer(ans)


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())






