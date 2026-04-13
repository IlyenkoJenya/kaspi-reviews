# agent/wait_for_order.py
import time

PICKUP_URL   = "https://kaspi.kz/mc/#/orders-new?status=PICKUP"
DELIVERY_URL = "https://kaspi.kz/mc/#/orders-new?status=DELIVERY"


def _check_orders(page, price):
    """Ищет заказ с нужной ценой в уже загруженной таблице."""
    try:
        page.wait_for_timeout(4000)

        if not page.locator(".root-component").count():
            print("📭 No orders rendered yet")
            return None

        orders = page.locator(".root-component .rows")
        count = orders.count()
        print(f"📦 Found {count} orders")

        for i in range(count):
            order = orders.nth(i)
            text = order.inner_text()

            if str(price) in text:
                print("✅ ORDER FOUND!")

                order_id = order.locator("a").inner_text()
                customer = order.locator("span").first.inner_text()

                return {
                    "order_id": order_id.strip(),
                    "customer": customer.strip(),
                }

    except Exception as e:
        print(f"⚠️ Table not ready yet: {e}")

    return None


def wait_for_order(page, offer_id, price, max_attempts=40):
    """
    Polling заказов. Проверяет PICKUP и DELIVERY каждые ~15 сек.
    Возвращает dict с order_id и customer или None по таймауту.
    """
    for attempt in range(max_attempts):
        print(f"\n🔍 Attempt {attempt + 1}/{max_attempts}")

        # PICKUP
        print("📦 Checking PICKUP...")
        page.goto(PICKUP_URL)
        page.wait_for_timeout(5000)
        order = _check_orders(page, price)
        if order:
            order["type"] = "pickup"
            print("📦 Found in PICKUP")
            return order

        # DELIVERY
        print("🚚 Checking DELIVERY...")
        page.goto(DELIVERY_URL)
        page.wait_for_timeout(5000)
        order = _check_orders(page, price)
        if order:
            order["type"] = "delivery"
            print("🚚 Found in DELIVERY")
            return order

        print("⏳ Not found, waiting 6 sec...")
        time.sleep(6)

    print("❌ Order not found after all attempts")
    return None
