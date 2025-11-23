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
    FSInputFile,
)
from dotenv import load_dotenv
import openai
from docx import Document
from yookassa import Payment, Configuration
from yookassa import ApiError

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
logger = logging.getLogger("advokatx")

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
    "mode": "free",
    "last_q": None,
    "last_a": None,
    "consult_active": False,
    "consult_until": None,
    "consult_msgs": 0,
    "pending_payment": None,  # {"id": ..., "service": ..., "amount": ...}
})

SYSTEM_PROMPT = """
Ты — «Адвокат X», профессиональный юридический помощник.
Отвечаешь по законодательству РФ, официально и по делу.
Не даёшь гарантий исхода дела и не заменяешь очную консультацию адвоката.
"""


async def ask_short(text: str) -> str:
    r = await asyncio.to_thread(
        openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
    )
    return r["choices"][0]["message"]["content"].strip()


async def ask_full(text: str) -> str:
    r = await asyncio.to_thread(
        openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Сделай подробный юридический анализ ситуации, укажи нормы закона, риски и пошаговый план:\n{text}",
            },
        ],
        temperature=0.2,
    )
    return r["choices"][0]["message"]["content"].strip()


async def ask_document_text(question: str, analysis: str) -> str:
    prompt = f"""
Ситуация:
{question}

Юридический анализ:
{analysis}

Составь проект юридического документа (заявление / претензия / договор и т.п. — выбери сам по смыслу).
Сделай структуру:
1) вводная часть,
2) фактические обстоятельства,
3) правовое обоснование,
4) требования / просительная часть,
5) блок для подписи и даты.
Пиши аккуратно и официально.
"""
    r = await asyncio.to_thread(
        openai.ChatCompletion.create,
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return r["choices"][0]["message"]["content"].strip()


def make_doc(text: str, uid: int) -> str:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    fname = f"advokatx_{uid}_{int(datetime.now().timestamp())}.docx"
    doc.save(fname)
    return fname


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
        [InlineKeyboardButton(
            text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE} ₽)",
            callback_data="start_individual"
        )],
        [InlineKeyboardButton(text="Меню", callback_data="menu")],
    ])


def kb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"5 сообщений — {PACK_5} ₽", callback_data="buy5")],
        [InlineKeyboardButton(text=f"10 сообщений — {PACK_10} ₽", callback_data="buy10")],
        [InlineKeyboardButton(text=f"20 сообщений — {PACK_20} ₽", callback_data="buy20")],
        [InlineKeyboardButton(
            text=f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽",
            callback_data="start_individual"
        )],
    ])


def kb_need_doc() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Подготовить документ — {DOC_PRICE} ₽",
            callback_data="paid_doc"
        )],
        [InlineKeyboardButton(text="Меню", callback_data="menu")],
    ])


def kb_pay(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=url)],
        [InlineKeyboardButton(text="Я оплатил(а)", callback_data="check_payment")],
    ])


def create_payment(amount: int, description: str, uid: int, service: str) -> tuple[str, str]:
    """
    amount — рубли целым числом.
    Возвращает (payment_id, confirmation_url).
    """
    value = f"{amount:.2f}"

    try:
        payment = Payment.create({
            "amount": {
                "value": value,
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": PAY_RETURN_URL,
            },
            "capture": True,
            "description": description,
            "metadata": {
                "user_id": str(uid),
                "service": service,
            },
        })
    except ApiError as e:
        logger.error(f"YooKassa ApiError: {e}")
        raise
    except Exception as e:
        logger.error(f"YooKassa unknown error: {e}")
        raise

    return payment.id, payment.confirmation.confirmation_url


