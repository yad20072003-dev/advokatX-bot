import os
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
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

DAILY_FREE_LIMIT = 10
DOC_BASIC_PRICE_RUB = 50
INDIVIDUAL_PRICE_RUB = 299

PACK_5_PRICE = 75
PACK_10_PRICE = 129
PACK_20_PRICE = 239

MSK = timezone(timedelta(hours=3))


def today_msk_str() -> str:
    return datetime.now(MSK).date().isoformat()


user_state = defaultdict(
    lambda: {
        "free_messages_left": DAILY_FREE_LIMIT,
        "paid_messages_left": 0,
        "subscription_messages_left": 0,
        "last_reset_date": today_msk_str(),
        "mode": "basic",
        "last_question": None,
        "last_answer": None,
        "doc_needed": False,
    }
)

SYSTEM_PROMPT_BASE = """
Ты — «Адвокат X», профессиональный юрист РФ.
Отвечаешь строго по закону, официально и без воды.
Не предлагаешь ничего незаконного.
Отвечаешь на основе законодательства РФ, судебной практики и здравого смысла.
"""

USER_PROMPT_BASIC_SUFFIX = """Краткий ответ: 5–8 предложений.

В конце ответа на отдельной строке напиши строго:
DOC_NEEDED: да
или
DOC_NEEDED: нет
"""

USER_PROMPT_INDIVIDUAL_SUFFIX = """Развёрнутый юридический анализ с нормами, рисками и планом действий.

В конце ответа на отдельной строке напиши строго:
DOC_NEEDED: да
или
DOC_NEEDED: нет
"""


async def openai_chat(messages: list) -> str:
    try:
        resp = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.2,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.exception("OpenAI error: %s", e)
        return "Произошла техническая ошибка при обращении к ИИ. Попробуйте позже."


async def ask_basic(t: str) -> str:
    return await openai_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {"role": "user", "content": t + "\n\n" + USER_PROMPT_BASIC_SUFFIX},
        ]
    )


async def ask_individual(t: str) -> str:
    return await openai_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {"role": "user", "content": t + "\n\n" + USER_PROMPT_INDIVIDUAL_SUFFIX},
        ]
    )


async def ask_document_text(question: str, analysis: str) -> str:
    prompt = f"""
Ситуация:
{question}

Юридический анализ:
{analysis}

Составь готовый юридический документ на русском языке с вводной частью, описанием фактов, правовым обоснованием, требованиями и блоком для подписи и даты. Не добавляй никаких пояснений — только текст документа.
"""
    return await openai_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {"role": "user", "content": prompt},
        ]
    )


def extract_doc_needed(text: str) -> tuple[str, bool]:
    lines = text.strip().splitlines()
    need = False
    if lines:
        last = lines[-1].strip().lower()
        if last.startswith("doc_needed:"):
            val = last.split(":", 1)[1].strip()
            if val == "да":
                need = True
            lines = lines[:-1]
    return "\n".join(lines).strip(), need


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Обычная консультация", callback_data="mode_basic"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE_RUB} ₽)",
                    callback_data="mode_individual",
                )
            ],
        ]
    )


def kb_basic_after(doc: bool) -> InlineKeyboardMarkup:
    if doc:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Подготовить документ ({DOC_BASIC_PRICE_RUB} ₽)",
                        callback_data="doc_basic",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE_RUB} ₽)",
                        callback_data="mode_individual",
                    )
                ],
            ]
        )
    else:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE_RUB} ₽)",
                        callback_data="mode_individual",
                    )
                ]
            ]
        )


def kb_doc_free() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подготовить документ (бесплатно)", callback_data="doc_free"
                )
            ]
        ]
    )


def kb_limit_reached() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Купить сообщения", callback_data="buy_messages"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Подписка (30 сообщений + документы)",
                    callback_data="buy_subscription",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE_RUB} ₽)",
                    callback_data="mode_individual",
                )
            ],
        ]
    )


def make_docx(text: str, uid: int) -> str:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    fname = f"advokatx_{uid}_{datetime.now(MSK).strftime('%Y%m%d_%H%M%S')}.docx"
    doc.save(fname)
    return fname


def ensure_daily_limit(uid: int):
    st = user_state[uid]
    today = today_msk_str()
    if st["last_reset_date"] != today:
        st["free_messages_left"] = DAILY_FREE_LIMIT
        st["last_reset_date"] = today


def total_messages_left(st: dict) -> int:
    return (
        st["free_messages_left"]
        + st["paid_messages_left"]
        + st["subscription_messages_left"]
    )


def consume_message(st: dict):
    if st["free_messages_left"] > 0:
        st["free_messages_left"] -= 1
    elif st["paid_messages_left"] > 0:
        st["paid_messages_left"] -= 1
    elif st["subscription_messages_left"] > 0:
        st["subscription_messages_left"] -= 1


@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    ensure_daily_limit(uid)
    st = user_state[uid]
    await message.answer(
        "Здравствуйте. Я — «Адвокат X», ИИ-юрист.\n\n"
        "⚖️ Бот не является адвокатом. Ответы формируются ИИ на основе законодательства РФ "
        "и не являются официальной юридической помощью.\n\n"
        f"Ваш дневной лимит: {DAILY_FREE_LIMIT} бесплатных сообщений.\n"
        f"Сейчас доступно: {total_messages_left(st)}.\n\n"
        "Опишите вашу ситуацию или выберите режим ниже.",
        reply_markup=kb_main(),
    )


