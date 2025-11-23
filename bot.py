import os
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv
import openai
from docx import Document
from yookassa import Configuration, Payment

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BOT_USERNAME = os.getenv("BOT_USERNAME")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
PAY_RETURN_URL = os.getenv("PAY_RETURN_URL", f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "https://t.me")

openai.api_key = OPENAI_API_KEY
Configuration.configure(account_id=str(YOOKASSA_SHOP_ID), secret_key=YOOKASSA_SECRET_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("advokatx")

dp = Dispatcher()

FREE_DAILY_LIMIT = 10
DOC_PRICE = 50
INDIVIDUAL_PRICE = 199
SUBSCRIPTION_PRICE = 299
PACK_5_PRICE = 75
PACK_10_PRICE = 129
PACK_20_PRICE = 239

INDIVIDUAL_MAX_MESSAGES = 100
INDIVIDUAL_MAX_HOURS = 12


def today_str() -> str:
    return date.today().isoformat()


user_state = defaultdict(
    lambda: {
        "free_left": FREE_DAILY_LIMIT,
        "last_reset": today_str(),
        "mode": "basic",
        "individual_active": False,
        "individual_started": None,
        "individual_left": INDIVIDUAL_MAX_MESSAGES,
        "last_question": None,
        "last_answer": None,
        "has_subscription": False,
    }
)

SYSTEM_PROMPT_BASE = (
    "Ты — «Адвокат X», профессиональный юрист по праву РФ. "
    "Отвечаешь строго по закону, с опорой на нормы и практику, понятным языком. "
    "Разъясняешь риски и безопасные варианты действий. Не предлагаешь ничего незаконного. "
    "Если данных мало, уточняешь, что нужен очный юрист или дополнительные документы."
)

USER_PROMPT_BASIC_SUFFIX = "Сформулируй краткий практический ответ 5–8 предложений."
USER_PROMPT_INDIVIDUAL_SUFFIX = (
    "Дай развёрнутый юридический разбор: правовая квалификация, ссылки на нормы, риски, "
    "варианты действий по шагам. Пиши структурированно и по делу."
)


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
        return "Произошла техническая ошибка при обращении к ИИ. Попробуйте ещё раз позже."


async def ask_basic(text: str) -> str:
    return await openai_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": text + "\n\n" + USER_PROMPT_BASIC_SUFFIX,
            },
        ]
    )


async def ask_individual(text: str) -> str:
    return await openai_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": text + "\n\n" + USER_PROMPT_INDIVIDUAL_SUFFIX,
            },
        ]
    )


async def ask_document_text(question: str, analysis: str) -> str:
    prompt = (
        "Ситуация:\n"
        f"{question}\n\n"
        "Юридический анализ:\n"
        f"{analysis}\n\n"
        "Составь готовый юридический документ на русском языке: вводная часть, фактические обстоятельства, "
        "правовое обоснование со ссылками на нормы права, требования или просьбы, заключительная часть и блок для подписи. "
        "Структурируй текст с абзацами и при необходимости пунктами."
    )
    return await openai_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {"role": "user", "content": prompt},
        ]
    )


def need_document_suggestion(answer: str) -> bool:
    keywords = [
        "исковое заявление",
        "исковое требование",
        "претензи",
        "жалоб",
        "заявлени",
        "ходатайств",
        "договор",
        "соглашени",
    ]
    lower = answer.lower()
    return any(k in lower for k in keywords)


def make_docx(text: str, uid: int) -> str:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    fname = f"advokatx_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    doc.save(fname)
    return fname


def kb_main() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обычная консультация", callback_data="mode_basic")],
            [
                InlineKeyboardButton(
                    text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE} ₽)",
                    callback_data="indiv_info",
                )
            ],
            [InlineKeyboardButton(text="Пополнить сообщения", callback_data="buy_messages")],
            [
                InlineKeyboardButton(
                    text=f"Тариф «Подписка» ({SUBSCRIPTION_PRICE} ₽/мес)",
                    callback_data="sub_info",
                )
            ],
        ]
    )
    return kb