async def check_and_apply_payment(uid: int) -> str:
    u = users[uid]
    pp = u.get("pending_payment")
    if not pp:
        return "Нет ожидающего платежа. Сначала выберите услугу."

    try:
        payment = Payment.find_one(pp["id"])
    except Exception as e:
        logger.error(f"Error Payment.find_one: {e}")
        return "Не удалось проверить оплату. Попробуйте через минуту."

    if payment.status != "succeeded":
        return "Платёж пока не найден или не завершён. Если вы уже оплатили, подождите немного и нажмите ещё раз."

    service = pp["service"]
    amount = pp["amount"]

    if service == "pack5":
        u["limit"] += 5
        msg = "Оплата получена. Лимит пополнен на 5 сообщений."
    elif service == "pack10":
        u["limit"] += 10
        msg = "Оплата получена. Лимит пополнен на 10 сообщений."
    elif service == "pack20":
        u["limit"] += 20
        msg = "Оплата получена. Лимит пополнен на 20 сообщений."
    elif service == "individual":
        u["consult_active"] = True
        u["consult_until"] = datetime.now() + timedelta(hours=12)
        u["consult_msgs"] = 0
        msg = "Оплата консультации получена. Опишите ситуацию максимально подробно, и я буду вести вас до решения вопроса."
    elif service == "doc":
        if not u["last_q"] or not u["last_a"]:
            msg = "Оплата прошла, но нет последней консультации для документа. Напишите вашу ситуацию ещё раз."
        else:
            text = await ask_document_text(u["last_q"], u["last_a"])
            fname = make_doc(text, uid)
            file = FSInputFile(fname)
            await bot.send_document(
                chat_id=uid,
                document=file,
                caption="Ваш документ по ситуации.",
            )
            try:
                os.remove(fname)
            except OSError:
                pass
            msg = "Документ подготовлен и отправлен файлом."

    else:
        msg = "Платёж получен."

    u["pending_payment"] = None
    return msg


@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    await message.answer(
        "Здравствуйте! Я — Адвокат X.\n"
        "Помогаю разбирать юридические ситуации на основе законодательства РФ.\n\n"
        "Ответы носят информационный характер и не заменяют очную консультацию адвоката.\n\n"
        f"Ваш бесплатный дневной лимит: {u['limit']} сообщений.\n"
        "Опишите проблему или выберите режим ниже.",
        reply_markup=kb_main(),
    )


@dp.callback_query(F.data == "mode_basic")
async def cb_mode_basic(call: CallbackQuery):
    users[call.from_user.id]["mode"] = "free"
    await call.message.answer("Режим обычных коротких консультаций включён.")


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.answer("Меню услуг:", reply_markup=kb_menu())


@dp.callback_query(F.data == "start_individual")
async def cb_start_individual(call: CallbackQuery):
    uid = call.from_user.id
    u = users[uid]

    if u["consult_active"] and u["consult_until"] and u["consult_until"] > datetime.now():
        await call.message.answer("У вас уже активна индивидуальная консультация. Просто продолжайте описывать ситуацию.")
        return

    try:
        pid, url = create_payment(
            INDIVIDUAL_PRICE,
            "Individual consultation",
            uid,
            "individual",
        )
    except Exception:
        await call.message.answer("Не удалось создать платёж. Попробуйте позже.")
        return

    u["pending_payment"] = {
        "id": pid,
        "service": "individual",
        "amount": INDIVIDUAL_PRICE,
    }

    await call.message.answer(
        "Индивидуальная консультация — это сопровождение по одному вопросу до решения.\n"
        "После оплаты напишите вашу ситуацию максимально подробно.\n\n"
        "Перейдите по ссылке для оплаты, затем вернитесь в бота и нажмите «Я оплатил(а)».",
        reply_markup=kb_pay(url),
    )


