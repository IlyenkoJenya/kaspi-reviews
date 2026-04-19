# agent/session.py
#
# Улучшения v3:
#
# 1. ГАРАНТИРОВАННЫЙ ВОЗВРАТ ЦЕНЫ
#    - _safe_restore_price(): 3 попытки с паузой, с проверкой результата
#    - Вызывается в finally — при ЛЮБОМ выходе из флоу
#    - Если страница упала — _restore_price_new_browser() открывает новый браузер
#    - Если всё провалилось — мастер получает данные для ручного возврата со ссылкой
#
# 2. SMS-КОД В ЛЮБОЙ МОМЕНТ
#    - sms_code_queue буферизует код — мастер может написать его заранее
#    - handlers.py принимает 4 цифры и в waiting_sms, и в waiting_confirm
#
# 3. ТАЙМАУТ С УВЕДОМЛЕНИЕМ
#    - Все таймауты сопровождаются сообщением мастеру
#    - После таймаута всегда пытаемся вернуть цену
#
# 4. ФЛАГ _price_changed
#    - Возврат цены происходит только если цена реально была изменена
#    - Снимается после успешного возврата — нет двойных попыток

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from playwright.sync_api import sync_playwright
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Inline-клавиатура подтверждения заказа
CONFIRM_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="✅ Да", callback_data="order:yes"),
    InlineKeyboardButton(text="❌ Нет", callback_data="order:no"),
]])

from agent.kaspi_login import login
from agent.kaspi_actions import (
    open_price_modal,
    get_price_from_modal,
    set_price_in_modal,
    get_product_link,
)
from agent.wait_for_order import PICKUP_URL, DELIVERY_URL, _check_orders
from agent.deliver_order_flow import send_sms_for_delivery, confirm_delivery

_executor = ThreadPoolExecutor(max_workers=4)

CONFIRM_TIMEOUT  = 300   # 5 мин на подтверждение заказа
SMS_TIMEOUT      = 300   # 5 мин на ввод SMS-кода
RESTORE_ATTEMPTS = 3     # попыток вернуть цену
RESTORE_DELAY    = 10    # секунд между попытками

_CANCEL_SENTINEL = object()


