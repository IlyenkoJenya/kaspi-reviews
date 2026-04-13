# Kaspi Review Bot

Телеграм-бот для автоматизации получения отзывов через Kaspi. Снижает цену товара до 0₸, ждёт заказ, проводит выдачу через SMS-код и возвращает цену обратно. Работает в групповом чате — несколько мастеров могут использовать одновременно, у каждого своя сессия.

## Структура

```
├── agent/
│   ├── session.py            # основная логика сессии (Playwright + asyncio)
│   ├── kaspi_login.py
│   ├── kaspi_actions.py      # смена цены, получение ссылки
│   ├── wait_for_order.py     # polling заказов
│   └── deliver_order_flow.py # SMS и подтверждение выдачи
├── bot/
│   ├── main.py
│   ├── handlers.py
│   └── states.py
├── config.py
├── product_manager.py
├── products.json
└── requirements.txt
```

## Использование

Мастер пишет в группу `сделай отзыв` — бот спрашивает товар или предлагает случайный из списка. После выбора снижает цену до 100₸ и отправляет ссылку для клиента.

Когда заказ появляется в Kaspi, бот присылает данные клиента — мастер подтверждает `да` или `нет`. После подтверждения бот отправляет SMS клиенту, мастер вводит 4-значный код, бот проводит выдачу и возвращает цену на место.

Отменить можно в любой момент: `отмени отзыв` или `/cancel`. Если отмена после отправки SMS — нужно всё равно завести код, иначе выдача зависнет.

**Команды:**
- `/start`, `/status`, `/cancel`, `/list_products`
- `/add_product <offer_id> <название>` и `/remove_product <offer_id>` — только для владельца

## Установка

```bash

cd /opt/kaspi_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

Скопируй `.env.example` в `.env` и заполни:

```ini
BOT_TOKEN=токен_от_BotFather
KASPI_EMAIL=email_от_merchant_cabinet
KASPI_PASS=пароль
ALLOWED_CHATS=-100123456789
OWNER_ID=123456789
OPENAI_API_KEY=sk-...
```

Проверь что запускается:
```bash
python -m bot.main
```

## Запуск как сервис (systemd)

```bash
nano /etc/systemd/system/kaspi-bot.service
```

```ini
[Unit]
Description=Kaspi Review Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/kaspi_bot
ExecStart=/opt/kaspi_bot/venv/bin/python -m bot.main
Restart=always
RestartSec=5
EnvironmentFile=/opt/kaspi_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable kaspi-bot
systemctl start kaspi-bot
```

Логи: `tail -f /opt/kaspi_bot/logs/bot.log`

## Настройка группы

В @BotFather отключи Group Privacy для бота (`Bot Settings → Group Privacy → Turn off`), добавь бота в группу мастеров, узнай ID группы через @userinfobot и впиши в `ALLOWED_CHATS`.
