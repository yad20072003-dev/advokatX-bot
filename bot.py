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
    FSInputFile,
)

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import PyPDF2
import re

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

PRICE_DOC_COMPOSE = 50
PRICE_DOC_CHECK = 99
PRICE_PLAN = 149
PRICE_FULL_SUPPORT = 299

users = defaultdict(
    lambda: {
        "free_left": FREE_LIMIT,
        "paid_left": 0,
        "last_reset": datetime.now(),
        "consult_active": False,
        "service": None,
        "doc_format": None,
        "case_mode": None,
        "case_summary": None,
    }
)

SYSTEM_PROMPT_BASE = """
Ты — «Адвокат X», профессиональный юрист по праву РФ и опытный процессуалист.
Ты:
1) Всегда уточняешь факты, если их не хватает для точного ответа.
2) Строишь ответы по структуре:
   - Краткий вывод по ситуации;
   - Правовое основание;
   - Варианты действий;
   - Риски;
   - Пошаговый план.
3) Не предлагаешь незаконных действий.
4) Даёшь максимально выгодную стратегию в рамках закона.
5) Если фактов мало — уточняешь.
Отвечай профессионально и понятно обычному человеку.
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
    r = await asyncio.to_thread(_call)
    return r["choices"][0]["message"]["content"].strip()


async def ask_short_consult(text: str) -> str:
    return await ask_model(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": (
                    "Режим: краткая консультация.\n"
                    "Сделай короткий, но насыщенный ответ (5–8 предложений), без лишней воды:\n\n"
                    f"{text}"
                ),
            },
        ]
    )


async def ask_individual_consult(text: str) -> str:
    return await ask_model(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": (
                    "Режим: углубленный разбор одного вопроса.\n"
                    "Дай развёрнутый профессиональный разбор ситуации, как на очной консультации у юриста:\n"
                    "– чёткий вывод;\n"
                    "– ссылки на нормы права РФ (если уверен);\n"
                    "– варианты стратегии и тактики;\n"
                    "– практические советы, как усилить позицию клиента.\n\n"
                    f"{text}"
                ),
            },
        ]
    )


async def ask_doc_compose(text: str) -> str:
    return await ask_model(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": (
                    "Нужно составить готовый юридический документ по описанной ситуации.\n"
                    "Сам выбери тип документа (претензия, заявление, жалоба, иск, объяснительная, договор и т.п.), "
                    "который лучше всего подходит.\n\n"
                    "Сделай документ в структуре:\n"
                    "- шапка (кому, от кого — шаблонно, без реальных данных);\n"
                    "- вводная часть (кратко суть ситуации);\n"
                    "- правовое обоснование с нормами права РФ, если это уместно;\n"
                    "- требования / просьба / условия;\n"
                    "- заключительная часть, место для даты и подписи.\n\n"
                    "Документ должен быть оформлен сухим деловым стилем и быть максимально готовым к использованию после "
                    "подстановки ФИО, адресов и реквизитов.\n\n"
                    f"СИТУАЦИЯ:\n{text}"
                ),
            },
        ]
    )


async def ask_doc_check(text: str) -> str:
    return await ask_model(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": (
                    "Режим: проверка юридического документа.\n"
                    "Текст ниже — это документ или его черновик.\n\n"
                    "Твоя задача:\n"
                    "- указать потенциальные риски и слабые формулировки;\n"
                    "- отметить, что может быть использовано против клиента;\n"
                    "- предложить формулировки, которые лучше защитят интересы клиента;\n"
                    "- если есть пробелы (нет сроков, порядка расторжения, ответственности и т.п.) — указать это.\n\n"
                    "Ответ сделай структурированным, по пунктам, как это сделал бы практикующий юрист.\n\n"
                    f"ТЕКСТ ДОКУМЕНТА:\n{text}"
                ),
            },
        ]
    )


async def ask_plan_actions(text: str) -> str:
    return await ask_model(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": (
                    "Режим: составление плана действий.\n"
                    "Нужно сделать чёткий, по шагам, план действий для клиента по его ситуации.\n\n"
                    "Структура ответа:\n"
                    "1) Краткий вывод по ситуации.\n"
                    "2) Общая стратегия (что хотим получить в итоге).\n"
                    "3) Подробный пошаговый план с примерными сроками (дни/недели) и указанием, куда обращаться.\n"
                    "4) Какие документы собирать.\n"
                    "5) Возможные риски и что делать, если всё идёт не по плану.\n\n"
                    f"СИТУАЦИЯ:\n{text}"
                ),
            },
        ]
    )


async def ask_full_support(text: str, case_summary: str | None = None) -> str:
    case_part = f"Краткое описание дела, которое ты ведёшь: {case_summary}\n\n" if case_summary else ""
    return await ask_model(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": (
                    "Режим: полное сопровождение дела.\n"
                    "Ты ведёшь одно конкретное дело клиента. Тебе нужно помогать выстраивать стратегию, "
                    "предусматривать ходы оппонента и варианты развития, объяснять, что делать дальше.\n\n"
                    f"{case_part}"
                    "Сделай детальный разбор и практическую тактику по текущему сообщению клиента:\n\n"
                    f"{text}"
                ),
            },
        ]
    )


def kb_main():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Задать вопрос", callback_data="mode_basic")],
            [InlineKeyboardButton(text="Каталог услуг", callback_data="catalog")],
            [InlineKeyboardButton(text="Пакеты сообщений", callback_data="menu")],
            [InlineKeyboardButton(text="ℹ️ Инфо", callback_data="info")],
            [InlineKeyboardButton(text="⚖️ Условия использования", callback_data="terms")],
        ]
    )


def kb_catalog():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Углубленный разбор — {PRICE_INDIV} ₽", callback_data="srv_indiv"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Составить документ — {PRICE_DOC_COMPOSE} ₽",
                    callback_data="srv_doc_compose",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Проверить документ — {PRICE_DOC_CHECK} ₽",
                    callback_data="srv_doc_check",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"План действий — {PRICE_PLAN} ₽", callback_data="srv_plan"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Полное сопровождение дела — {PRICE_FULL_SUPPORT} ₽",
                    callback_data="srv_full_support",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
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
                    text="⬅️ Назад в главное меню", callback_data="back_main"
                )
            ],
        ]
    )


def kb_main_button(doc: bool = False):
    if not doc:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main")]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📄 Составить документ по этой ситуации — {PRICE_DOC_COMPOSE} ₽",
                    callback_data="buy_doc_compose",
                )
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main")],
        ]
    )


def kb_full_support():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Составить документ по этой ситуации — {PRICE_DOC_COMPOSE} ₽",
                    callback_data="buy_doc_compose",
                )
            ],
            [InlineKeyboardButton(text="✅ Дело решено", callback_data="case_done")],
            [InlineKeyboardButton(text="Пакеты сообщений", callback_data="menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main")],
        ]
    )


async def create_invoice(chat_id, title, description, payload, amount_rub):
    amount_cents = int(round(amount_rub * 100))
    prices = [LabeledPrice(label=title, amount=amount_cents)]
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        need_email=True,
        send_email_to_provider=True,
    )


def make_docx_file(text: str, uid: int) -> str:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    filename = f"document_{uid}_{int(datetime.now().timestamp())}.docx"
    doc.save(filename)
    return filename


def make_pdf_file(text: str, uid: int) -> str:
    filename = f"document_{uid}_{int(datetime.now().timestamp())}.pdf"
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40
    for line in text.split("\n"):
        c.drawString(40, y, line)
        y -= 14
        if y < 40:
            c.showPage()
            y = height - 40
    c.save()
    return filename


def extract_text_from_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    if ext == ".docx":
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    if ext == ".pdf":
        text = []
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text.append(t)
        return "\n".join(text)
    return ""


def is_same_case(new_text: str, base_text: str | None) -> bool:
    if not base_text:
        return True

    def tokens(s: str):
        return {w for w in re.findall(r"\w+", s.lower()) if len(w) >= 4}

    a = tokens(new_text)
    b = tokens(base_text)
    if not a or not b:
        return True
    inter = len(a & b)
    ratio = inter / min(len(a), len(b))
    return ratio >= 0.25


def need_doc_button(answer: str) -> bool:
    a = answer.lower()
    return bool(
        re.search(
            r"заявлен|претензи|жалоб|иск|договор|расписк|соглашени|ходатайств|акт|протокол",
            a,
        )
    )


@dp.message(CommandStart())
async def start_cmd(message: Message):
    uid = message.from_user.id
    reset_limits(uid)
    u = users[uid]
    await message.answer(
        "Здравствуйте! Я — «Адвокат X», юридический ИИ-помощник.\n\n"
        "Важно:\n"
        "• Я не адвокат и не представляю ваши интересы в суде.\n"
        "• Все ответы носят информационный характер и не являются официальной юридической консультацией "
        "или публичной офертой.\n"
        "• Перед серьёзными действиями желательно дополнительно проконсультироваться с живым юристом.\n\n"
        f"Бесплатных сообщений на сегодня: {u['free_left']}\n"
        f"Оплаченных сообщений: {u['paid_left']}\n\n"
        "Опишите вашу ситуацию или выберите нужный раздел ниже.",
        reply_markup=kb_main(),
    )


@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery):
    await call.message.answer("Главное меню:", reply_markup=kb_main())
    await call.answer()


@dp.callback_query(F.data == "catalog")
async def catalog(call: CallbackQuery):
    await call.message.answer("Каталог услуг:", reply_markup=kb_catalog())
    await call.answer()


@dp.callback_query(F.data == "mode_basic")
async def mode_basic(call: CallbackQuery):
    await call.message.answer(
        "Режим «Задать вопрос» активирован. Опишите вашу ситуацию одним сообщением."
    )
    await call.answer()


@dp.callback_query(F.data == "menu")
async def menu(call: CallbackQuery):
    await call.message.answer(
        "Пакеты сообщений:", reply_markup=kb_menu()
    )
    await call.answer()


@dp.callback_query(F.data == "info")
async def info(call: CallbackQuery):
    text = (
        "ℹ️ Кратко об услугах бота «Адвокат X»:\n\n"
        "1. Задать вопрос\n"
        "   Краткий, но по существу ответ на ваш вопрос в пределах дневного лимита.\n\n"
        f"2. Углубленный разбор — {PRICE_INDIV} ₽\n"
        "   Подробный разбор одного вопроса с объяснениями, стратегией, рисками и рекомендациями, "
        "как это сделал бы практикующий юрист.\n\n"
        f"3. Составить документ — {PRICE_DOC_COMPOSE} ₽\n"
        "   Подготовка текста юридического документа по вашей ситуации "
        "(претензия, заявление, жалоба, иск и т.п.). Вы получите готовый текст, "
        "в который останется подставить свои данные.\n\n"
        f"4. Проверить документ — {PRICE_DOC_CHECK} ₽\n"
        "   Анализ текста документа: риски, слабые места, что можно улучшить и как усилить вашу позицию.\n"
        "   Можно прислать текст или файл (PDF, DOCX, TXT).\n\n"
        f"5. План действий — {PRICE_PLAN} ₽\n"
        "   Подробный пошаговый план: что делать, куда обращаться, какие документы готовить, "
        "на что обратить внимание.\n\n"
        f"6. Полное сопровождение дела — {PRICE_FULL_SUPPORT} ₽\n"
        "   Сопровождение одного дела: стратегия, ответы на уточняющие вопросы, "
        "подготовка к шагам и реакция на действия оппонентов.\n\n"
        "Оплату принимает Telegram через ЮKassa. Чек приходит на указанный e-mail."
    )
    await call.message.answer(text)
    await call.answer()


@dp.callback_query(F.data == "terms")
async def terms(call: CallbackQuery):
    text = (
        "⚖️ Условия использования:\n\n"
        "• Бот «Адвокат X» работает на базе ИИ и не является адвокатом, юристом или представительством.\n"
        "• Ответы носят справочный и информационный характер и не являются официальной юридической "
        "консультацией, заключением или публичной офертой.\n"
        "• Ответы формируются автоматически на основе введённых данных. Вы самостоятельно принимаете решения "
        "и несёте ответственность за их последствия.\n"
        "• Запрещено использовать бот для подготовки явно незаконных действий, ухода от ответственности, "
        "обмана и т.п.\n"
        "• Используя бота, вы соглашаетесь с тем, что сервис предоставляется «как есть» и не даёт гарантий "
        "результата в суде или перед госорганами.\n"
    )
    await call.message.answer(text)
    await call.answer()


@dp.callback_query(F.data == "srv_indiv")
async def srv_indiv(call: CallbackQuery):
    text = (
        "Услуга: «Углубленный разбор».\n\n"
        "Что вы получаете:\n"
        "• подробный разбор одного вопроса;\n"
        "• анализ правовых рисков;\n"
        "• стратегию и тактику действий;\n"
        "• практические рекомендации, как защитить свои интересы.\n\n"
        "Услуга рассчитана на один вопрос и один развёрнутый ответ."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить — {PRICE_INDIV} ₽", callback_data="buy_indiv"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад к каталогу", callback_data="catalog")],
        ]
    )
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "srv_doc_compose")
async def srv_doc_compose(call: CallbackQuery):
    text = (
        "Услуга: «Составить документ».\n\n"
        "Что вы получаете:\n"
        "• текст юридического документа по вашей ситуации (претензия, заявление, жалоба, иск, договор и др.);\n"
        "• деловой стиль, структура и правовое обоснование там, где это уместно;\n"
        "• документ, который готов к использованию после подстановки ваших данных.\n\n"
        "Файл будет выдан в формате DOCX."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить — {PRICE_DOC_COMPOSE} ₽",
                    callback_data="buy_doc_compose",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад к каталогу", callback_data="catalog")],
        ]
    )
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "srv_doc_check")
async def srv_doc_check(call: CallbackQuery):
    text = (
        "Услуга: «Проверить документ».\n\n"
        "Что вы получаете:\n"
        "• анализ текста документа;\n"
        "• указание рисков и слабых формулировок;\n"
        "• предложения по усилению вашей позиции;\n"
        "• рекомендации, какие пункты стоит добавить или изменить.\n\n"
        "Документ можно отправить текстом или файлом (PDF, DOCX, TXT)."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить — {PRICE_DOC_CHECK} ₽",
                    callback_data="buy_doc_check",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад к каталогу", callback_data="catalog")],
        ]
    )
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "srv_plan")
async def srv_plan(call: CallbackQuery):
    text = (
        "Услуга: «План действий».\n\n"
        "Что вы получаете:\n"
        "• чёткий по шагам план, что делать по вашему делу;\n"
        "• примерные сроки,\n"
        "• куда обращаться и какие документы собирать;\n"
        "• предупреждение о рисках и вариантах, если что-то пойдёт не по плану."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить — {PRICE_PLAN} ₽", callback_data="buy_plan"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад к каталогу", callback_data="catalog")],
        ]
    )
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "srv_full_support")
async def srv_full_support(call: CallbackQuery):
    text = (
        "Услуга: «Полное сопровождение дела».\n\n"
        "Что вы получаете:\n"
        "• сопровождение одного конкретного дела;\n"
        "• стратегию и тактику по делу;\n"
        "• ответы на уточняющие вопросы по мере развития ситуации;\n"
        "• помощь в оценке действий оппонентов и органов;\n"
        "• рекомендации, когда стоит завершить дело или менять стратегию.\n\n"
        "Для другого дела потребуется оформить новую услугу."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить — {PRICE_FULL_SUPPORT} ₽",
                    callback_data="buy_full_support",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад к каталогу", callback_data="catalog")],
        ]
    )
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "buy_indiv")
async def buy_indiv(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "Углубленный разбор",
        "Развёрнутый разбор одного юридического вопроса.",
        "individual",
        PRICE_INDIV,
    )
    await call.answer()


@dp.callback_query(F.data == "buy_doc_compose")
async def buy_doc_compose(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "Составление документа",
        "Подготовка текста юридического документа по вашей ситуации.",
        "doc_compose",
        PRICE_DOC_COMPOSE,
    )
    await call.answer()


@dp.callback_query(F.data == "buy_doc_check")
async def buy_doc_check(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "Проверка документа",
        "Проверка текста документа: риски, слабые места, предложения по улучшению.",
        "doc_check",
        PRICE_DOC_CHECK,
    )
    await call.answer()


@dp.callback_query(F.data == "buy_plan")
async def buy_plan(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "План действий",
        "Подробный пошаговый план действий по вашему делу.",
        "plan",
        PRICE_PLAN,
    )
    await call.answer()


@dp.callback_query(F.data == "buy_full_support")
async def buy_full_support(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "Полное сопровождение дела",
        "Расширенное сопровождение одного юридического вопроса.",
        "full_support",
        PRICE_FULL_SUPPORT,
    )
    await call.answer()


@dp.callback_query(F.data == "buy5")
async def buy5(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "Пакет 5 сообщений",
        "Дополнительно 5 сообщений к вашему лимиту.",
        "pack5",
        PRICE_PACK5,
    )
    await call.answer()


@dp.callback_query(F.data == "buy10")
async def buy10(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "Пакет 10 сообщений",
        "Дополнительно 10 сообщений к вашему лимиту.",
        "pack10",
        PRICE_PACK10,
    )
    await call.answer()


@dp.callback_query(F.data == "buy20")
async def buy20(call: CallbackQuery):
    uid = call.from_user.id
    await create_invoice(
        uid,
        "Пакет 20 сообщений",
        "Дополнительные 20 сообщений к вашему лимиту.",
        "pack20",
        PRICE_PACK20,
    )
    await call.answer()


@dp.callback_query(F.data == "case_done")
async def case_done(call: CallbackQuery):
    uid = call.from_user.id
    u = users[uid]
    u["consult_active"] = False
    u["case_mode"] = None
    u["case_summary"] = None
    await call.message.answer(
        "Отмечаю дело как завершённое.\n"
        "Если появится новое дело, вы можете заказать новую услугу «Полное сопровождение дела».",
        reply_markup=kb_main(),
    )
    await call.answer()


@dp.pre_checkout_query()
async def checkout(q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(q.id, ok=True)


@dp.message(F.successful_payment)
async def paid(message: Message):
    uid = message.from_user.id
    payload = message.successful_payment.invoice_payload
    u = users[uid]

    if payload == "individual":
        u["service"] = "deep"
        await message.answer(
            "Услуга «Углубленный разбор» активирована.\n"
            "Опишите, пожалуйста, вашу ситуацию максимально подробно: факты, даты, документы, с кем спор, чего хотите добиться.",
            reply_markup=kb_main_button(),
        )
    elif payload == "doc_compose":
        u["service"] = "doc_compose"
        u["doc_format"] = "docx"
        await message.answer(
            "Оплата прошла.\n"
            "Опишите подробно вашу ситуацию и укажите, какой документ вы хотите получить (претензия, заявление, иск и т.п.).",
            reply_markup=kb_main_button(),
        )
    elif payload == "doc_check":
        u["service"] = "doc_check"
        await message.answer(
            "Оплата прошла.\n"
            "Пришлите документ для проверки:\n"
            "• текстом (скопируйте в сообщение), или\n"
            "• файлом PDF / DOCX / TXT.\n"
            "Если отправите фото документа, я попрошу вас отправить текст вручную.",
            reply_markup=kb_main_button(),
        )
    elif payload == "plan":
        u["service"] = "plan"
        await message.answer(
            "Оплата прошла.\n"
            "Опишите подробно вашу ситуацию, а я подготовлю для вас пошаговый план действий.",
            reply_markup=kb_main_button(),
        )
    elif payload == "full_support":
        u["consult_active"] = True
        u["case_mode"] = "full"
        u["case_summary"] = None
        await message.answer(
            "Полное сопровождение дела активировано.\n"
            "Опишите подробно вашу ситуацию. Я буду помогать вам формировать стратегию и дальше по ходу дела.",
            reply_markup=kb_main_button(),
        )
    elif payload == "pack5":
        u["paid_left"] += 5
        await message.answer(
            "Оплата прошла. К вашему лимиту добавлено 5 сообщений.",
            reply_markup=kb_main_button(),
        )
    elif payload == "pack10":
        u["paid_left"] += 10
        await message.answer(
            "Оплата прошла. К вашему лимиту добавлено 10 сообщений.",
            reply_markup=kb_main_button(),
        )
    elif payload == "pack20":
        u["paid_left"] += 20
        await message.answer(
            "Оплата прошла. К вашему лимиту добавлено 20 сообщений.",
            reply_markup=kb_main_button(),
        )


@dp.message(F.document)
async def doc_message(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if u["service"] != "doc_check":
        await message.answer(
            "Сейчас проверка документа не активирована.\n"
            "Если хотите проверить документ, выберите услугу «Проверить документ» в каталоге услуг и оплатите её.",
            reply_markup=kb_main_button(),
        )
        return

    os.makedirs("files", exist_ok=True)
    file = message.document
    local_path = os.path.join("files", f"{uid}_{file.file_name}")
    await bot.download(file, destination=local_path)

    text = extract_text_from_file(local_path)
    if not text.strip():
        await message.answer(
            "Не удалось прочитать текст из файла.\n"
            "Поддерживаются форматы: PDF, DOCX, TXT.\n"
            "Попробуйте отправить документ в одном из этих форматов или скопируйте текст в сообщение.",
            reply_markup=kb_main_button(),
        )
        return

    ans = await ask_doc_check(text)
    u["service"] = None
    await message.answer(ans, reply_markup=kb_main_button())


@dp.message(F.photo)
async def photo_message(message: Message):
    uid = message.from_user.id
    u = users[uid]

    if u["service"] == "doc_check":
        await message.answer(
            "Пока я не умею надёжно читать текст с фото.\n"
            "Пожалуйста, отправьте документ в виде текста или файла PDF / DOCX / TXT.",
            reply_markup=kb_main_button(),
        )
    else:
        await message.answer(
            "Если хотите проверить документ, сначала выберите услугу «Проверить документ» в каталоге услуг и оплатите её.",
            reply_markup=kb_main_button(),
        )


@dp.message(F.text)
async def msg(message: Message):
    uid = message.from_user.id
    text = message.text
    u = users[uid]

    reset_limits(uid)

    if u["service"] == "doc_compose":
        ans = await ask_doc_compose(text)
        await message.answer(ans)
        docx_path = make_docx_file(ans, uid)
        await message.answer_document(
            FSInputFile(docx_path),
            caption="Ваш документ в формате DOCX.",
        )
        u["service"] = None
        u["doc_format"] = None
        await message.answer(
            "Если нужно, вы можете воспользоваться другими услугами.",
            reply_markup=kb_main_button(),
        )
        return

    if u["service"] == "doc_check":
        ans = await ask_doc_check(text)
        u["service"] = None
        await message.answer(ans, reply_markup=kb_main_button())
        return

    if u["service"] == "plan":
        ans = await ask_plan_actions(text)
        u["service"] = None
        await message.answer(ans, reply_markup=kb_main_button())
        return

    if u["service"] == "deep":
        ans = await ask_individual_consult(text)
        doc_btn = need_doc_button(ans)
        u["service"] = None
        await message.answer(ans, reply_markup=kb_main_button(doc_btn))
        return

    if u["consult_active"] and u.get("case_mode") == "full":
        if not u.get("case_summary"):
            u["case_summary"] = text
        else:
            if not is_same_case(text, u["case_summary"]):
                await message.answer(
                    "Сейчас у вас активна услуга «Полное сопровождение дела» по ранее описанной ситуации.\n"
                    "Это сообщение похоже на новый вопрос по другому делу.\n\n"
                    "В рамках текущего сопровождения я отвечаю только по одному делу.\n"
                    "Если это всё-таки связано с тем же делом — поясните, как именно. "
                    "Для нового дела потребуется оформить новую услугу.",
                    reply_markup=kb_main_button(),
                )
                return

        ans = await ask_full_support(text, u.get("case_summary"))
        await message.answer(ans, reply_markup=kb_full_support())
        return

    if u["free_left"] + u["paid_left"] <= 0:
        await message.answer(
            "Ваш бесплатный и оплаченный лимит сообщений исчерпан.\n"
            "Бесплатный лимит обновится через сутки.\n"
            "Вы можете пополнить сообщения в разделе «Пакеты сообщений».",
            reply_markup=kb_main_button(),
        )
        return

    if u["free_left"] > 0:
        u["free_left"] -= 1
    else:
        u["paid_left"] -= 1

    ans = await ask_short_consult(text)
    doc_btn = need_doc_button(ans)

    await message.answer(
        f"{ans}\n\n"
        f"Бесплатных сообщений на сегодня: {u['free_left']}\n"
        f"Оплаченных сообщений: {u['paid_left']}",
        reply_markup=kb_main_button(doc_btn),
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())





















