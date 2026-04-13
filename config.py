# config.py
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
KASPI_EMAIL    = os.getenv("KASPI_EMAIL", "")
KASPI_PASS     = os.getenv("KASPI_PASS", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ID чатов/групп, которым разрешено использовать бота
# Пример: "-100123456789" для группы, "123456789" для лички мастера
ALLOWED_CHATS = [
    int(x.strip())
    for x in os.getenv("ALLOWED_CHATS", "0").split(",")
    if x.strip()
]

# ID владельца (только он может добавлять/удалять товары)
# Узнать свой ID: написать @userinfobot в Telegram
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
