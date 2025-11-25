import os
import asyncio
import logging
import json
from datetime import datetime, timedelta
from collections import defaultdict

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

from openai import OpenAI

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN")

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

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
Ты — «Адвокат X», профессиональный юрист по праву РФ.
Структура ответа:
1) Краткий вывод.
2) Правовое основание (статьи только когда уверен).
3) Варианты решения.
4) Риски.
5) Пошаговый план.
Давай практичные, чёткие, профессиональные ответы.
"""


def reset_limits(uid):
    u = users[uid]
    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["free_left"] = FREE_LIMIT
        u["last_reset"] = datetime.now()


async def ask_model(messages):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.2
    )
    return response.choices[0].message.content.strip()


async def ask_short(text):
    return await ask_model([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Дай короткий ответ (5–8 предложений):\n{text}"}
    ])


async def ask_full(text):
    return await ask_model([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Дай развёрнутую профессиональную консультацию:\n{text}"}
    ])


def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="basic")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {PRICE_INDIV} ₽", callback_data="buy_indiv")],
        [InlineKeyboardButton(text="Меню услуг", callback_data="menu")]
    ])


def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"5 сообщений — {PRICE_PACK5} ₽", callback_data="buy5")],
        [InlineKeyboardButton(text=f"10 сообщений — {PRICE_PACK10} ₽", callback_data="buy10")],
        [InlineKeyboardButton(text=f"20 сообщений — {PRICE_PACK20} ₽", callback_data="buy20")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {PRICE_INDIV} ₽", callback_data="buy_indiv")],
    ])


async def create_invoice(chat_id, title, description, payload, amount_rub):
    prices = [LabeledPrice(label=title, amount=amount_rub * 100)]

    provider_data = {
        "receipt": {
            "items": [
                {
                    "description": title,
                    "quantity": 1,
                    "amount": {
                        "value": amount_rub,
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
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

        provider_data=json.dumps(provider_data),
        is_flexible=False
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    reset_limits(uid)
    u = users[uid]

    await message.answer(
        "Здравствуйте! Я — «Адвокат X», ваш юридический ИИ-помощник.\n\n"
        f"Бесплатный дневной лимит: {u['free_left']} сообщений.\n"
        f"Оплаченных сообщений: {u['paid_left']}.\n"
        "Опишите вашу ситуацию:",
        reply_markup=kb_main()
    )


@dp.callback_query(F.data == "basic")
async def cb_basic(call: CallbackQuery):
    await call.message.answer("Режим обычной консультации активирован.")
    await call.answer()


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())
    await call.answer()


@dp.callback_query(F.data == "buy_indiv")
async def cb_indiv(call: CallbackQuery):
    await create_invoice(
        chat_id=call.from_user.id,
        title="Индивидуальная консультация",
        description="Подробный юридический разбор одного вопроса.",
        payload="individual",
        amount_rub=PRICE_INDIV
    )
    await call.answer()


@dp.callback_query(F.data == "buy5")
async def cb_buy5(call: CallbackQuery):
    await create_invoice(call.from_user.id, "Пакет 5 сообщений", "5 доп. сообщений", "pack5", PRICE_PACK5)
    await call.answer()


@dp.callback_query(F.data == "buy10")
async def cb_buy10(call: CallbackQuery):
    await create_invoice(call.from_user.id, "Пакет 10 сообщений", "10 доп. сообщений", "pack10", PRICE_PACK10)
    await call.answer()


@dp.callback_query(F.data == "buy20")
async def cb_buy20(call: CallbackQuery):
    await create_invoice(call.from_user.id, "Пакет 20 сообщений", "20 доп. сообщений", "pack20", PRICE_PACK20)
    await call.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)


@dp.message(F.successful_payment)
async def payment_done(message: Message):
    uid = message.from_user.id
    u = users[uid]
    payload = message.successful_payment.invoice_payload

    if payload == "individual":
        u["consult_active"] = True
        await message.answer("Индивидуальная консультация активирована.")
    elif payload == "pack5":
        u["paid_left"] += 5
        await message.answer("Добавлено 5 сообщений!")
    elif payload == "pack10":
        u["paid_left"] += 10
        await message.answer("Добавлено 10 сообщений!")
    elif payload == "pack20":
        u["paid_left"] += 20
        await message.answer("Добавлено 20 сообщений!")


@dp.message(F.text)
async def on_message(message: Message):
    uid = message.from_user.id
    reset_limits(uid)
    u = users[uid]

    if u["consult_active"]:
        ans = await ask_full(message.text)
        await message.answer(ans)
        return

    if u["free_left"] + u["paid_left"] <= 0:
        await message.answer(
            "Лимит сообщений закончился.",
            reply_markup=kb_menu()
        )
        return

    if u["free_left"] > 0:
        u["free_left"] -= 1
    else:
        u["paid_left"] -= 1

    ans = await ask_short(message.text)

    await message.answer(
        f"{ans}\n\n"
        f"Бесплатные: {u['free_left']} | Платные: {u['paid_left']}",
        reply_markup=kb_menu()
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())










