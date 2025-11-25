import os
import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict

import openai
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

FREE_LIMIT = 10
PRICE_INDIV = 199
PRICE_PACK5 = 75
PRICE_PACK10 = 129
PRICE_PACK20 = 239

users = defaultdict(
    lambda: {
        "free_left": FREE_LIMIT,
        "paid_left": 0,
        "last_reset": datetime.now(),
        "consult_active": False,
    }
)

SYSTEM_PROMPT = """
Ты — «Адвокат X», профессиональный юрист по праву РФ и опытный процессуалист.
Ты даёшь глубокие, структурированные ответы, приводишь стратегию, правовые основания,
пошаговые действия, и всегда спрашиваешь уточняющие детали, если они нужны.
"""


def reset_limits(user_id: int):
    u = users[user_id]
    now = datetime.now()
    if now - u["last_reset"] >= timedelta(days=1):
        u["free_left"] = FREE_LIMIT
        u["last_reset"] = now


async def ask_model(messages: list) -> str:
    def _call():
        return openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.2,
        )

    resp = await asyncio.to_thread(_call)
    return resp["choices"][0]["message"]["content"].strip()


async def ask_short(text: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Дай короткий, но сильный юридический ответ:\n{text}"},
    ]
    return await ask_model(msgs)


async def ask_full(text: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Сделай полный профессиональный юридический разбор:\n{text}"},
    ]
    return await ask_model(msgs)


def kb_main():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
            [InlineKeyboardButton(text=f"Индивидуальная консультация — {PRICE_INDIV} ₽", callback_data="buy_indiv")],
            [InlineKeyboardButton(text="Меню услуг", callback_data="menu")],
        ]
    )


def kb_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"5 сообщений — {PRICE_PACK5} ₽", callback_data="buy5")],
            [InlineKeyboardButton(text=f"10 сообщений — {PRICE_PACK10} ₽", callback_data="buy10")],
            [InlineKeyboardButton(text=f"20 сообщений — {PRICE_PACK20} ₽", callback_data="buy20")],
            [InlineKeyboardButton(text=f"Индивидуальная консультация — {PRICE_INDIV} ₽", callback_data="buy_indiv")],
        ]
    )


async def create_invoice(chat_id: int, title: str, description: str, payload: str, amount_rub: int):
    prices = [LabeledPrice(label=title, amount=amount_rub * 100)]

    provider_data = {
        "receipt": {
            "items": [
                {
                    "description": title,
                    "quantity": 1,
                    "amount": {"value": float(amount_rub), "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
            "tax_system_code": 1
        }
    }

    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        need_email=True,
        send_email_to_provider=True,
        provider_data=provider_data
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    reset_limits(uid)
    u = users[uid]
    await message.answer(
        "Здравствуйте! Я — «Адвокат X».\n"
        f"Бесплатный лимит: {u['free_left']} сообщений.\n"
        f"Оплаченных: {u['paid_left']}.\n"
        "Опишите вашу ситуацию.",
        reply_markup=kb_main(),
    )


@dp.callback_query(F.data == "mode_basic")
async def cb_mode_basic(call: CallbackQuery):
    await call.message.answer("Обычная консультация включена.")
    await call.answer()


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())
    await call.answer()


@dp.callback_query(F.data == "buy_indiv")
async def cb_buy_indiv(call: CallbackQuery):
    await create_invoice(
        chat_id=call.from_user.id,
        title="Индивидуальная консультация",
        description="Подробное юридическое сопровождение.",
        payload="individual",
        amount_rub=PRICE_INDIV,
    )
    await call.answer()


@dp.callback_query(F.data == "buy5")
async def cb_buy5(call: CallbackQuery):
    await create_invoice(
        chat_id=call.from_user.id,
        title="Пакет 5 сообщений",
        description="Плюс 5 сообщений к лимиту.",
        payload="pack5",
        amount_rub=PRICE_PACK5,
    )
    await call.answer()


@dp.callback_query(F.data == "buy10")
async def cb_buy10(call: CallbackQuery):
    await create_invoice(
        chat_id=call.from_user.id,
        title="Пакет 10 сообщений",
        description="Плюс 10 сообщений к лимиту.",
        payload="pack10",
        amount_rub=PRICE_PACK10,
    )
    await call.answer()


@dp.callback_query(F.data == "buy20")
async def cb_buy20(call: CallbackQuery):
    await create_invoice(
        chat_id=call.from_user.id,
        title="Пакет 20 сообщений",
        description="Плюс 20 сообщений к лимиту.",
        payload="pack20",
        amount_rub=PRICE_PACK20,
    )
    await call.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)


@dp.message(F.successful_payment)
async def on_successful_payment(message: Message):
    uid = message.from_user.id
    u = users[uid]

    p = message.successful_payment.invoice_payload

    if p == "individual":
        u["consult_active"] = True
        await message.answer("Индивидуальная консультация активирована. Опишите ситуацию.")
    elif p == "pack5":
        u["paid_left"] += 5
        await message.answer("Добавлено 5 сообщений.")
    elif p == "pack10":
        u["paid_left"] += 10
        await message.answer("Добавлено 10 сообщений.")
    elif p == "pack20":
        u["paid_left"] += 20
        await message.answer("Добавлено 20 сообщений.")


@dp.message(F.text)
async def on_message(message: Message):
    uid = message.from_user.id
    text = message.text
    u = users[uid]

    reset_limits(uid)

    if u["consult_active"]:
        ans = await ask_full(text)
        await message.answer(ans)
        return

    total = u["free_left"] + u["paid_left"]
    if total <= 0:
        await message.answer(
            "Лимит исчерпан. Пополните его.",
            reply_markup=kb_menu(),
        )
        return

    if u["free_left"] > 0:
        u["free_left"] -= 1
    else:
        u["paid_left"] -= 1

    ans = await ask_short(text)
    await message.answer(
        f"{ans}\n\n"
        f"Бесплатных: {u['free_left']} | Платных: {u['paid_left']}",
        reply_markup=kb_menu(),
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())












