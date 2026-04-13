# bot/handlers.py
import asyncio
import random
import re

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

from bot.states import ReviewFlow
from agent.session import ReviewSession
from product_manager import (
    get_all_products,
    get_offer_ids,
    get_product_by_offer_id,
    add_product,
    remove_product,
    format_products_list,
)
from config import ALLOWED_CHATS, OWNER_ID

router = Router()
active_sessions: dict[int, ReviewSession] = {}

REVIEW_TRIGGER = re.compile(
    r"(сделай\s+отзыв|нужен\s+отзыв|запусти\s+отзыв|сделать\s+отзыв|"
    r"давай\s+отзыв|хочу\s+отзыв|запускай\s+отзыв|review)",
    re.IGNORECASE,
)
CANCEL_TRIGGER = re.compile(
    r"(отмен|клиент\s+передумал|не\s+надо|стоп|stop|хватит|забудь|не\s+нужно)",
    re.IGNORECASE,
)


def is_allowed(message):
    if not ALLOWED_CHATS or ALLOWED_CHATS == [0]:
        return True
    return message.chat.id in ALLOWED_CHATS


def is_owner(message):
    if OWNER_ID == 0:
        return True
    return message.from_user.id == OWNER_ID


def get_session_key(message):
    return message.from_user.id


async def make_notify(bot: Bot, chat_id: int, thread_id: int | None = None):
    """Отправляет сообщения агента БЕЗ parse_mode — избегаем конфликтов с Markdown."""
    async def notify(uid: int, text: str):
        await bot.send_message(
            chat_id,
            text,
            reply_markup=ReplyKeyboardRemove(),
            message_thread_id=thread_id,
        )
    return notify


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот AQUASOFT для автоматизации отзывов в Kaspi.\n\n"
        "Напиши сделай отзыв — запущу процесс.\n"
        "Отмена в любой момент: отмени отзыв или /cancel\n\n"
        "Команды мастера:\n"
        "/list_products — список товаров\n"
        "/status — статус твоей сессии\n"
        "/cancel — отменить сессию\n\n"
        "Команды владельца:\n"
        "/add_product <offer_id> <название>\n"
        "/remove_product <offer_id>",
    )


# ── Управление товарами ───────────────────────────────────────────────────────

@router.message(Command("list_products"))
async def cmd_list_products(message: Message):
    if not is_allowed(message):
        return
    await message.answer(format_products_list())


@router.message(Command("add_product"))
async def cmd_add_product(message: Message):
    if not is_allowed(message):
        return
    if not is_owner(message):
        await message.answer("Только владелец может добавлять товары.")
        return
    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Формат: /add_product <offer_id> <название>")
        return
    offer_id = parts[1]
    name = parts[2] if len(parts) > 2 else offer_id
    success, msg = add_product(offer_id, name)
    await message.answer(msg)
    if success:
        await message.answer(format_products_list())


@router.message(Command("remove_product"))
async def cmd_remove_product(message: Message):
    if not is_allowed(message):
        return
    if not is_owner(message):
        await message.answer("Только владелец может удалять товары.")
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /remove_product <offer_id>")
        return
    success, msg = remove_product(parts[1].strip())
    await message.answer(msg)
    if success:
        await message.answer(format_products_list())


# ── Отмена ────────────────────────────────────────────────────────────────────

