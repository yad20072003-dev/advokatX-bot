import os
from fastapi import FastAPI, Request
from yookassa import Configuration
from aiogram import Bot

app = FastAPI()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

Configuration.account_id = SHOP_ID
Configuration.secret_key = SECRET_KEY

bot = Bot(token=BOT_TOKEN)
user_payments = {}


@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    body = await request.json()

    event = body.get("event")
    obj = body.get("object", {})

    if event == "payment.succeeded":
        metadata = obj.get("metadata", {})
        uid = metadata.get("user_id")
        payment_type = metadata.get("type")

        if payment_type == "doc":
            await bot.send_message(uid, "Оплата получена. Подготавливаю документ…")
        elif payment_type == "indiv":
            await bot.send_message(uid, "Оплата получена. Индивидуальная консультация активирована.")
        elif payment_type == "pack":
            await bot.send_message(uid, "Пакет сообщений активирован.")
        elif payment_type == "sub":
            await bot.send_message(uid, "Подписка активирована.")
        else:
            await bot.send_message(uid, "Платёж успешно получен.")

    return {"status": "ok"}