def kb_after_basic(show_doc_button: bool) -> InlineKeyboardMarkup:
    rows = []
    if show_doc_button:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Подготовить документ ({DOC_PRICE} ₽)",
                    callback_data="doc_paid",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE} ₽)",
                callback_data="indiv_info",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="Пополнить сообщения", callback_data="buy_messages")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_after_individual(show_doc_button: bool) -> InlineKeyboardMarkup:
    rows = []
    if show_doc_button:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Подготовить документ по этой ситуации",
                    callback_data="doc_from_indiv",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def kb_limit_reached() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пополнить сообщения", callback_data="buy_messages")],
            [
                InlineKeyboardButton(
                    text=f"Тариф «Подписка» ({SUBSCRIPTION_PRICE} ₽/мес)",
                    callback_data="sub_info",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Индивидуальная консультация ({INDIVIDUAL_PRICE} ₽)",
                    callback_data="indiv_info",
                )
            ],
        ]
    )
    return kb


def kb_buy_messages() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"5 сообщений — {PACK_5_PRICE} ₽",
                    callback_data="buy_pack_5",
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


def disclaimer_text() -> str:
    return (
        "Важно: бот даёт информацию общего характера и не заменяет очную консультацию адвоката. "
        "Для ответственных решений, подписания документов или споров с высокой стоимостью лучше привлечь живого юриста."
    )


def new_payment(amount_rub: int, description: str, user_id: int, product_code: str) -> str:
    idempotence_key = str(uuid.uuid4())
    payment = Payment.create(
        {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": PAY_RETURN_URL,
            },
            "capture": True,
            "description": description,
            "metadata": {
                "user_id": str(user_id),
                "product": product_code,
                "amount": amount_rub,
            },
        },
        idempotence_key,
    )
    return payment.confirmation.confirmation_url


def ensure_daily_limit(uid: int):
    st = user_state[uid]
    today = today_str()
    if st["last_reset"] != today and not st["has_subscription"]:
        st["last_reset"] = today
        st["free_left"] = FREE_DAILY_LIMIT


def individual_is_active(st: dict) -> bool:
    if not st["individual_active"]:
        return False
    if st["individual_left"] <= 0:
        return False
    if not st["individual_started"]:
        return False
    started = datetime.fromisoformat(st["individual_started"])
    if datetime.utcnow() - started > timedelta(hours=INDIVIDUAL_MAX_HOURS):
        return False
    return True


@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    st = user_state[uid]
    ensure_daily_limit(uid)
    text = (
        "Здравствуйте. Я — «Адвокат X», ваш юридический помощник.\n\n"
        "Я помогаю разобраться с трудовыми спорами, долгами, ареной, покупками, семьёй, спорами с работодателем и другими бытовыми правовыми вопросами.\n\n"
        f"Бесплатный дневной лимит: {st['free_left']} сообщений.\n\n"
        + disclaimer_text()
        + "\n\nВыберите формат или сразу опишите вашу ситуацию."
    )
    await message.answer(text, reply_markup=kb_main())


@dp.callback_query(F.data == "mode_basic")
async def mode_basic(call: CallbackQuery):
    uid = call.from_user.id
    ensure_daily_limit(uid)
    user_state[uid]["mode"] = "basic"
    await call.message.answer(
        "Режим обычной консультации включён. Кратко опишите вашу ситуацию и прикрепите важные детали."
    )
    await call.answer()


@dp.callback_query(F.data == "indiv_info")
async def indiv_info(call: CallbackQuery):
    text = (
        f"Индивидуальная консультация — {INDIVIDUAL_PRICE} ₽.\n\n"
        "Что это значит:\n"
        "• бот детально разбирает ваш конкретный кейс;\n"
        "• можно задавать уточняющие вопросы, пока разбираем одну проблему;\n"
        "• по результату можно бесплатно подготовить один документ по этой ситуации.\n\n"
        + disclaimer_text()
        + "\n\nЕсли готовы, нажмите «Оплатить консультацию», затем после оплаты вернитесь в бота и нажмите «Я оплатил»."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить консультацию ({INDIVIDUAL_PRICE} ₽)",
                    callback_data="pay_individual",
                )
            ],
            [InlineKeyboardButton(text="Я оплатил консультацию", callback_data="indiv_paid_manual")],
        ]
    )
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "pay_individual")
async def pay_individual(call: CallbackQuery):
    uid = call.from_user.id
    url = new_payment(INDIVIDUAL_PRICE, "Индивидуальная консультация", uid, "individual")
    await call.message.answer(
        "Ссылка для оплаты консультации через ЮKassa:\n"
        f"{url}\n\nПосле успешной оплаты вернитесь в бота и нажмите «Я оплатил консультацию».",
    )
    await call.answer()


