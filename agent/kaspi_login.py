# agent/kaspi_login.py
from config import KASPI_EMAIL, KASPI_PASS


def login(page):
    """
    Логин в Kaspi Merchant Cabinet.
    Использует поля #user_email_field / #password_field.
    """
    print("🔐 Opening Kaspi login...")
    page.goto("https://kaspi.kz/mc/")
    page.wait_for_selector("#user_email_field", timeout=60000)

    print("✉️ Entering email...")
    page.fill("#user_email_field", KASPI_EMAIL)
    page.locator("button:has-text('Продолжить')").click()

    page.wait_for_selector("#password_field", timeout=60000)

    print("🔑 Entering password...")
    page.fill("#password_field", KASPI_PASS)
    page.keyboard.press("Enter")

    page.wait_for_url("**/#/**", timeout=60000)
    page.wait_for_timeout(5000)

    print("✅ Logged in successfully")
