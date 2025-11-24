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
    LabeledPrice,
    PreCheckoutQuery,
    FSInputFile,
)
from aiogram.enums import ContentType
from dotenv import load_dotenv
import openai
from docx import Document

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)

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
    "consult_until": None,
    "last_q": None,
    "last_a": None,
})

SYSTEM_PROMPT = (
    "Ты — «Адвокат X», профессиональный юридический помощник по законодательству РФ. "
    "Отвечаешь строго по закону, официально, чётко и без лишней воды. "
    "Не даёшь советов, направленных на обход закона или злоупотребление правом."
)


async def ask_short(text: str) -> str:
    resp = await asyncio.to_thread(
        openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text + "\nДай краткий, по сути, официальный ответ."},
        ],
        temperature=0.2,
    )
    return resp["choices"][0]["message"]["content"].strip()


async def ask_full(text: str) -> str:
    resp = await asyncio.to_thread(
        openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Сделай развёрнутый юридический разбор ситуации с указанием возможных рисков, "
                    "вариантов действий и ссылками на нормы права, если это уместно:\n\n" + text
                ),
            },
        ],
        temperature=0.2,
    )
    return resp["choices"][0]["message"]["content"].strip()


def make_doc(text: str, uid: int) -> str:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    fname = f"advokatx_{uid}_{int(datetime.now().timestamp())}.docx"
    doc.save(fname)
    return fname


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
            [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="buy_consult")],
            [InlineKeyboardButton(text="Меню услуг", callback_data="menu")],
        ]
    )


def kb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"5 сообщений — {PACK_5} ₽", callback_data="buy5")],
            [InlineKeyboardButton(text=f"10 сообщений — {PACK_10} ₽", callback_data="buy10")],
            [InlineKeyboardButton(text=f"20 сообщений — {PACK_20} ₽", callback_data="buy20")],
            [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="buy_consult")],
        ]
    )


def kb_after_answer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Подготовить документ — {DOC_PRICE} ₽", callback_data="buy_doc")],
            [InlineKeyboardButton(text="Меню услуг", callback_data="menu")],
        ]
    )


def kb_limit_reached() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"5 сообщений — {PACK_5} ₽", callback_data="buy5")],
            [InlineKeyboardButton(text=f"10 сообщений — {PACK_10} ₽", callback_data="buy10")],
            [InlineKeyboardButton(text=f"20 сообщений — {PACK_20} ₽", callback_data="buy20")],
            [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="buy_consult")],
        ]
    )


async def send_invoice(chat_id: int, title: str, description: str, amount_rub: int, payload: str):
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
        need_email=False,
        need_phone_number=False,
        is_flexible=False,
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    text = (
        "Здравствуйте! Я — «Адвокат X», ваш юридический помощник.\n\n"
        "Отвечаю по законодательству РФ, официально и по существу. "
        "Сервис носит информационный характер и не заменяет очную консультацию адвоката.\n\n"
        f"Ваш бесплатный дневной лимит: {u['limit']} сообщений.\n"
        "Опишите вашу ситуацию или выберите режим ниже."
    )
    await message.answer(text, reply_markup=kb_main())


@dp.callback_query(F.data == "mode_basic")
async def cb_mode_basic(call: CallbackQuery):
    uid = call.from_user.id
    u = users[uid]
    u["consult_active"] = False
    u["consult_until"] = None
    await call.message.answer("Включён режим обычной консультации. Можете отправить ваш вопрос.")


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())


@dp.callback_query(F.data == "buy_consult")
async def cb_buy_consult(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Индивидуальная консультация",
        description="Подробный разбор вашей ситуации до достижения результата.",
        amount_rub=INDIVIDUAL_PRICE,
        payload="consult",
    )


@dp.callback_query(F.data == "buy5")
async def cb_buy5(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Пакет 5 сообщений",
        description="Пополнение лимита на 5 сообщений.",
        amount_rub=PACK_5,
        payload="pack5",
    )


@dp.callback_query(F.data == "buy10")
async def cb_buy10(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Пакет 10 сообщений",
        description="Пополнение лимита на 10 сообщений.",
        amount_rub=PACK_10,
        payload="pack10",
    )


@dp.callback_query(F.data == "buy20")
async def cb_buy20(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Пакет 20 сообщений",
        description="Пополнение лимита на 20 сообщений.",
        amount_rub=PACK_20,
        payload="pack20",
    )


@dp.callback_query(F.data == "buy_doc")
async def cb_buy_doc(call: CallbackQuery):
    uid = call.from_user.id
    u = users[uid]

    if not u["last_q"] or not u["last_a"]:
        await call.answer("Сначала опишите ситуацию и получите консультацию.", show_alert=True)
        return

    await send_invoice(
        chat_id=uid,
        title="Подготовка документа",
        description="Подготовка юридического документа по вашей ситуации.",
        amount_rub=DOC_PRICE,
        payload="doc",
    )


@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: Message):
    uid = message.from_user.id
    u = users[uid]
    sp = message.successful_payment
    payload = sp.invoice_payload

    if payload == "pack5":
        u["limit"] += 5
        await message.answer("Оплата получена. Лимит пополнен на 5 сообщений.")
    elif payload == "pack10":
        u["limit"] += 10
        await message.answer("Оплата получена. Лимит пополнен на 10 сообщений.")
    elif payload == "pack20":
        u["limit"] += 20
        await message.answer("Оплата получена. Лимит пополнен на 20 сообщений.")
    elif payload == "consult":
        u["consult_active"] = True
        u["consult_until"] = datetime.now() + timedelta(hours=12)
        await message.answer(
            "Оплата индивидуальной консультации прошла успешно.\n"
            "Теперь вы можете подробно описать вашу ситуацию, и я буду вести вас до её решения."
        )
    elif payload == "doc":
        if not u["last_q"] or not u["last_a"]:
            await message.answer("Оплата получена, но данные для документа не найдены. Повторите запрос.")
            return
        full_text = (
            "Ситуация пользователя:\n"
            + u["last_q"]
            + "\n\nЮридический анализ:\n"
            + u["last_a"]
            + "\n\nСоставь по этой ситуации структурированный документ: вводная часть, факты, правовое обоснование, требования."
        )
        doc_text = await ask_full(full_text)
        fname = make_doc(doc_text, uid)
        file = FSInputFile(fname)
        await message.answer_document(file, caption="Подготовленный документ по вашей ситуации.")
        try:
            os.remove(fname)
        except OSError:
            pass


@dp.message(F.text)
async def on_message(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if u["consult_active"] and u["consult_until"] and datetime.now() <= u["consult_until"]:
        ans = await ask_full(message.text)
        u["last_q"] = message.text
        u["last_a"] = ans
        await message.answer(ans, reply_markup=kb_after_answer())
        return

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    if u["limit"] <= 0:
        await message.answer(
            "Ваш бесплатный лимит сообщений на сегодня исчерпан. Вы можете пополнить его или оформить индивидуальную консультацию.",
            reply_markup=kb_limit_reached(),
        )
        return

    u["limit"] -= 1
    ans = await ask_short(message.text)
    u["last_q"] = message.text
    u["last_a"] = ans
    await message.answer(
        f"{ans}\n\nОставшийся лимит на сегодня: {u['limit']} сообщений.",
        reply_markup=kb_after_answer(),
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())








