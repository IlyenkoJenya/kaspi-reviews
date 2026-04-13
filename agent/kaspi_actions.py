# agent/kaspi_actions.py
import time


def ensure_single_price_enabled(page):
    toggle = page.locator('[data-testid="toggle-single-price-enabled"]').first
    toggle.wait_for(state="visible", timeout=10000)
    if toggle.get_attribute("aria-checked") == "false":
        print("Включаем режим Одна цена...")
        toggle.click()
        page.wait_for_timeout(1000)


def handle_possible_alerts(page):
    print("Проверяем всплывающие окна...")
    if page.locator('[data-testid="price-change-alert-confirm-button"]').count() > 0:
        page.locator('[data-testid="price-change-alert-confirm-button"]').click()
        page.wait_for_timeout(2000)
    if page.locator('[data-testid="undefined-stock-alert-confrm-button"]').count() > 0:
        page.locator('[data-testid="undefined-stock-alert-confrm-button"]').click()
        page.wait_for_timeout(2000)


def open_price_modal(page, offer_id):
    """Открывает модалку цены с 3 попытками."""
    last_error = None
    for attempt in range(1, 4):
        try:
            print(f"Открываю товар {offer_id} (попытка {attempt}/3)...")
            page.goto(f"https://kaspi.kz/mc/#/offer/{offer_id}")
            page.wait_for_timeout(5000)

            print("Жду кнопку редактирования...")
            edit_btn = page.locator("text=Изменить цену и остатки").first
            edit_btn.wait_for(state="visible", timeout=30000)

            print("Открываю модалку...")
            edit_btn.click()
            page.wait_for_timeout(2000)

            modal = page.locator('[data-testid="single-price-edit-input"]')
            modal.wait_for(state="visible", timeout=20000)

            print("Модалка открыта")
            return

        except Exception as e:
            last_error = e
            print(f"Попытка {attempt} не удалась: {e}")
            if attempt < 3:
                time.sleep(5)
                try:
                    page.reload()
                    page.wait_for_timeout(3000)
                except Exception:
                    pass

    raise Exception(f"Не удалось открыть модалку после 3 попыток: {last_error}")


def get_price_from_modal(page):
    price_input = page.locator('[data-testid="single-price-edit-input"] input').first
    price_input.wait_for(state="visible", timeout=10000)
    raw_price = price_input.input_value()
    clean_price = "".join(filter(str.isdigit, raw_price))
    price = int(clean_price)
    print(f"Текущая цена: {price}")
    return price


def set_price_in_modal(page, new_price):
    print(f"Меняю цену на {new_price}...")
    ensure_single_price_enabled(page)

    page.evaluate("""
    (value) => {
        const inputs = document.querySelectorAll(
            '[data-testid="single-price-edit-input"] input'
        )
        for (const input of inputs) {
            if (input.offsetParent !== null) {
                input.value = value
                input.dispatchEvent(new Event('input', { bubbles: true }))
                input.dispatchEvent(new Event('change', { bubbles: true }))
            }
        }
    }
    """, str(new_price))

    page.wait_for_timeout(1000)

    save_btn = page.locator('[data-testid="stocks-modal-save"]')
    save_btn.wait_for(state="visible", timeout=10000)
    save_btn.click()

    handle_possible_alerts(page)
    page.wait_for_timeout(5000)
    print("Цена изменена!")


def get_product_link(page):
    print("Получаю ссылку на товар...")
    link = page.locator("a:has-text('Посмотреть на Kaspi.kz')").first
    link.wait_for(state="visible", timeout=30000)
    href = link.get_attribute("href")
    full_link = "https://kaspi.kz" + href
    print("Ссылка:", full_link)
    return full_link