@router.message(F.text.regexp(CANCEL_TRIGGER))
async def handle_cancel_phrase(message: Message, state: FSMContext):
    if not is_allowed(message):
        return
    user_id = get_session_key(message)
    session = active_sessions.get(user_id)
    if not session:
        await message.answer("Нет активной сессии для отмены.")
        return
    stage = session.cancel()
    msgs = {
        "init":        "Агент ещё не успел изменить цену.",
        "price_set":   "Цена была снижена до 100 тг — агент вернёт её обратно.",
        "order_found": "Заказ найден — агент вернёт цену.",
        "sms_sent":    "SMS уже отправлен. Агент завершит выдачу и вернёт цену.",
        "done":        "Сессия уже завершена.",
    }
    await state.clear()
    active_sessions.pop(user_id, None)
    await message.answer(
        f"Сессия отменяется...\n{msgs.get(stage, '')}\n\nАгент возвращает всё на место...",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    user_id = get_session_key(message)
    session = active_sessions.pop(user_id, None)
    if session:
        stage = session.cancel()
        msgs = {
            "init":        "Агент ещё не успел изменить цену.",
            "price_set":   "Цена снижена до 100 тг — агент вернёт обратно.",
            "order_found": "Заказ найден — агент вернёт цену.",
            "sms_sent":    "SMS уже отправлен. Агент завершит и вернёт цену.",
            "done":        "Уже завершено.",
        }
        await message.answer(
            f"Сессия отменена.\n{msgs.get(stage, '')}",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await message.answer("Нет активной сессии.", reply_markup=ReplyKeyboardRemove())
    await state.clear()


# ── /status ───────────────────────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message):
    user_id = get_session_key(message)
    session = active_sessions.get(user_id)
    if not session:
        await message.answer("У тебя нет активной сессии.")
        return
    labels = {
        "init":        "Логин / инициализация",
        "price_set":   "Цена снижена, жду заказ",
        "order_found": "Заказ найден, жду подтверждения",
        "sms_sent":    "SMS отправлен, жду код",
        "done":        "Завершено",
    }
    old_price = session._old_price_ref
    price_info = f"\nОригинальная цена: {old_price} тг" if old_price else ""
    offer = session._offer_id_ref or "—"
    await message.answer(
        f"Статус сессии:\n"
        f"Товар: {offer}\n"
        f"Стадия: {labels.get(session._current_stage, session._current_stage)}"
        f"{price_info}"
    )


# ── Шаг 1: триггер "сделай отзыв" ────────────────────────────────────────────

@router.message(F.text.regexp(REVIEW_TRIGGER))
async def cmd_start_review(message: Message, state: FSMContext):
    if not is_allowed(message):
        return
    user_id = get_session_key(message)
    if user_id in active_sessions:
        await message.answer("У тебя уже есть активная сессия. Для отмены: /cancel")
        return
    products = get_all_products()
    if not products:
        await message.answer("Список товаров пуст! Владелец должен добавить товар.")
        return

    # Кнопки с номерами товаров
    kb_rows = []
    row = []
    for i in range(1, len(products) + 1):
        row.append(KeyboardButton(text=str(i)))
        if len(row) == 4:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([KeyboardButton(text="пропустить")])
    kb = ReplyKeyboardMarkup(keyboard=kb_rows, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(ReviewFlow.waiting_product)
    await state.update_data(chat_id=message.chat.id, thread_id=message.message_thread_id)

    lines = [f"Выбери товар по номеру:"]
    for i, p in enumerate(products, 1):
        lines.append(f"{i}. {p['name']}")
    lines.append("\nИли нажми пропустить для случайного")
    await message.answer("\n".join(lines), reply_markup=kb)


# ── Шаг 2: выбор товара → запуск агента ──────────────────────────────────────

@router.message(ReviewFlow.waiting_product)
async def handle_product(message: Message, state: FSMContext, bot: Bot):
    text      = message.text.strip()
    data      = await state.get_data()
    chat_id   = data.get("chat_id", message.chat.id)
    thread_id = data.get("thread_id")
    user_id   = get_session_key(message)
    products  = get_all_products()
    offer_ids = [p["offer_id"] for p in products]

    if text.lower() == "пропустить":
        if not offer_ids:
            await message.answer("Список товаров пуст.")
            await state.clear()
            return
        offer_id = random.choice(offer_ids)
    elif text.isdigit() and 1 <= int(text) <= len(products):
        offer_id = products[int(text) - 1]["offer_id"]
    else:
        offer_id = text

    product      = get_product_by_offer_id(offer_id)
    product_name = product["name"] if product else offer_id
    product_desc = product.get("description", "") if product else ""

    notify  = await make_notify(bot, chat_id, thread_id)
    session = ReviewSession(user_id, notify)
    active_sessions[user_id] = session

    await state.set_state(ReviewFlow.waiting_confirm)
    await message.answer(
        f"Запускаю агента...\n"
        f"Товар: {product_name}\n"
        f"{offer_id}\n\n"
        f"Для отмены: отмени отзыв или /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )
    asyncio.create_task(
        _run_session_and_cleanup(session, offer_id, product_name, product_desc, user_id, state)
    )


# ── Шаг 3: подтверждение заказа ──────────────────────────────────────────────

@router.message(ReviewFlow.waiting_confirm, F.text.lower().in_(["да", "yes", "✅", "+"]))
async def confirm_order_yes(message: Message, state: FSMContext):
    user_id = get_session_key(message)
    session = active_sessions.get(user_id)
    if not session:
        await message.answer("Сессия не найдена.")
        return
    await message.answer("Подтверждено. Ожидай SMS-код от клиента...", reply_markup=ReplyKeyboardRemove())
    await state.set_state(ReviewFlow.waiting_sms)
    await session.order_confirm_queue.put("yes")


@router.message(ReviewFlow.waiting_confirm, F.text.lower().in_(["нет", "no", "❌", "-"]))
async def confirm_order_no(message: Message, state: FSMContext):
    user_id = get_session_key(message)
    session = active_sessions.get(user_id)
    if session:
        await session.order_confirm_queue.put("no")
    await state.clear()
    active_sessions.pop(user_id, None)
    await message.answer("Отменено. Цену возвращаю.", reply_markup=ReplyKeyboardRemove())


# SMS-код написан ДО перехода в waiting_sms (заранее)
@router.message(ReviewFlow.waiting_confirm, F.text.regexp(r"^\d{4}$"))
async def handle_sms_code_early(message: Message):
    user_id = get_session_key(message)
    session = active_sessions.get(user_id)
    if not session:
        return
    await session.sms_code_queue.put(message.text.strip())
    await message.answer("Код принят! Подожди — сначала нужно подтвердить заказ.\nОтветь да или нет на вопрос выше.")


# ── Шаг 4: SMS-код ───────────────────────────────────────────────────────────

@router.message(ReviewFlow.waiting_sms, F.text.regexp(r"^\d{4}$"))
async def handle_sms_code(message: Message):
    user_id = get_session_key(message)
    session = active_sessions.get(user_id)
    if not session:
        await message.answer("Сессия не найдена.")
        return
    await message.answer("Ввожу код в Kaspi...", reply_markup=ReplyKeyboardRemove())
    await session.sms_code_queue.put(message.text.strip())


@router.message(ReviewFlow.waiting_sms)
async def handle_sms_wrong(message: Message):
    await message.answer("Код должен быть ровно 4 цифры. Попробуй ещё раз:")


# ── Helper ────────────────────────────────────────────────────────────────────

async def _run_session_and_cleanup(
    session: ReviewSession,
    offer_id: str,
    product_name: str,
    product_desc: str,
    user_id: int,
    state: FSMContext,
):
    try:
        await session.start(offer_id, product_name, product_desc)
    finally:
        active_sessions.pop(user_id, None)
        await state.clear()
