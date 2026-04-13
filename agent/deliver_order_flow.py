# agent/deliver_order_flow.py


def send_sms_for_delivery(page, order_id):
    """
    1️⃣ Переходит на страницу заказа
    2️⃣ Нажимает первую кнопку 'Выдать заказ'
    3️⃣ Kaspi отправляет SMS клиенту
    4️⃣ Ждёт появления поля для ввода кода
    """
    print(f"📨 Открываем заказ {order_id}...")
    page.goto(f"https://kaspi.kz/mc/#/orders/{order_id}")
    page.wait_for_timeout(6000)

    print("📨 Нажимаем 'Выдать заказ' для отправки SMS...")
    issue_btn = page.locator("button:has-text('Выдать заказ')").first
    issue_btn.wait_for(state="visible", timeout=60000)
    issue_btn.click()

    print("📨 SMS отправлена клиенту")
    page.wait_for_selector("input[placeholder='Введите SMS-код']", timeout=60000)


def confirm_delivery(page, code):
    """
    1️⃣ Вводит SMS-код
    2️⃣ Нажимает вторую кнопку 'Выдать заказ' (подтверждение)
    3️⃣ Ждёт модалку 'Заказ выдан!'
    4️⃣ Нажимает OK
    """
    print("📲 Waiting SMS modal...")
    sms_input = page.locator("input[placeholder='Введите SMS-код']").first
    sms_input.wait_for(state="visible", timeout=60000)

    print("✏️ Entering SMS code...")
    sms_input.fill(code)
    page.wait_for_timeout(1000)

    print("📦 Confirming delivery...")
    page.locator("button:has-text('Выдать заказ')").last.click()

    print("⏳ Waiting success modal...")
    success_modal = page.locator("text=выдан!")
    success_modal.wait_for(state="visible", timeout=60000)
    print("✅ Order delivered!")

    ok_btn = page.locator("button:has-text('OK')").last
    ok_btn.click()

    success_modal.wait_for(state="hidden", timeout=30000)
    page.wait_for_timeout(2000)
    print("🟢 Success modal closed")
