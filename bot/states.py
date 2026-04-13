# bot/states.py
from aiogram.fsm.state import State, StatesGroup


class ReviewFlow(StatesGroup):
    waiting_product = State()  # ждём код товара или "пропустить"
    waiting_confirm = State()  # ждём "да" / "нет" на найденный заказ
    waiting_sms     = State()  # ждём 4-значный SMS-код
