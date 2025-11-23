import os
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile
)

from dotenv import load_dotenv
import openai
from docx import Document
from yookassa import Configuration, Payment

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_API_KEY = os.getenv("YOOKASSA_API_KEY")

openai.api_key = OPENAI_API_KEY
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("advokatx")

dp = Dispatcher()

FREE_LIMIT = 10
DOC_PRICE = 50
PACK_5 = 75
PACK_10 = 129
PACK_20 = 239
INDIVIDUAL_PRICE = 199
SUB_PRICE = 299

user_state = defaultdict(lambda: {
    "messages_left": FREE_LIMIT,
    "mode": "basic",
    "last_question": None,
    "last_answer": None,
    "individual_active": False,
    "individual_until": None,
    "individual_messages_left": 100,
})

SYSTEM_PROMPT = """
Ты — «Адвокат X», профессиональный юрист РФ.
Отвечай строго, юридически корректно, официально, без воды.
Если нужна ссылка на норму — укажи её.
Не давай незаконных или сомнительных советов.
"""

BASIC_SUFFIX = "Ответ 5–7 предложений."
INDIV_SUFFIX = "Развёрнутый юридический анализ, ссылки на нормы, риски, рекомендации."


async def openai_answer(messages):
    try:
        resp = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.2
        )
        return resp["choices"][0]["message"]["content"].strip()
    except:
        return "Произошла ошибка при обращении к ИИ."


async def ask_basic(text):
    return await openai_answer([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text + "\n" + BASIC_SUFFIX}
    ])


async def ask_individual(text):
    return await openai_answer([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text + "\n" + INDIV_SUFFIX}
    ])


async def ask_doc(question, analysis):
    prompt = f"""
Ситуация:
{question}

Анализ:
{analysis}

Составь юридический документ: вводная часть, факты, правовое обоснование, требования, заключение и место для подписи.
"""
    return await ask_individual(prompt)


def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE} ₽)", callback_data="buy_indiv")],
        [InlineKeyboardButton(text="Тарифы", callback_data="menu_buy")],
    ])


def kb_buy_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"5 сообщений — {PACK_5} ₽", callback_data="buy_pack_5")],
        [InlineKeyboardButton(text=f"10 сообщений — {PACK_10} ₽", callback_data="buy_pack_10")],
        [InlineKeyboardButton(text=f"20 сообщений — {PACK_20} ₽", callback_data="buy_pack_20")],
        [InlineKeyboardButton(text=f"Подписка ({SUB_PRICE} ₽)", callback_data="buy_sub")],
    ])


def kb_doc(price):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Подготовить документ ({price} ₽)", callback_data="make_doc")]
    ])


def kb_free_doc():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подготовить документ (бесплатно)", callback_data="free_doc")]
    ])


def new_payment(amount, desc, uid):
    payment = Payment.create({
        "amount": {"value": f"{amount}.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": "https://t.me/AdvocatX_bot"},
        "capture": True,
        "description": f"{desc} | user {uid}"
    })
    return payment.confirmation.confirmation_url


def reset_if_needed(uid):
    st = user_state[uid]
    now = datetime.now()
    if "reset_day" not in st or st["reset_day"].date() != now.date():
        st["reset_day"] = now
        st["messages_left"] = FREE_LIMIT


@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    reset_if_needed(uid)
    st = user_state[uid]

    await message.answer(
        f"Здравствуйте, я — Адвокат X.\n\n"
        f"Ваш бесплатный лимит: {st['messages_left']} сообщений.\n\n"
        "⚠️ Дисклеймер: бот даёт юридические консультации, но не заменяет адвоката.",
        reply_markup=kb_menu()
    )


@dp.callback_query(F.data == "mode_basic")
async def cb_basic(call: CallbackQuery):
    user_state[call.from_user.id]["mode"] = "basic"
    await call.message.answer("Режим обычной консультации включён.")


@dp.callback_query(F.data == "menu_buy")
async def buy_menu(call: CallbackQuery):
    await call.message.answer("Выберите тариф:", reply_markup=kb_buy_menu())


@dp.callback_query(F.data == "buy_pack_5")
async def buy5(call: CallbackQuery):
    url = new_payment(PACK_5, "5 messages", call.from_user.id)
    await call.message.answer(f"Оплатите:\n{url}")


@dp.callback_query(F.data == "buy_pack_10")
async def buy10(call: CallbackQuery):
    url = new_payment(PACK_10, "10 messages", call.from_user.id)
    await call.message.answer(f"Оплатите:\n{url}")


@dp.callback_query(F.data == "buy_pack_20")
async def buy20(call: CallbackQuery):
    url = new_payment(PACK_20, "20 messages", call.from_user.id)
    await call.message.answer(f"Оплатите:\n{url}")


@dp.callback_query(F.data == "buy_sub")
async def buy_sub(call: CallbackQuery):
    url = new_payment(SUB_PRICE, "Subscription 30 days", call.from_user.id)
    await call.message.answer(f"Оплатите:\n{url}")


@dp.callback_query(F.data == "buy_indiv")
async def buy_indiv(call: CallbackQuery):
    uid = call.from_user.id
    url = new_payment(INDIVIDUAL_PRICE, "Individual consultation", uid)

    await call.message.answer(
        "После оплаты я буду вести вашу ситуацию до её полного решения.\n\n"
        "Когда проблема будет решена — напишите: «дело закрыто».\n\n"
        f"Ссылка на оплату:\n{url}"
    )


@dp.callback_query(F.data == "make_doc")
async def paid_doc(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]
    if not st["last_answer"]:
        await call.answer("Нет данных для документа.", show_alert=True)
        return
    url = new_payment(DOC_PRICE, "Document", uid)
    await call.message.answer(f"Оплатите подготовку документа:\n{url}")


@dp.callback_query(F.data == "free_doc")
async def free_doc(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]

    txt = await ask_doc(st["last_question"], st["last_answer"])
    fname = f"advokatx_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"

    doc = Document()
    for line in txt.split("\n"):
        doc.add_paragraph(line)
    doc.save(fname)

    await call.message.answer_document(InputFile(fname), caption="Ваш документ.")
    os.remove(fname)


@dp.message(F.text)
async def msg(message: Message):
    uid = message.from_user.id
    reset_if_needed(uid)
    st = user_state[uid]
    text = message.text.lower()

    if st["individual_active"]:
        st["individual_messages_left"] -= 1
        st["last_question"] = message.text

        ans = await ask_individual(message.text)
        st["last_answer"] = ans

        await message.answer(ans, reply_markup=kb_free_doc())

        if "дело закрыто" in text:
            st["individual_active"] = False
            await message.answer("Индивидуальная консультация завершена.")
        return

    if st["messages_left"] <= 0:
        await message.answer(
            "Ваш дневной лимит исчерпан. Он обновится завтра.\n"
            "Вы можете приобрести дополнительные сообщения:",
            reply_markup=kb_buy_menu()
        )
        return

    st["messages_left"] -= 1
    st["last_question"] = message.text

    need_doc = any(x in text for x in ["увольн", "договор", "иск", "претенз", "жалоб"])

    ans = await ask_basic(message.text)
    st["last_answer"] = ans

    if need_doc:
        await message.answer(ans, reply_markup=kb_doc(DOC_PRICE))
    else:
        await message.answer(ans)


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