@dp.callback_query(F.data == "paid_doc")
async def cb_paid_doc(call: CallbackQuery):
    uid = call.from_user.id
    u = users[uid]

    if not u["last_q"] or not u["last_a"]:
        await call.message.answer("Сначала получите консультацию, чтобы было на основе чего составлять документ.")
        return

    try:
        pid, url = create_payment(
            DOC_PRICE,
            "Legal document",
            uid,
            "doc",
        )
    except Exception:
        await call.message.answer("Не удалось создать платёж. Попробуйте позже.")
        return

    u["pending_payment"] = {
        "id": pid,
        "service": "doc",
        "amount": DOC_PRICE,
    }

    await call.message.answer(
        "Сейчас будет подготовлен документ по вашей ситуации (претензия, заявление или другой текст — по смыслу).\n"
        "Оплатите услугу, затем нажмите «Я оплатил(а)», и я вышлю файл .docx.",
        reply_markup=kb_pay(url),
    )


@dp.callback_query(F.data == "buy5")
async def cb_buy5(call: CallbackQuery):
    uid = call.from_user.id
    try:
        pid, url = create_payment(PACK_5, "5 messages pack", uid, "pack5")
    except Exception:
        await call.message.answer("Не удалось создать платёж. Попробуйте позже.")
        return

    users[uid]["pending_payment"] = {"id": pid, "service": "pack5", "amount": PACK_5}
    await call.message.answer("Оплатите пакет сообщений, затем нажмите «Я оплатил(а)».", reply_markup=kb_pay(url))


@dp.callback_query(F.data == "buy10")
async def cb_buy10(call: CallbackQuery):
    uid = call.from_user.id
    try:
        pid, url = create_payment(PACK_10, "10 messages pack", uid, "pack10")
    except Exception:
        await call.message.answer("Не удалось создать платёж. Попробуйте позже.")
        return

    users[uid]["pending_payment"] = {"id": pid, "service": "pack10", "amount": PACK_10}
    await call.message.answer("Оплатите пакет сообщений, затем нажмите «Я оплатил(а)».", reply_markup=kb_pay(url))


@dp.callback_query(F.data == "buy20")
async def cb_buy20(call: CallbackQuery):
    uid = call.from_user.id
    try:
        pid, url = create_payment(PACK_20, "20 messages pack", uid, "pack20")
    except Exception:
        await call.message.answer("Не удалось создать платёж. Попробуйте позже.")
        return

    users[uid]["pending_payment"] = {"id": pid, "service": "pack20", "amount": PACK_20}
    await call.message.answer("Оплатите пакет сообщений, затем нажмите «Я оплатил(а)».", reply_markup=kb_pay(url))


@dp.callback_query(F.data == "check_payment")
async def cb_check_payment(call: CallbackQuery):
    uid = call.from_user.id
    text = await check_and_apply_payment(uid)
    await call.message.answer(text, reply_markup=kb_menu())


@dp.message(F.text)
async def on_message(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if datetime.now() - u["last_reset"] >= timedelta(days=1):
        u["limit"] = FREE_LIMIT
        u["last_reset"] = datetime.now()

    if u["consult_active"]:
        if u["consult_until"] and u["consult_until"] < datetime.now():
            u["consult_active"] = False
        else:
            ans = await ask_full(message.text)
            u["last_q"] = message.text
            u["last_a"] = ans
            u["consult_msgs"] += 1
            await message.answer(ans, reply_markup=kb_need_doc())
            return

    if u["limit"] <= 0:
        await message.answer(
            "Ваш дневной лимит бесплатных сообщений исчерпан. Лимит обновится через сутки.\n"
            "Вы можете пополнить сообщения или оформить индивидуальную консультацию:",
            reply_markup=kb_menu(),
        )
        return

    u["limit"] -= 1
    ans = await ask_short(message.text)
    u["last_q"] = message.text
    u["last_a"] = ans

    markup = kb_menu()
    if any(w in message.text.lower() for w in ["договор", "жалоб", "претензи", "иск", "заявлен"]):
        markup = kb_need_doc()

    await message.answer(
        f"{ans}\n\nОсталось бесплатных сообщений сегодня: {u['limit']}.",
        reply_markup=markup,
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())