@dp.callback_query(F.data == "indiv_paid_manual")
async def indiv_paid_manual(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]
    st["mode"] = "individual"
    st["individual_active"] = True
    st["individual_left"] = INDIVIDUAL_MAX_MESSAGES
    st["individual_started"] = datetime.utcnow().isoformat()
    await call.message.answer(
        "Индивидуальная консультация активирована. Опишите вашу проблему максимально подробно: даты, документы, суммы, участники."
    )
    await call.answer()


@dp.callback_query(F.data == "buy_messages")
async def buy_messages(call: CallbackQuery):
    await call.message.answer(
        "Платные сообщения позволяют продолжать обычные консультации, когда бесплатный дневной лимит исчерпан.",
        reply_markup=kb_buy_messages(),
    )
    await call.answer()


@dp.callback_query(F.data == "buy_pack_5")
async def buy_pack_5(call: CallbackQuery):
    uid = call.from_user.id
    url = new_payment(PACK_5_PRICE, "Пакет 5 сообщений", uid, "pack_5")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил", callback_data="pack_5_paid_manual")],
        ]
    )
    await call.message.answer(
        f"Ссылка для оплаты пакета 5 сообщений ({PACK_5_PRICE} ₽):\n{url}\n\n"
        "После оплаты вернитесь в бота и нажмите «Я оплатил».",
        reply_markup=kb,
    )
    await call.answer()


@dp.callback_query(F.data == "buy_pack_10")
async def buy_pack_10(call: CallbackQuery):
    uid = call.from_user.id
    url = new_payment(PACK_10_PRICE, "Пакет 10 сообщений", uid, "pack_10")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил", callback_data="pack_10_paid_manual")],
        ]
    )
    await call.message.answer(
        f"Ссылка для оплаты пакета 10 сообщений ({PACK_10_PRICE} ₽):\n{url}\n\n"
        "После оплаты вернитесь в бота и нажмите «Я оплатил».",
        reply_markup=kb,
    )
    await call.answer()


@dp.callback_query(F.data == "buy_pack_20")
async def buy_pack_20(call: CallbackQuery):
    uid = call.from_user.id
    url = new_payment(PACK_20_PRICE, "Пакет 20 сообщений", uid, "pack_20")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил", callback_data="pack_20_paid_manual")],
        ]
    )
    await call.message.answer(
        f"Ссылка для оплаты пакета 20 сообщений ({PACK_20_PRICE} ₽):\n{url}\n\n"
        "После оплаты вернитесь в бота и нажмите «Я оплатил».",
        reply_markup=kb,
    )
    await call.answer()


@dp.callback_query(F.data == "pack_5_paid_manual")
async def pack_5_paid_manual(call: CallbackQuery):
    uid = call.from_user.id
    user_state[uid]["free_left"] += 5
    await call.message.answer("Пакет 5 сообщений активирован. Можно продолжать переписку.")
    await call.answer()


@dp.callback_query(F.data == "pack_10_paid_manual")
async def pack_10_paid_manual(call: CallbackQuery):
    uid = call.from_user.id
    user_state[uid]["free_left"] += 10
    await call.message.answer("Пакет 10 сообщений активирован. Можно продолжать переписку.")
    await call.answer()


@dp.callback_query(F.data == "pack_20_paid_manual")
async def pack_20_paid_manual(call: CallbackQuery):
    uid = call.from_user.id
    user_state[uid]["free_left"] += 20
    await call.message.answer("Пакет 20 сообщений активирован. Можно продолжать переписку.")
    await call.answer()


@dp.callback_query(F.data == "sub_info")
async def sub_info(call: CallbackQuery):
    text = (
        f"Тариф «Подписка» — {SUBSCRIPTION_PRICE} ₽ в месяц.\n\n"
        "Что входит:\n"
        "• до 30 консультационных сообщений в месяц в обычном режиме;\n"
        "• подготовка документов по согласованным делам без доплаты;\n"
        "• приоритет при ответе.\n\n"
        + disclaimer_text()
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить подписку ({SUBSCRIPTION_PRICE} ₽)",
                    callback_data="pay_subscription",
                )
            ],
            [InlineKeyboardButton(text="Я оплатил подписку", callback_data="subscription_paid_manual")],
        ]
    )
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "pay_subscription")
async def pay_subscription(call: CallbackQuery):
    uid = call.from_user.id
    url = new_payment(SUBSCRIPTION_PRICE, "Подписка Адвокат X", uid, "subscription")
    await call.message.answer(
        f"Ссылка для оплаты подписки ({SUBSCRIPTION_PRICE} ₽):\n{url}\n\n"
        "После оплаты вернитесь в бота и нажмите «Я оплатил подписку».",
    )
    await call.answer()