@dp.callback_query(F.data == "mode_basic")
async def cb_basic(call: CallbackQuery):
    uid = call.from_user.id
    ensure_daily_limit(uid)
    user_state[uid]["mode"] = "basic"
    await call.message.answer("Режим обычной консультации включён. Опишите вашу ситуацию.")


@dp.callback_query(F.data == "mode_individual")
async def cb_indiv(call: CallbackQuery):
    uid = call.from_user.id
    ensure_daily_limit(uid)
    user_state[uid]["mode"] = "individual"
    await call.message.answer(
        f"Режим индивидуальной консультации.\n"
        f"Стоимость услуги — {INDIVIDUAL_PRICE_RUB} ₽ (оплата пока в тестовом режиме).\n"
        "Опишите ситуацию максимально подробно: кто, что, когда, какие документы есть."
    )


@dp.callback_query(F.data == "doc_basic")
async def cb_doc_basic(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]

    if not st["last_question"] or not st["last_answer"]:
        await call.answer("Нет данных для подготовки документа.", show_alert=True)
        return

    await call.message.answer(
        f"Готовлю документ по вашей ситуации. Обычная стоимость — {DOC_BASIC_PRICE_RUB} ₽."
    )

    txt = await ask_document_text(st["last_question"], st["last_answer"])
    fname = make_docx(txt, uid)

    doc = FSInputFile(fname)
    await call.message.answer_document(document=doc, caption="Ваш документ (DOCX).")
    os.remove(fname)


@dp.callback_query(F.data == "doc_free")
async def cb_doc_free(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]

    if not st["last_question"] or not st["last_answer"]:
        await call.answer("Нет данных для подготовки документа.", show_alert=True)
        return

    await call.message.answer("Готовлю документ по вашей ситуации…")

    txt = await ask_document_text(st["last_question"], st["last_answer"])
    fname = make_docx(txt, uid)

    doc = FSInputFile(fname)
    await call.message.answer_document(document=doc, caption="Документ по вашей ситуации.")
    os.remove(fname)


@dp.callback_query(F.data == "buy_messages")
async def cb_buy_messages(call: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"5 сообщений — {PACK_5_PRICE} ₽", callback_data="buy_pack_5"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"10 сообщений — {PACK_10_PRICE} ₽",
                    callback_data="buy_pack_10",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"20 сообщений — {PACK_20_PRICE} ₽",
                    callback_data="buy_pack_20",
                )
            ],
        ]
    )
    await call.message.answer("Выберите пакет сообщений (оплата пока тестовая):", reply_markup=kb)


async def add_messages(uid: int, amount: int):
    ensure_daily_limit(uid)
    user_state[uid]["paid_messages_left"] += amount


@dp.callback_query(F.data == "buy_pack_5")
async def cb_pack5(call: CallbackQuery):
    await add_messages(call.from_user.id, 5)
    st = user_state[call.from_user.id]
    await call.message.answer(
        f"Добавлено 5 сообщений. Теперь доступно: {total_messages_left(st)}."
    )


@dp.callback_query(F.data == "buy_pack_10")
async def cb_pack10(call: CallbackQuery):
    await add_messages(call.from_user.id, 10)
    st = user_state[call.from_user.id]
    await call.message.answer(
        f"Добавлено 10 сообщений. Теперь доступно: {total_messages_left(st)}."
    )


@dp.callback_query(F.data == "buy_pack_20")
async def cb_pack20(call: CallbackQuery):
    await add_messages(call.from_user.id, 20)
    st = user_state[call.from_user.id]
    await call.message.answer(
        f"Добавлено 20 сообщений. Теперь доступно: {total_messages_left(st)}."
    )


@dp.callback_query(F.data == "buy_subscription")
async def cb_sub(call: CallbackQuery):
    uid = call.from_user.id
    ensure_daily_limit(uid)
    st = user_state[uid]
    st["subscription_messages_left"] += 30
    st["mode"] = "individual"
    await call.message.answer(
        "Подписка (тестовый режим) активирована.\n"
        "Добавлено 30 сообщений, подготовка документов в рамках консультаций — бесплатно."
    )


@dp.message(F.text)
async def msg(message: Message):
    uid = message.from_user.id
    ensure_daily_limit(uid)
    st = user_state[uid]

    if total_messages_left(st) <= 0 and st["mode"] == "basic":
        await message.answer(
            "Ваш дневной лимит сообщений исчерпан. Обновление лимитов происходит раз в сутки.\n\n"
            "Вы можете приобрести дополнительные сообщения или оформить подписку.",
            reply_markup=kb_limit_reached(),
        )
        return

    st["last_question"] = message.text
    consume_message(st)

    if st["mode"] == "individual":
        raw = await ask_individual(message.text)
        ans, doc_needed = extract_doc_needed(raw)
        st["last_answer"] = ans
        st["doc_needed"] = doc_needed

        if doc_needed:
            await message.answer(ans, reply_markup=kb_doc_free())
        else:
            await message.answer(ans)
    else:
        raw = await ask_basic(message.text)
        ans, doc_needed = extract_doc_needed(raw)
        st["last_answer"] = ans
        st["doc_needed"] = doc_needed
        total_left = total_messages_left(st)

        await message.answer(
            f"{ans}\n\nОсталось сообщений: {total_left}",
            reply_markup=kb_basic_after(doc_needed),
        )


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
