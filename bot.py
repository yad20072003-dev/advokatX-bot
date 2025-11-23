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
    "consult_active": False,
    "last_q": None,
    "last_a": None
})


SYSTEM_PROMPT = (
    "Ты — «Адвокат X», юридический помощник. "
    "Отвечаешь официально, по закону РФ, чётко и без лишней воды."
)


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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="start_individual")],
        [InlineKeyboardButton(text="Меню", callback_data="menu")]
    ])


def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"5 сообщений — {PACK_5} ₽", callback_data="buy5")],
        [InlineKeyboardButton(text=f"10 сообщений — {PACK_10} ₽", callback_data="buy10")],
        [InlineKeyboardButton(text=f"20 сообщений — {PACK_20} ₽", callback_data="buy20")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="start_individual")]
    ])


def doc_btn():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Подготовить документ — {DOC_PRICE} ₽", callback_data="doc_pay")]
    ])


def create_payment(amount, description, uid, service_tag):
    payment = Payment.create({
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": PAY_RETURN_URL},
        "capture": True,
        "description": description,
        "metadata": {
            "user_id": uid,
            "service": service_tag
        }
    })
    return payment.confirmation.confirmation_url


@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    await message.answer(
        f"Здравствуйте! Я — Адвокат X.\n"
        f"Помогу разобраться в вашей юридической ситуации.\n\n"
        f"Ваш бесплатный лимит: {u['limit']} сообщений.",
        reply_markup=kb_main()
    )


@dp.callback_query(F.data == "mode_basic")
async def set_basic(call: CallbackQuery):
    await call.message.answer("Режим обычных консультаций включён.")


@dp.callback_query(F.data == "menu")
async def menu(call: CallbackQuery):
    await call.message.answer("Доступные услуги:", reply_markup=kb_menu())


@dp.callback_query(F.data == "start_individual")
async def ind_start(call: CallbackQuery):
    uid = call.from_user.id
    url = create_payment(INDIVIDUAL_PRICE, "Individual Consultation", uid, "individual")

    await call.message.answer(
        "После оплаты я буду вести вас до полного решения проблемы.\n"
        "Нажмите кнопку ниже:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
        )
    )


@dp.callback_query(F.data == "doc_pay")
async def pay_doc(call: CallbackQuery):
    uid = call.from_user.id
    url = create_payment(DOC_PRICE, "Document preparation", uid, "doc")

    await call.message.answer(
        "Оплатите услугу подготовки документа:",
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
async def on_text(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    if u["limit"] <= 0:
        await message.answer("Ваш лимит исчерпан. Пополните его через меню.", reply_markup=kb_menu())
        return

    u["limit"] -= 1

    answer = await ask_short(message.text)
    await message.answer(answer, reply_markup=doc_btn())


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())