class ReviewSession:

    def __init__(self, user_id: int, notify_callback):
        self.user_id  = user_id
        self.notify   = notify_callback
        self.loop     = asyncio.get_event_loop()

        # Очереди бот → агент
        self.order_confirm_queue = asyncio.Queue()
        self.sms_code_queue      = asyncio.Queue()  # буферизует код даже если написан заранее

        self._cancel_event  = threading.Event()
        self._current_stage = "init"
        self._offer_id_ref  = None
        self._old_price_ref = None
        self._price_changed = False  # True если цена была снижена и требует возврата

        self._product_name  = ""
        self._product_desc  = ""

    # ──────────────────────────────────────────────────────────────────────
    # PUBLIC
    # ──────────────────────────────────────────────────────────────────────

    async def start(self, offer_id: str, product_name: str = "", product_desc: str = ""):
        self._product_name = product_name
        self._product_desc = product_desc
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._run_sync, offer_id)

    def cancel(self) -> str:
        self._cancel_event.set()
        for q in [self.order_confirm_queue, self.sms_code_queue]:
            try:
                self.loop.call_soon_threadsafe(q.put_nowait, _CANCEL_SENTINEL)
            except Exception:
                pass
        return self._current_stage

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ──────────────────────────────────────────────────────────────────────
    # MAIN
    # ──────────────────────────────────────────────────────────────────────

    def _run_sync(self, offer_id: str):
        """Верхний уровень: запускает браузер и ловит его падение."""
        self._offer_id_ref = offer_id
        page = None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page()
                self._main_flow(page, offer_id)

        except Exception as e:
            # Браузер упал совсем — пробуем вернуть цену в новом браузере
            print(f"❌ Браузер упал: {e}")
            if self._price_changed and self._old_price_ref is not None:
                self._notify_sync("⚠️ Браузер упал. Открываю новый сеанс для возврата цены...")
                self._restore_price_new_browser(offer_id, self._old_price_ref)

    def _main_flow(self, page, offer_id: str):
        """
        Основной флоу сессии.
        Возврат цены гарантирован через finally.
        """
        old_price = None

        try:
            # ── 1. ЛОГИН ──────────────────────────────────────────────────
            self._check_cancelled()
            login(page)

            # ── 2. ЧИТАЕМ СТАРУЮ ЦЕНУ ─────────────────────────────────────
            self._check_cancelled()
            page_price = open_price_modal(page, offer_id)
            modal_price = get_price_from_modal(page)
            old_price = page_price if (page_price and page_price > 0) else modal_price
            if not old_price or old_price == 0:
                old_price = None
                print("Цена не задана, возврат не нужен")
            else:
                self._old_price_ref = old_price
                print(f"💰 Старая цена: {old_price}₸")

            # ── 3. СТАВИМ 100₸ ────────────────────────────────────────────
            self._check_cancelled()
            set_price_in_modal(page, 100)
            self._price_changed = True   # <-- с этого момента цену нужно вернуть
            self._current_stage = "price_set"

            product_link = get_product_link(page)
            self._notify_sync(
                f"✅ Готово! Цена снижена до 100 ₸\n\n"
                f"🔗 Ссылка для клиента:\n{product_link}\n\n"
                f"⏳ Жду появления заказа...\n"
                f"Для отмены — «отмени отзыв» или /cancel"
            )

            # ── 4. ЖДЁМ ЗАКАЗ ─────────────────────────────────────────────
            self._check_cancelled()
            order = self._wait_for_order_cancellable(page)

            if self.is_cancelled:
                raise _CancelledByUser()

            if not order:
                self._notify_sync(
                    "⏰ Заказ не появился за отведённое время.\n"
                    "Возвращаю цену..."
                )
                return  # finally вернёт цену

            # ── 5. ПОДТВЕРЖДЕНИЕ ЗАКАЗА ───────────────────────────────────
            self._current_stage = "order_found"
            order_type = "🏪 Самовывоз" if order.get("type") == "pickup" else "🚚 Доставка"
            print(f"🆔 Заказ найден: №{order['order_id']}, клиент: {order['customer']}, тип: {order_type}")
            self._notify_sync(
                f"📦 Найден заказ!\n"
                f"{order_type}\n"
                f"👤 Клиент: {order['customer']}\n"
                f"🆔 Заказ №: {order['order_id']}\n\n"
                f"Это твой клиент?",
                reply_markup=CONFIRM_KB,
            )

            confirm = self._wait_from_bot(
                self.order_confirm_queue,
                timeout=CONFIRM_TIMEOUT,
                timeout_msg="подтверждения заказа"
            )

            if confirm is _CANCEL_SENTINEL or self.is_cancelled:
                raise _CancelledByUser()

            if confirm != "yes":
                self._notify_sync("❌ Клиент не подтверждён. Возвращаю цену...")
                return  # finally вернёт цену

            # ── 6. ОТПРАВЛЯЕМ SMS ─────────────────────────────────────────
            self._check_cancelled()
            send_sms_for_delivery(page, order["order_id"])
            self._current_stage = "sms_sent"
            self._notify_sync(
                "📲 SMS отправлен клиенту!\n\n"
                "Введи 4-значный код из SMS:\n"
                "просто цифры, например: 1234\n\n"
                "⚠️ На этом этапе отмена невозможна — нужно завершить выдачу"
            )

            # ── 7. ЖДЁМ SMS-КОД ───────────────────────────────────────────
            # Если мастер написал код ДО этого момента — он уже в очереди, берём сразу
            sms_code = self._wait_from_bot(
                self.sms_code_queue,
                timeout=SMS_TIMEOUT,
                timeout_msg="SMS-кода"
            )

            # Если пришёл sentinel (попытка отмены) — всё равно нужен код
            while sms_code is _CANCEL_SENTINEL:
                self._notify_sync(
                    "⚠️ SMS уже отправлен — отмена невозможна!\n"
                    "Введи 4-значный код чтобы завершить выдачу:"
                )
                sms_code = self._wait_from_bot(
                    self.sms_code_queue,
                    timeout=SMS_TIMEOUT,
                    timeout_msg="SMS-кода (после попытки отмены)"
                )

            # ── 8. ПОДТВЕРЖДАЕМ ВЫДАЧУ ────────────────────────────────────
            confirm_delivery(page, sms_code)
            self._current_stage = "done"

            # ── 9. ВОЗВРАЩАЕМ ЦЕНУ ────────────────────────────────────────
            # Делаем явно здесь (в finally тоже будет попытка — это нормально)
            success = self._safe_restore_price(page, offer_id, old_price)
            if success:
                self._price_changed = False  # снимаем флаг — возврат уже сделан
                self._notify_sync(
                    "🎉 Заказ выдан! Цена возвращена.\n\n"
                    "⏳ Генерирую варианты отзыва..."
                )
            else:
                # finally попробует ещё раз
                self._notify_sync("🎉 Заказ выдан! Генерирую отзыв...")

            # ── 🔟 ГЕНЕРИРУЕМ ОТЗЫВЫ ──────────────────────────────────────
            self._generate_and_send_reviews()

        except _CancelledByUser:
            if self._price_changed:
                self._notify_sync(
                    f"🚫 Сессия отменена.\n"
                    f"Возвращаю цену..."
                )
            else:
                self._notify_sync("🚫 Отменено. Цена не была изменена.")

        except _TimeoutWaiting as e:
            self._notify_sync(
                f"⏰ Время ожидания вышло: {e}.\n"
                f"Возвращаю цену..."
            )

        except Exception as e:
            print(f"❌ Flow error: {e}")
            self._notify_sync(
                f"❌ Ошибка: {e}\n"
                f"Пробую вернуть цену..."
            )

        finally:
            # ГАРАНТИРОВАННЫЙ ВОЗВРАТ — выполняется при ЛЮБОМ выходе
            if self._price_changed and old_price is not None:
                success = self._safe_restore_price(page, offer_id, old_price)
                if success:
                    self._price_changed = False
                    self._notify_sync(f"✅ Цена возвращена: {old_price} ₸")
                else:
                    # Все 3 попытки провалились — отправляем данные для ручного возврата
                    self._notify_sync(
                        f"🚨 ВНИМАНИЕ! Не удалось вернуть цену автоматически!\n\n"
                        f"Верни вручную в Kaspi Merchant:\n"
                        f"Offer ID: {offer_id}\n"
                        f"Цена должна быть: {old_price} ₸\n\n"
                        f"https://kaspi.kz/mc/#/offer/{offer_id}"
                    )

    # ──────────────────────────────────────────────────────────────────────
    # НАДЁЖНЫЙ ВОЗВРАТ ЦЕНЫ
    # ──────────────────────────────────────────────────────────────────────

    def _safe_restore_price(self, page, offer_id: str, old_price: int) -> bool:
        """
        3 попытки вернуть цену с проверкой результата.
        Возвращает True если цена успешно восстановлена.
        """
        for attempt in range(1, RESTORE_ATTEMPTS + 1):
            try:
                print(f"🔁 Возврат цены, попытка {attempt}/{RESTORE_ATTEMPTS}...")
                open_price_modal(page, offer_id)
                set_price_in_modal(page, old_price)

                # Верифицируем — открываем модалку снова и проверяем
                open_price_modal(page, offer_id)
                current_price = get_price_from_modal(page)

                if current_price == old_price:
                    print(f"✅ Цена возвращена: {old_price}₸")
                    return True
                else:
                    print(f"⚠️ Цена {current_price}₸ ≠ {old_price}₸, повторяю...")

            except Exception as e:
                print(f"⚠️ Попытка {attempt} провалилась: {e}")

            if attempt < RESTORE_ATTEMPTS:
                time.sleep(RESTORE_DELAY)

        print(f"❌ Все {RESTORE_ATTEMPTS} попытки вернуть цену провалились")
        return False

    def _restore_price_new_browser(self, offer_id: str, old_price: int):
        """Резервный метод: новый браузер + логин + возврат цены."""
        try:
            print("🔄 Открываю новый браузер для возврата цены...")
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page()
                login(page)
                success = self._safe_restore_price(page, offer_id, old_price)
                browser.close()

            if success:
                self._price_changed = False
                self._notify_sync(f"✅ Цена возвращена через новый сеанс: {old_price} ₸")
            else:
                self._notify_sync(
                    f"🚨 КРИТИЧНО! Верни цену вручную!\n\n"
                    f"Offer ID: {offer_id}\n"
                    f"Цена должна быть: {old_price} ₸\n\n"
                    f"https://kaspi.kz/mc/#/offer/{offer_id}"
                )

        except Exception as e:
            print(f"❌ Новый браузер тоже упал: {e}")
            self._notify_sync(
                f"🚨 КРИТИЧНО! Верни цену вручную немедленно!\n\n"
                f"Offer ID: {offer_id}\n"
                f"Цена должна быть: {old_price} ₸\n\n"
                f"https://kaspi.kz/mc/#/offer/{offer_id}"
            )

    # ──────────────────────────────────────────────────────────────────────
    # ГЕНЕРАЦИЯ ОТЗЫВОВ
    # ──────────────────────────────────────────────────────────────────────

    def _generate_and_send_reviews(self):
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._async_generate_reviews(), self.loop
            )
            future.result(timeout=30)
        except Exception as e:
            print(f"⚠️ Review generation failed: {e}")
            self._notify_sync(
                "⚠️ Не удалось сгенерировать отзыв автоматически.\n"
                "Клиент может написать своими словами в приложении Kaspi."
            )

    async def _async_generate_reviews(self):
        from review_generator import generate_reviews

        reviews = await generate_reviews(
            product_name=self._product_name,
            product_description=self._product_desc,
        )

        if not reviews:
            await self.notify(self.user_id,
                "⚠️ Не удалось сгенерировать отзыв.\n"
                "Клиент может написать сам в приложении Kaspi."
            )
            return

        lines = [
            "📝 Варианты отзыва для клиента:\n",
            "Покажи клиенту — пусть выберет и скопирует\n",
        ]
        for r in reviews:
            lines.append(f"{r['label']}:")
            lines.append(r['text'])
            lines.append("")

        lines.append("✅ Как оставить отзыв: Kaspi → Мои заказы → Оценить")
        await self.notify(self.user_id, "\n".join(lines))

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _check_cancelled(self):
        if self._cancel_event.is_set():
            raise _CancelledByUser()

    def _wait_for_order_cancellable(self, page):
        max_attempts = 40
        for attempt in range(max_attempts):
            if self._cancel_event.is_set():
                return None

            print(f"\n🔍 Попытка {attempt + 1}/{max_attempts}")

            if self._cancel_event.is_set():
                return None
            try:
                page.goto(PICKUP_URL)
                page.wait_for_timeout(5000)
                order = _check_orders(page, 100)
                if order:
                    order["type"] = "pickup"
                    return order
            except Exception as e:
                print(f"⚠️ PICKUP error: {e}")

            if self._cancel_event.is_set():
                return None
            try:
                page.goto(DELIVERY_URL)
                page.wait_for_timeout(5000)
                order = _check_orders(page, 100)
                if order:
                    order["type"] = "delivery"
                    return order
            except Exception as e:
                print(f"⚠️ DELIVERY error: {e}")

            for _ in range(6):
                if self._cancel_event.is_set():
                    return None
                time.sleep(1)

        return None

    def _stage_description(self) -> str:
        descriptions = {
            "init":        "ещё не началось / логин",
            "price_set":   "цена снижена до 100₸, ожидание заказа",
            "order_found": "заказ найден, ожидание подтверждения",
            "sms_sent":    "SMS уже отправлен клиенту",
            "done":        "завершено",
        }
        return descriptions.get(self._current_stage, self._current_stage)

    def _wait_from_bot(self, queue: asyncio.Queue, timeout: int, timeout_msg: str):
        future = asyncio.run_coroutine_threadsafe(queue.get(), self.loop)
        try:
            return future.result(timeout=timeout)
        except Exception:
            raise _TimeoutWaiting(timeout_msg)

    def _notify_sync(self, text: str, reply_markup=None):
        future = asyncio.run_coroutine_threadsafe(
            self.notify(self.user_id, text, reply_markup), self.loop
        )
        try:
            future.result(timeout=15)
        except Exception as e:
            print(f"⚠️ Notify failed: {e}")


class _CancelledByUser(Exception):
    pass


class _TimeoutWaiting(Exception):
    pass
