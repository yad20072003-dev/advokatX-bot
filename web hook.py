import os
import json
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")

app = Flask(__name__)

users = {}

@app.post("/yookassa")
def yookassa_webhook():
    payload = request.json

    if not payload:
        return {"status": "no payload"}, 400

    event = payload.get("event")
    obj = payload.get("object", {})
    payment_id = obj.get("id")

    metadata = obj.get("metadata", {})
    user_id = metadata.get("user_id")
    action = metadata.get("action")

    if event == "payment.succeeded" and user_id and action:
        users[user_id] = action

    return {"status": "ok"}
    
if name == "__main__":
    app.run(host="0.0.0.0", port=5000)
