# bot/handlers.py
import asyncio
import random
import re
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

from bot.states import ReviewFlow
from agent.session import ReviewSession
from product_manager import (
    get_all_products,
    get_product_by_offer_id,
    add_product,
    remove_product,
    format_products_list,
)
from config import ALLOWED_CHATS, OWNER_ID

router = Router()

# Одна глобальная сессия — только один мастер за раз
_global_session: ReviewSession | None = None
_global_owner_id: int | None = None
_global_owner_name: str = ""
_global_started_at: datetime | None = None

# FSMContext владельца сессии — нужен владельцу для force-cancel
_global_owner_state: FSMContext | None = None

REVIEW_TRIGGER = re.compile(
    r"(сделай\s+отзыв|нужен\s+отзыв|запусти\s+отзыв|сделать\s+отзыв|"
    r"давай\s+отзыв|хочу\s+отзыв|запускай\s+отзыв|review)",
    re.IGNORECASE,
)
CANCEL_TRIGGER = re.compile(
    r"(отмен|клиент\s+передумал|не\s+надо|стоп|stop|хватит|забудь|не\s+нужно)",
    re.IGNORECASE,
)

STAGE_MSGS = {
    "init":        "Агент ещё не успел изменить цену.",
    "price_set":   "Цена была снижена до 100 ₸ — агент вернёт её обратно.",
    "order_found": "Заказ найден — агент вернёт цену.",
    "sms_sent":    "SMS уже отправлен. Агент завершит выдачу и вернёт цену.",
    "done":        "Сессия уже завершена.",
}

STAGE_LABELS = {
    "init":        "Логин / инициализация",
    "price_set":   "Цена снижена, жду заказ",
    "order_found": "Заказ найден, жду подтверждения",
    "sms_sent":    "SMS отправлен, жду код",
    "done":        "Завершено",
}


def is_allowed(message: Message) -> bool:
    if not ALLOWED_CHATS or ALLOWED_CHATS == [0]:
        return True
    return message.chat.id in ALLOWED_CHATS


def is_owner(message: Message) -> bool:
    if OWNER_ID == 0:
        return True
    return message.from_user.id == OWNER_ID


def _can_cancel(message: Message) -> bool:
    """Может отменить сессию: владелец бота или тот, кто её запустил."""
    return is_owner(message) or message.from_user.id == _global_owner_id


def _get_user_name(message: Message) -> str:
    user = message.from_user
    return user.first_name or user.username or "Мастер"