@dp.callback_query(F.data == "subscription_paid_manual")
async def subscription_paid_manual(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]
    st["has_subscription"] = True
    st["free_left"] = 30
    st["last_reset"] = today_str()
    await call.message.answer(
        "Подписка активирована. Доступно до 30 сообщений в месяц и подготовка документов по согласованным делам."
    )
    await call.answer()


@dp.callback_query(F.data == "doc_paid")
async def doc_paid(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]
    if not st["last_question"] or not st["last_answer"]:
        await call.answer("Нет данных для подготовки документа. Сначала опишите ситуацию.", show_alert=True)
        return
    url = new_payment(DOC_PRICE, "Подготовка документа", uid, "document")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил документ", callback_data="doc_paid_manual")],
        ]
    )
    await call.message.answer(
        f"Ссылка для оплаты документа ({DOC_PRICE} ₽):\n{url}\n\n"
        "После оплаты вернитесь в бота и нажмите «Я оплатил документ».",
        reply_markup=kb,
    )
    await call.answer()


@dp.callback_query(F.data == "doc_paid_manual")
async def doc_paid_manual(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]
    if not st["last_question"] or not st["last_answer"]:
        await call.message.answer("Не удалось найти последнюю ситуацию для документа. Опишите её ещё раз.")
        await call.answer()
        return
    await call.message.answer("Готовлю документ по вашей ситуации…")
    txt = await ask_document_text(st["last_question"], st["last_answer"])
    fname = make_docx(txt, uid)
    try:
        with open(fname, "rb") as f:
            await call.message.answer_document(f, caption="Ваш документ (DOCX).")
    finally:
        try:
            os.remove(fname)
        except OSError:
            pass
    await call.answer()


@dp.callback_query(F.data == "doc_from_indiv")
async def doc_from_indiv(call: CallbackQuery):
    uid = call.from_user.id
    st = user_state[uid]
    if not st["individual_active"]:
        await call.answer("Документ по индивидуальной консультации сейчас недоступен.", show_alert=True)
        return
    if not st["last_question"] or not st["last_answer"]:
        await call.answer("Нет данных для документа. Сначала опишите ситуацию в консультации.", show_alert=True)
        return
    await call.message.answer("Готовлю документ по вашей индивидуальной консультации…")
    txt = await ask_document_text(st["last_question"], st["last_answer"])
    fname = make_docx(txt, uid)
    try:
        with open(fname, "rb") as f:
            await call.message.answer_document(f, caption="Документ по вашей ситуации (DOCX).")
    finally:
        try:
            os.remove(fname)
        except OSError:
            pass
    await call.answer()


@dp.message(F.text)
async def on_text(message: Message):
    uid = message.from_user.id
    st = user_state[uid]
    ensure_daily_limit(uid)

    if individual_is_active(st):
        st["mode"] = "individual"
    else:
        st["individual_active"] = False

    if st["mode"] == "basic" and not st["has_subscription"]:
        if st["free_left"] <= 0:
            await message.answer(
                "Ваш бесплатный дневной лимит сообщений исчерпан. Лимит обновится завтра.\n\n"
                "Вы можете пополнить сообщения или оформить подписку.",
                reply_markup=kb_limit_reached(),
            )
            return

    text = message.text.strip()
    st["last_question"] = text

    if st["mode"] == "individual" and individual_is_active(st):
        st["individual_left"] -= 1
        ans = await ask_individual(text)
        st["last_answer"] = ans
        show_doc = need_document_suggestion(ans)
        kb = kb_after_individual(show_doc)
        if kb:
            await message.answer(ans, reply_markup=kb)
        else:
            await message.answer(ans)
        return

    st["free_left"] -= 1
    ans = await ask_basic(text)
    st["last_answer"] = ans
    show_doc = need_document_suggestion(ans)
    kb = kb_after_basic(show_doc)
    await message.answer(
        f"{ans}\n\nОсталось бесплатных сообщений на сегодня: {st['free_left']}",
        reply_markup=kb,
    )


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
