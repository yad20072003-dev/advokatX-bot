import os
import asyncio
import logging
from collections import defaultdict
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import openai
from docx import Document

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("advokatx")

dp = Dispatcher()

FREE_MESSAGE_LIMIT = 5
DOC_BASIC_PRICE_RUB = 149
INDIVIDUAL_PRICE_RUB = 299

user_state = defaultdict(lambda: {
    "messages_left": FREE_MESSAGE_LIMIT,
    "mode": "basic",
    "last_question": None,
    "last_answer": None,
})

SYSTEM_PROMPT_BASE = """
Ты — «Адвокат X», профессиональный юрист РФ.
Отвечаешь строго по закону, официально и без воды.
Не предлагаешь ничего незаконного.
"""

USER_PROMPT_BASIC_SUFFIX = "Краткий ответ: 5–8 предложений."
USER_PROMPT_INDIVIDUAL_SUFFIX = "Развёрнутый юридический анализ с нормами, рисками и планом действий."


async def openai_chat(messages: list) -> str:
    try:
        resp = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.2,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception:
        return "Произошла техническая ошибка при обращении к ИИ."


async def ask_basic(t: str) -> str:
    return await openai_chat([
        {"role": "system", "content": SYSTEM_PROMPT_BASE},
        {"role": "user", "content": t + "\n\n" + USER_PROMPT_BASIC_SUFFIX},
    ])


async def ask_individual(t: str) -> str:
    return await openai_chat([
        {"role": "system", "content": SYSTEM_PROMPT_BASE},
        {"role": "user", "content": t + "\n\n" + USER_PROMPT_INDIVIDUAL_SUFFIX},
    ])


async def ask_document_text(question: str, analysis: str) -> str:
    prompt = f"""
Ситуация:
{question}

Юридический анализ:
{analysis}

Составь готовый юридический документ с вводной частью, фактами, правовым обоснованием, требованиями и местом для подписи.
"""
    return await openai_chat([
        {"role": "system", "content": SYSTEM_PROMPT_BASE},
        {"role": "user", "content": prompt},
    ])


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
            [InlineKeyboardButton(
                text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE_RUB} ₽)",
                callback_data="mode_individual"
            )]
        ]
    )


def kb_follow() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"Подготовить документ ({DOC_BASIC_PRICE_RUB} ₽)",
                callback_data="doc_basic"
            )],
            [InlineKeyboardButton(
                text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE_RUB} ₽)",
                callback_data="mode_individual"
            )]
        ]
    )


def kb_doc_free() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="Подготовить документ (бесплатно)",
                callback_data="doc_free"
            )]
        ]
    )


def kb_limit_reached() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пополнить сообщения", callback_data="buy_messages")],
            [InlineKeyboardButton(
                text="Подписка (30 сообщений + документы)",
                callback_data="buy_subscription"
            )],
            [InlineKeyboardButton(
                text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE_RUB} ₽)",
                callback_data="mode_individual"
            )]
        ]
    )


def make_docx(text: str, uid: int) -> str:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    fname = f"advokatx_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    doc.save(fname)
    return fname


@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    st = user_state[uid]
    await message.answer(
        f"Здравствуйте. Я — «Адвокат X».\n\n"
        f"Ваш бесплатный лимит: {st['messages_left']} сообщений.\n\n"
        "Выберите формат или опишите ситуацию.",
        reply_markup=kb_main()
    )


@dp.callback_query(F.data == "mode_basic")
async def cb_basic(call: CallbackQuery):
    user_state[call.from_user.id]["mode"] = "basic"
    await call.message.answer("Режим обычной консультации включён.")


@dp.callback_query(F.data == "mode_individual")
async def cb_indiv(call: CallbackQuery):
    user_state[call.from_user.id]["mode"] = "individual"
    await call.message.answer(
        f"Индивидуальная консультация (299 ₽ — сейчас тестовый режим).\n"
        "Опишите ситуацию подробно."
    )


@dp.callback_query(F.data == "doc_basic")
async def cb_doc_basic(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]
    if not st["last_question"] or not st["last_answer"]:
        await call.answer("Нет данных для документа.", show_alert=True)
        return

    await call.message.answer(
        f"Документ обычно стоит {DOC_BASIC_PRICE_RUB} ₽. Сейчас тестовый режим — готовлю документ."
    )

    txt = await ask_document_text(st["last_question"], st["last_answer"])
    fname = make_docx(txt, uid)

    await call.message.answer_document(open(fname, "rb"), caption="Ваш документ (DOCX).")
    os.remove(fname)


@dp.callback_query(F.data == "doc_free")
async def cb_doc_free(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]

    await call.message.answer("Готовлю документ…")

    txt = await ask_document_text(st["last_question"], st["last_answer"])
    fname = make_docx(txt, uid)

    await call.message.answer_document(open(fname, "rb"), caption="Документ по вашей ситуации (DOCX).")
    os.remove(fname)


@dp.callback_query(F.data == "buy_messages")
async def cb_buy_messages(call: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="5 сообщений — 99 ₽", callback_data="buy_pack_5")],
            [InlineKeyboardButton(text="15 сообщений — 199 ₽", callback_data="buy_pack_15")],
            [InlineKeyboardButton(text="40 сообщений — 399 ₽", callback_data="buy_pack_40")]
        ]
    )
    await call.message.answer("Выберите пакет сообщений:", reply_markup=kb)


async def add_messages(uid: int, amount: int):
    user_state[uid]["messages_left"] += amount


@dp.callback_query(F.data == "buy_pack_5")
async def cb_pack5(call: CallbackQuery):
    await add_messages(call.from_user.id, 5)
    await call.message.answer("Пополнено: +5 сообщений.")


@dp.callback_query(F.data == "buy_pack_15")
async def cb_pack15(call: CallbackQuery):
    await add_messages(call.from_user.id, 15)
    await call.message.answer("Пополнено: +15 сообщений.")


@dp.callback_query(F.data == "buy_pack_40")
async def cb_pack40(call: CallbackQuery):
    await add_messages(call.from_user.id, 40)
    await call.message.answer("Пополнено: +40 сообщений.")


@dp.callback_query(F.data == "buy_subscription")
async def cb_sub(call: CallbackQuery):
    uid = call.from_user.id
    user_state[uid]["messages_left"] = 30
    user_state[uid]["mode"] = "individual"
    await call.message.answer(
        "Подписка активирована (тестовый режим).\n"
        "Доступно 30 сообщений и бесплатная подготовка документов."
    )


@dp.message(F.text)
async def msg(message: Message):
    uid = message.from_user.id
    st = user_state[uid]

    if st["messages_left"] <= 0 and st["mode"] == "basic":
        await message.answer("Ваш лимит исчерпан.", reply_markup=kb_limit_reached())
        return

    st["last_question"] = message.text
    st["messages_left"] -= 1

    if st["mode"] == "individual":
        ans = await ask_individual(message.text)
        st["last_answer"] = ans
        await message.answer(ans, reply_markup=kb_doc_free())
    else:
        ans = await ask_basic(message.text)
        st["last_answer"] = ans
        await message.answer(
            f"{ans}\n\nОсталось сообщений: {st['messages_left']}",
            reply_markup=kb_follow()
        )


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