def _elapsed_str() -> str:
    if not _global_started_at:
        return ""
    minutes = int((datetime.now() - _global_started_at).total_seconds() // 60)
    return f" ({minutes} мин. назад)" if minutes > 0 else ""


async def make_notify(bot: Bot, chat_id: int, thread_id: int | None = None):
    async def notify(uid: int, text: str, reply_markup=None):
        await bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup if reply_markup is not None else ReplyKeyboardRemove(),
            message_thread_id=thread_id,
        )
    return notify


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message):
    owner_commands = (
        "\n\nКоманды владельца:\n"
        "/add_product <offer_id> <название>\n"
        "/remove_product <offer_id>"
    ) if is_owner(message) else ""

    await message.answer(
        "👋 Привет! Я автоматизирую получение отзывов в Kaspi.\n\n"
        "Чтобы начать — напиши «сделай отзыв»\n"
        "Отмена — «отмени отзыв» или /cancel\n\n"
        "📋 Команды:\n"
        "/list_products — список товаров\n"
        "/status — статус текущей сессии"
        f"{owner_commands}",
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
        await message.answer("❌ Только владелец может добавлять товары.")
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
        await message.answer("❌ Только владелец может удалять товары.")
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

    if not _global_session:
        await message.answer("Нет активной сессии.")
        return

    if not _can_cancel(message):
        await message.answer(
            f"⏳ Сессию ведёт {_global_owner_name} — только он или владелец могут её отменить."
        )
        return

    await _do_cancel(message, state)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if not _global_session:
        await message.answer("Нет активной сессии.", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return

    if not _can_cancel(message):
        await message.answer(
            f"⏳ Сессию ведёт {_global_owner_name} — только он или владелец могут её отменить.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await _do_cancel(message, state)


async def _do_cancel(message: Message, state: FSMContext):
    """Общая логика отмены сессии."""
    global _global_session, _global_owner_id, _global_owner_name, _global_started_at, _global_owner_state

    session = _global_session
    owner_state = _global_owner_state
    by_owner = is_owner(message) and message.from_user.id != _global_owner_id

    stage = session.cancel()

    # Сбрасываем состояние того, кто запустил (если отменяет владелец — у него другой state)
    if owner_state is not None and by_owner:
        await owner_state.clear()
    await state.clear()

    who = f" (отменено владельцем)" if by_owner else ""
    await message.answer(
        f"🚫 Сессия {_global_owner_name} отменена{who}.\n"
        f"{STAGE_MSGS.get(stage, '')}\n\n"
        f"Возвращаю всё на место...",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── /status ───────────────────────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message):
    if not _global_session:
        await message.answer("Нет активной сессии.")
        return

    old_price = _global_session._old_price_ref
    price_info = f"\n💰 Оригинальная цена: {old_price} ₸" if old_price else ""
    offer = _global_session._offer_id_ref or "—"

    await message.answer(
        f"📊 Текущая сессия:\n"
        f"👤 Мастер: {_global_owner_name}{_elapsed_str()}\n"
        f"📦 Товар: {offer}\n"
        f"🔄 Статус: {STAGE_LABELS.get(_global_session._current_stage, _global_session._current_stage)}"
        f"{price_info}"
    )


# ── Шаг 1: триггер "сделай отзыв" ────────────────────────────────────────────

@router.message(F.text.regexp(REVIEW_TRIGGER))
async def cmd_start_review(message: Message, state: FSMContext):
    if not is_allowed(message):
        return

    if _global_session is not None:
        await message.answer(
            f"⏳ {_global_owner_name} уже ведёт сессию{_elapsed_str()}.\n\n"
            f"Дождись завершения или попроси владельца отменить."
        )
        return

    products = get_all_products()
    if not products:
        await message.answer("Список товаров пуст. Владелец должен добавить товар.")
        return

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

    lines = ["Выбери товар по номеру:\n"]
    for i, p in enumerate(products, 1):
        lines.append(f"{i}. {p['name']}")
    lines.append("\nИли нажми «пропустить» — выберу случайный")
    await message.answer("\n".join(lines), reply_markup=kb)


# ── Шаг 2: выбор товара → запуск агента ──────────────────────────────────────

@router.message(ReviewFlow.waiting_product)
async def handle_product(message: Message, state: FSMContext, bot: Bot):
    global _global_session, _global_owner_id, _global_owner_name, _global_started_at, _global_owner_state

    # Повторная проверка — пока выбирал товар, кто-то мог стартануть
    if _global_session is not None:
        await state.clear()
        await message.answer(
            f"⏳ {_global_owner_name} успел запустить сессию пока ты выбирал товар.\n"
            f"Дождись завершения.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    text      = message.text.strip()
    data      = await state.get_data()
    chat_id   = data.get("chat_id", message.chat.id)
    thread_id = data.get("thread_id")
    user_id   = message.from_user.id
    products  = get_all_products()

    if text.lower() == "пропустить":
        offer_ids = [p["offer_id"] for p in products]
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

    _global_session     = session
    _global_owner_id    = user_id
    _global_owner_name  = _get_user_name(message)
    _global_started_at  = datetime.now()
    _global_owner_state = state

    await state.set_state(ReviewFlow.waiting_confirm)
    await message.answer(
        f"⚙️ Запускаю агента...\n"
        f"📦 Товар: {product_name}\n\n"
        f"Для отмены — «отмени отзыв» или /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )
    asyncio.create_task(
        _run_session_and_cleanup(session, offer_id, product_name, product_desc, user_id, state)
    )


# ── Шаг 3: подтверждение заказа — inline-кнопки ──────────────────────────────

@router.callback_query(F.data == "order:yes")
async def cb_order_yes(call: CallbackQuery, state: FSMContext):
    if _global_session is None:
        await call.answer("Сессия уже завершена.", show_alert=True)
        return
    if call.from_user.id != _global_owner_id:
        await call.answer(
            f"Это сессия {_global_owner_name} — только он может ответить.",
            show_alert=True,
        )
        return
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("✅ Подтверждено! Ожидай SMS от клиента...")
    await state.set_state(ReviewFlow.waiting_sms)
    await _global_session.order_confirm_queue.put("yes")


@router.callback_query(F.data == "order:no")
async def cb_order_no(call: CallbackQuery, state: FSMContext):
    if _global_session is None:
        await call.answer("Сессия уже завершена.", show_alert=True)
        return
    if call.from_user.id != _global_owner_id:
        await call.answer(
            f"Это сессия {_global_owner_name} — только он может ответить.",
            show_alert=True,
        )
        return
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("❌ Клиент не подтверждён. Возвращаю цену...")
    await state.clear()
    await _global_session.order_confirm_queue.put("no")


# ── Шаг 3: подтверждение заказа — текст (fallback) ───────────────────────────

@router.message(ReviewFlow.waiting_confirm, F.text.lower().in_(["да", "yes", "✅", "+"]))
async def confirm_order_yes(message: Message, state: FSMContext):
    if not _global_session:
        await message.answer("Сессия не найдена.")
        return
    await message.answer("✅ Подтверждено! Ожидай SMS от клиента...", reply_markup=ReplyKeyboardRemove())
    await state.set_state(ReviewFlow.waiting_sms)
    await _global_session.order_confirm_queue.put("yes")


@router.message(ReviewFlow.waiting_confirm, F.text.lower().in_(["нет", "no", "❌", "-"]))
async def confirm_order_no(message: Message, state: FSMContext):
    if _global_session:
        await _global_session.order_confirm_queue.put("no")
    await state.clear()
    await message.answer("❌ Клиент не подтверждён. Возвращаю цену...", reply_markup=ReplyKeyboardRemove())


# Мастер написал SMS-код раньше времени
@router.message(ReviewFlow.waiting_confirm, F.text.regexp(r"^\d{4}$"))
async def handle_sms_code_early(message: Message):
    if not _global_session:
        return
    await _global_session.sms_code_queue.put(message.text.strip())
    await message.answer("📥 Код принят! Сначала ответь «да» или «нет» на вопрос выше.")


# Неожиданный ввод в waiting_confirm
@router.message(ReviewFlow.waiting_confirm)
async def confirm_unknown(message: Message):
    await message.answer("Ответь «да» или «нет» 👆")


# ── Шаг 4: SMS-код ───────────────────────────────────────────────────────────

@router.message(ReviewFlow.waiting_sms, F.text.regexp(r"^\d{4}$"))
async def handle_sms_code(message: Message):
    if not _global_session:
        await message.answer("Сессия не найдена.")
        return
    await message.answer("⌨️ Ввожу код в Kaspi...", reply_markup=ReplyKeyboardRemove())
    await _global_session.sms_code_queue.put(message.text.strip())


@router.message(ReviewFlow.waiting_sms)
async def handle_sms_wrong(message: Message):
    await message.answer("Код — ровно 4 цифры. Попробуй ещё раз:")


# ── Ловим "да/нет" от мастеров не в FSM-состоянии ───────────────────────────

@router.message(F.text.lower().in_(["да", "нет", "yes", "no", "✅", "❌", "+", "-"]))
async def handle_yes_no_busy(message: Message):
    if _global_session is not None:
        await message.answer(
            f"⏳ {_global_owner_name} ведёт сессию{_elapsed_str()}.\n"
            f"Дождись завершения."
        )


# ── Helper ────────────────────────────────────────────────────────────────────

async def _run_session_and_cleanup(
    session: ReviewSession,
    offer_id: str,
    product_name: str,
    product_desc: str,
    user_id: int,
    state: FSMContext,
):
    global _global_session, _global_owner_id, _global_owner_name, _global_started_at, _global_owner_state
    try:
        await session.start(offer_id, product_name, product_desc)
    finally:
        _global_session     = None
        _global_owner_id    = None
        _global_owner_name  = ""
        _global_started_at  = None
        _global_owner_state = None
        await state.clear()
