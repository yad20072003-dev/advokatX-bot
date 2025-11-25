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
Ты:
1) Всегда уточняешь факты, если их не хватает для точного ответа.
2) Строишь ответы по структуре:
   - Краткий вывод по ситуации;
   - Правовое основание (указывай статьи кодексов и законов, только если разумно уверен);
   - Варианты действий и стратегия (в том числе процессуальные ходы, как защитить интересы, чем давить на оппонента в рамках закона);
   - Риски и на что обратить внимание;
   - Практический пошаговый план.
3) Никогда не предлагаешь явно незаконных действий, даже если пользователь этого хочет.
4) Стремишься найти максимально выгодную и безопасную линию поведения для пользователя, используя все легальные процессуальные возможности.
5) Если информации недостаточно или есть несколько вариантов, честно говоришь об этом и описываешь плюсы и минусы каждого.
Ответы делай чёткими, профессиональными, без лишней воды, но понятными для обычного человека.
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
        {
            "role": "user",
            "content": f"Сделай короткий, но содержательный ответ (примерно 5–8 предложений):\n\n{text}",
        },
    ]
    return await ask_model(msgs)


async def ask_full(text: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Сделай развёрнутый профессиональный юридический разбор ситуации:\n\n{text}",
        },
    ]
    return await ask_model(msgs)


def kb_main():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
            [
                InlineKeyboardButton(
                    text=f"Индивидуальная консультация — {PRICE_INDIV} ₽",
                    callback_data="buy_indiv",
                )
            ],
            [InlineKeyboardButton(text="Меню услуг", callback_data="menu")],
        ]
    )


def kb_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"5 сообщений — {PRICE_PACK5} ₽", callback_data="buy5"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"10 сообщений — {PRICE_PACK10} ₽", callback_data="buy10"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"20 сообщений — {PRICE_PACK20} ₽", callback_data="buy20"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Индивидуальная консультация — {PRICE_INDIV} ₽",
                    callback_data="buy_indiv",
                )
            ],
        ]
    )


async def create_invoice(chat_id: int, title: str, description: str, payload: str, amount_rub: int):
    prices = [LabeledPrice(label=title, amount=amount_rub * 100)]
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False,
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    reset_limits(uid)
    u = users[uid]
    await message.answer(
        "Здравствуйте! Я — «Адвокат X», юридический ИИ-помощник.\n\n"
        "Отвечаю по законодательству РФ, помогаю выстроить стратегию и понять, как защитить свои интересы.\n"
        "Сервис носит информационный характер и не заменяет очную консультацию адвоката.\n\n"
        f"Ваш бесплатный дневной лимит: {u['free_left']} сообщений.\n"
        f"Оплаченных сообщений: {u['paid_left']}.\n\n"
        "Опишите вашу ситуацию или выберите опцию ниже.",
        reply_markup=kb_main(),
    )


@dp.callback_query(F.data == "mode_basic")
async def cb_mode_basic(call: CallbackQuery):
    await call.message.answer(
        "Режим обычной консультации. Задайте вопрос — я отвечу кратко, но по существу."
    )
    await call.answer()


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())
    await call.answer()


@dp.callback_query(F.data == "buy_indiv")
async def cb_buy_indiv(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        chat_id=uid,
        title="Индивидуальная консультация",
        description="Развёрнутое сопровождение по одному вопросу до понятного плана действий.",
        payload="individual",
        amount_rub=PRICE_INDIV,
    )
    await call.answer()


@dp.callback_query(F.data == "buy5")
async def cb_buy5(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        chat_id=uid,
        title="Пакет 5 сообщений",
        description="Дополнительные 5 сообщений к вашему лимиту.",
        payload="pack5",
        amount_rub=PRICE_PACK5,
    )
    await call.answer()


@dp.callback_query(F.data == "buy10")
async def cb_buy10(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        chat_id=uid,
        title="Пакет 10 сообщений",
        description="Дополнительные 10 сообщений к вашему лимиту.",
        payload="pack10",
        amount_rub=PRICE_PACK10,
    )
    await call.answer()


@dp.callback_query(F.data == "buy20")
async def cb_buy20(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        chat_id=uid,
        title="Пакет 20 сообщений",
        description="Дополнительные 20 сообщений к вашему лимиту.",
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
    payload = message.successful_payment.invoice_payload

    if payload == "individual":
        u["consult_active"] = True
        await message.answer(
            "Индивидуальная консультация активирована.\n"
            "Опишите, пожалуйста, вашу ситуацию максимально подробно: факты, даты, документы, с кем спор, чего хотите добиться."
        )
    elif payload == "pack5":
        u["paid_left"] += 5
        await message.answer("Оплата прошла. К вашему лимиту добавлено 5 сообщений.")
    elif payload == "pack10":
        u["paid_left"] += 10
        await message.answer("Оплата прошла. К вашему лимиту добавлено 10 сообщений.")
    elif payload == "pack20":
        u["paid_left"] += 20
        await message.answer("Оплата прошла. К вашему лимиту добавлено 20 сообщений.")


@dp.message(F.text)
async def on_message(message: Message):
    uid = message.from_user.id
    text = message.text.strip()
    u = users[uid]

    reset_limits(uid)

    if u["consult_active"]:
        ans = await ask_full(text)
        await message.answer(ans)
        return

    total_left = u["free_left"] + u["paid_left"]
    if total_left <= 0:
        await message.answer(
            "Ваш бесплатный и оплаченный лимит сообщений исчерпан.\n"
            "Бесплатный лимит обновится через сутки.\n"
            "Вы можете пополнить сообщения в меню.",
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
        f"Остаток бесплатных сообщений на сегодня: {u['free_left']}.\n"
        f"Остаток оплаченных сообщений: {u['paid_left']}.",
        reply_markup=kb_menu(),
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())









