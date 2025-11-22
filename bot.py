import os
import asyncio
import logging
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from dotenv import load_dotenv
import openai

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN и OPENAI_API_KEY в переменных окружения")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("advokatx")

FREE_MESSAGE_LIMIT = 20

dp = Dispatcher()
user_state = defaultdict(lambda: {"messages_left": FREE_MESSAGE_LIMIT})

SYSTEM_PROMPT = (
    "Ты — «Адвокат X», строгий, точный и квалифицированный юрист по законодательству РФ. "
    "Отвечай официально-деловым стилем, по существу, без воды. "
    "Не давай советов по обходу закона или незаконным действиям. "
)


async def ask_ai_lawyer(text: str) -> str:
    try:
        resp = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.exception("OpenAI error: %s", e)
        return "Произошла техническая ошибка при обращении к ИИ. Попробуйте позже."


@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    state = user_state[uid]
    await message.answer(
        f"Здравствуйте. Я — «Адвокат X».\n"
        f"Осталось бесплатных сообщений: {state['messages_left']}.\n\n"
        "Кратко опишите вашу ситуацию."
    )


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    uid = message.from_user.id
    user_state[uid]["messages_left"] = FREE_MESSAGE_LIMIT
    await message.answer(
        f"Ваш лимит был сброшен. Доступно {FREE_MESSAGE_LIMIT} сообщений."
    )


@dp.message(F.text)
async def handle_text(message: Message):
    uid = message.from_user.id
    state = user_state[uid]

    if state["messages_left"] <= 0:
        await message.answer(
            "Лимит бесплатных сообщений исчерпан.\n"
            "Подписка будет доступна позднее."
        )
        return

    state["messages_left"] -= 1

    answer = await ask_ai_lawyer(message.text)
    await message.answer(
        f"{answer}\n\nОсталось сообщений: {state['messages_left']}"
    )


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if name == "__main__":
    asyncio.run(main())
