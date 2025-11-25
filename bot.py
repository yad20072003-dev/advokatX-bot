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
    FSInputFile,
    PreCheckoutQuery
)
from dotenv import load_dotenv
from openai import OpenAI
from docx import Document

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

FREE_LIMIT = 8
INDIVIDUAL_PRICE = 199
DOC_PRICE = 50
PACK_5 = 75
PACK_10 = 129
PACK_20 = 239

CONSULT_HOURS = 12

users = defaultdict(lambda: {
    "free_left": FREE_LIMIT,
    "paid_left": 0,
    "last_reset": datetime.now(),
    "mode": "basic",
    "consult_active": False,
    "consult_until": None,
    "last_q": None,
    "last_a": None,
})

SYSTEM_PROMPT = (
    "Ты — «Адвокат X», юридический ИИ-помощник.\n"
    "Отвечаешь по законодательству РФ, официально и по существу.\n"
    "Сервис носит информационный характер и не заменяет очную консультацию адвоката."
)


def reset_limit(uid: int):
    u = users[uid]
    now = datetime.now()
    if now - u["last_reset"] >= timedelta(days=1):
        u["free_left"] = FREE_LIMIT
        u["last_reset"] = now


async def ask_short(text: str) -> str:
    resp = await asyncio.to_thread(
        client.chat.completions.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    )
    return resp.choices[0].message.content.strip()


async def ask_full(text: str) -> str:
    resp = await asyncio.to_thread(
        client.chat.completions.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Сделай подробный юридический разбор:\n{text}"},
        ],
    )
    return resp.choices[0].message.content.strip()


def make_doc(text: str, uid: int) -> str:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    fname = f"advocatx_{uid}_{int(datetime.now().timestamp())}.docx"
    doc.save(fname)
    return fname


def kb_main():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
            [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="start_individual")],
            [InlineKeyboardButton(text="Меню услуг", callback_data="menu")],
        ]
    )


def kb_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Пополнить 5 сообщений — {PACK_5} ₽", callback_data="buy5")],
            [InlineKeyboardButton(text=f"Пополнить 10 сообщений — {PACK_10} ₽", callback_data="buy10")],
            [InlineKeyboardButton(text=f"Пополнить 20 сообщений — {PACK_20} ₽", callback_data="buy20")],
            [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽", callback_data="start_individual")],
        ]
    )


def kb_after_answer():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Подготовить документ — {DOC_PRICE} ₽", callback_data="paid_doc")],
            [InlineKeyboardButton(text="Меню услуг", callback_data="menu")],
        ]
    )


async def send_invoice(chat_id: int, title: str, description: str, payload: str, amount_rub: int):
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
    reset_limit(uid)
    u = users[uid]
    await message.answer(
        "Здравствуйте! Я — «Адвокат X», ваш юридический помощник.\n\n"
        "Отвечаю по законодательству РФ, официально и по существу. "
        "Сервис носит информационный характер и не заменяет очную консультацию адвоката.\n\n"
        f"Ваш бесплатный дневной лимит: {u['free_left']} сообщений.\n"
        f"Дополнительно оплаченных сообщений: {u['paid_left']}.\n\n"
        "Опишите вашу ситуацию или выберите режим ниже.",
        reply_markup=kb_main(),
    )


@dp.callback_query(F.data == "mode_basic")
async def cb_mode_basic(call: CallbackQuery):
    uid = call.from_user.id
    users[uid]["mode"] = "basic"
    await call.message.answer("Включён режим обычной консультации. Можете отправить ваш вопрос.")
    await call.answer()


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())
    await call.answer()


@dp.callback_query(F.data == "start_individual")
async def cb_start_individual(call: CallbackQuery):
    uid = call.from_user.id
    u = users[uid]
    if u["consult_active"] and u["consult_until"] and u["consult_until"] > datetime.now():
        await call.message.answer("У вас уже активна индивидуальная консультация. Можете продолжать переписку.")
        await call.answer()
        return
    await send_invoice(
        chat_id=uid,
        title="Индивидуальная консультация",
        description="Персональное юридическое сопровождение до решения одного вопроса.",
        payload="individual",
        amount_rub=INDIVIDUAL_PRICE,
    )
    await call.answer()


