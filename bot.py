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
    InputFile
)

from dotenv import load_dotenv
import openai
from docx import Document

from yookassa import Configuration, Payment

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
PAY_RETURN_URL = os.getenv("PAY_RETURN_URL")

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

dp = Dispatcher()

FREE_DAILY_LIMIT = 10
DOC_PRICE = 50
INDIV_PRICE = 199
PACK_5 = 75
PACK_10 = 129
PACK_20 = 239

user_state = defaultdict(lambda: {
    "messages_left": FREE_DAILY_LIMIT,
    "last_reset": datetime.now(),
    "mode": "basic",
    "indiv_until": None,
    "messages_indiv_left": 0,
    "last_question": None,
    "last_answer": None,
})

system_prompt = """
Ты — профессиональный юрист РФ «Адвокат X».
Отвечаешь строго по закону, официально и кратко.
Не предлагаешь ничего незаконного.
"""

async def ask_gpt(user_text, mode="basic"):
    suffix = "Краткий ответ 5–8 предложений." if mode == "basic" else "Дай глубокий юридический анализ, основанный на нормах закона."
    prompt = f"{user_text}\n\n{suffix}"

    try:
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        return response["choices"][0]["message"]["content"]
    except Exception:
        return "Произошла ошибка обработки запроса."

def make_doc(text, uid):
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    filename = f"doc_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    doc.save(filename)
    return filename

def main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="basic")],
        [InlineKeyboardButton(text=f"Индивидуальная консультация — {INDIV_PRICE}₽", callback_data="indiv_pay")],
        [InlineKeyboardButton(text="Пакеты сообщений", callback_data="buy_msgs")],
        [InlineKeyboardButton(text=f"Документ — {DOC_PRICE}₽", callback_data="doc_pay")]
    ])
    return kb

def payment(amount, desc, uid):
    payment = Payment.create({
        "amount": {
            "value": f"{amount}.00",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": PAY_RETURN_URL
        },
        "description": f"{desc} | user {uid}"
    })
    return payment.confirmation.confirmation_url, payment.id

@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    st = user_state[uid]

    await message.answer(
        f"Здравствуйте! Я — Адвокат X.\n\n"
        f"Ваш дневной лимит: {st['messages_left']} сообщений.\n"
        f"Используйте меню — или опишите проблему.",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "basic")
async def set_basic(call: CallbackQuery):
    uid = call.from_user.id
    user_state[uid]["mode"] = "basic"
    await call.message.answer("Режим обычной консультации включён.")

@dp.callback_query(F.data == "indiv_pay")
async def indiv_pay(call: CallbackQuery):
    uid = call.from_user.id
    url, pid = payment(INDIV_PRICE, "individual", uid)
    user_state[uid]["pending"] = pid
    await call.message.answer(f"Оплатите индивидуальную консультацию ({INDIV_PRICE}₽):\n{url}")

@dp.callback_query(F.data == "doc_pay")
async def doc_pay(call: CallbackQuery):
    uid = call.from_user.id
    url, pid = payment(DOC_PRICE, "document", uid)
    user_state[uid]["pending_doc"] = pid
    await call.message.answer(f"Оплатите подготовку документа ({DOC_PRICE}₽):\n{url}")

@dp.callback_query(F.data == "buy_msgs")
async def buy_msgs(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"5 сообщений — {PACK_5}₽", callback_data="p5")],
        [InlineKeyboardButton(text=f"10 сообщений — {PACK_10}₽", callback_data="p10")],
        [InlineKeyboardButton(text=f"20 сообщений — {PACK_20}₽", callback_data="p20")]
    ])
    await call.message.answer("Выберите пакет:", reply_markup=kb)

async def pack(call, price, count):
    uid = call.from_user.id
    url, pid = payment(price, f"{count} messages", uid)
    user_state[uid]["pending_pack"] = (pid, count)
    await call.message.answer(f"Оплатите пакет {count} сообщений ({price}₽):\n{url}")

@dp.callback_query(F.data == "p5")
async def buy_5(call): await pack(call, PACK_5, 5)

@dp.callback_query(F.data == "p10")
async def buy_10(call): await pack(call, PACK_10, 10)

@dp.callback_query(F.data == "p20")
async def buy_20(call): await pack(call, PACK_20, 20)

async def check_payment(pid):
    try:
        p = Payment.find_one(pid)
        return p.status == "succeeded"
    except:
        return False

@dp.message(F.text)
async def message_handler(message: Message):
    uid = message.from_user.id
    st = user_state[uid]

    if (datetime.now() - st["last_reset"]).days >= 1:
        st["messages_left"] = FREE_DAILY_LIMIT
        st["last_reset"] = datetime.now()

    if st["indiv_until"] and datetime.now() < st["indiv_until"]:
        if st["messages_indiv_left"] <= 0:
            await message.answer("Вы использовали весь лимит консультации.")
            return
        st["messages_indiv_left"] -= 1
        st["last_question"] = message.text
        ans = await ask_gpt(message.text, "indiv")
        st["last_answer"] = ans
        await message.answer(ans)
        return

    if st["messages_left"] <= 0:
        await message.answer(
            "Ваш дневной лимит исчерпан.\n"
            "Лимит обновляется каждые 24 часа.",
            reply_markup=main_menu()
        )
        return

    st["messages_left"] -= 1
    st["last_question"] = message.text
    ans = await ask_gpt(message.text, "basic")
    st["last_answer"] = ans

    need_doc = any(x in message.text.lower() for x in ["договор", "заявление", "претензия"])

    if need_doc:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Подготовить документ — {DOC_PRICE}₽", callback_data="doc_pay")]
        ])
        await message.answer(ans, reply_markup=kb)
    else:
        await message.answer(ans, reply_markup=main_menu())

async def main():
    bot = Bot(BOT_TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