@dp.callback_query(F.data == "buy5")
async def cb_buy5(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Пакет 5 сообщений",
        description="Дополнительные 5 сообщений для консультаций.",
        payload="pack5",
        amount_rub=PACK_5,
    )
    await call.answer()


@dp.callback_query(F.data == "buy10")
async def cb_buy10(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Пакет 10 сообщений",
        description="Дополнительные 10 сообщений для консультаций.",
        payload="pack10",
        amount_rub=PACK_10,
    )
    await call.answer()


@dp.callback_query(F.data == "buy20")
async def cb_buy20(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Пакет 20 сообщений",
        description="Дополнительные 20 сообщений для консультаций.",
        payload="pack20",
        amount_rub=PACK_20,
    )
    await call.answer()


@dp.callback_query(F.data == "paid_doc")
async def cb_paid_doc(call: CallbackQuery):
    uid = call.from_user.id
    await send_invoice(
        chat_id=uid,
        title="Подготовка документа",
        description="Подготовка проекта документа по вашей ситуации.",
        payload="doc",
        amount_rub=DOC_PRICE,
    )
    await call.answer()


@dp.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    uid = message.from_user.id
    u = users[uid]
    sp = message.successful_payment
    payload = sp.invoice_payload

    if payload == "pack5":
        u["paid_left"] += 5
        await message.answer("Оплата успешна. На ваш баланс зачислено 5 дополнительных сообщений.")
    elif payload == "pack10":
        u["paid_left"] += 10
        await message.answer("Оплата успешна. На ваш баланс зачислено 10 дополнительных сообщений.")
    elif payload == "pack20":
        u["paid_left"] += 20
        await message.answer("Оплата успешна. На ваш баланс зачислено 20 дополнительных сообщений.")
    elif payload == "individual":
        u["consult_active"] = True
        u["consult_until"] = datetime.now() + timedelta(hours=CONSULT_HOURS)
        await message.answer(
            "Оплата индивидуальной консультации прошла успешно.\n"
            "Опишите подробно вашу ситуацию. Я буду сопровождать вас в рамках этого вопроса."
        )
    elif payload == "doc":
        if not u["last_a"]:
            await message.answer("Не удалось найти последний ответ для документа. Сначала задайте вопрос.")
            return
        fname = make_doc(u["last_a"], uid)
        doc = FSInputFile(fname)
        await message.answer_document(
            doc,
            caption="Ваш проект документа сформирован на основе последнего ответа.",
        )
        try:
            os.remove(fname)
        except OSError:
            pass


@dp.message(F.text)
async def on_message(message: Message):
    uid = message.from_user.id
    text = message.text.strip()
    u = users[uid]

    reset_limit(uid)

    if u["consult_active"]:
        if u["consult_until"] and datetime.now() > u["consult_until"]:
            u["consult_active"] = False
            u["consult_until"] = None
        else:
            ans = await ask_full(text)
            u["last_q"] = text
            u["last_a"] = ans
            await message.answer(ans, reply_markup=kb_after_answer())
            return

    total_left = u["free_left"] + u["paid_left"]
    if total_left <= 0:
        await message.answer(
            "Ваш бесплатный и оплаченный лимит сообщений исчерпан.\n"
            "Лимит бесплатных сообщений обновится через сутки.\n"
            "Вы можете пополнить баланс в меню услуг.",
            reply_markup=kb_menu(),
        )
        return

    if u["free_left"] > 0:
        u["free_left"] -= 1
    else:
        u["paid_left"] -= 1

    ans = await ask_short(text)
    u["last_q"] = text
    u["last_a"] = ans

    await message.answer(
        f"{ans}\n\n"
        f"Остаток бесплатных сообщений на сегодня: {u['free_left']}.\n"
        f"Остаток оплаченных сообщений: {u['paid_left']}.",
        reply_markup=kb_after_answer(),
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())









