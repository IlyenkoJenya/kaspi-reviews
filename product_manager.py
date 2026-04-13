# product_manager.py

import json
import os

PRODUCTS_FILE = os.path.join(os.path.dirname(__file__), "products.json")


def _load() -> dict:
    if not os.path.exists(PRODUCTS_FILE):
        _save({"products": []})
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_all_products() -> list[dict]:
    return _load().get("products", [])


def get_offer_ids() -> list[str]:
    return [p["offer_id"] for p in get_all_products()]


def get_product_by_offer_id(offer_id: str) -> dict | None:
    """Возвращает полный словарь товара по offer_id."""
    for p in get_all_products():
        if p["offer_id"] == offer_id:
            return p
    return None


def add_product(offer_id: str, name: str, description: str = "") -> tuple[bool, str]:
    offer_id = offer_id.strip()
    name = name.strip()

    if not offer_id:
        return False, "offer_id не может быть пустым"

    data = _load()
    products = data.get("products", [])

    for p in products:
        if p["offer_id"] == offer_id:
            return False, f"Товар `{offer_id}` уже есть в списке как «{p['name']}»"

    products.append({
        "offer_id": offer_id,
        "name": name or offer_id,
        "description": description,
    })
    data["products"] = products
    _save(data)

    return True, f"✅ Товар добавлен:\n📦 `{offer_id}`\n📝 {name or offer_id}"


def remove_product(offer_id: str) -> tuple[bool, str]:
    offer_id = offer_id.strip()
    data = _load()
    products = data.get("products", [])

    removed = None
    new_products = []
    for p in products:
        if p["offer_id"] == offer_id:
            removed = p
        else:
            new_products.append(p)

    if removed is None:
        return False, f"❌ Товар `{offer_id}` не найден в списке"

    data["products"] = new_products
    _save(data)

    return True, f"🗑 Товар удалён:\n📦 `{offer_id}`\n📝 {removed['name']}"


def format_products_list() -> str:
    products = get_all_products()

    if not products:
        return "📭 Список товаров пуст.\nДобавь товар: `/add_product <offer_id> <название>`"

    lines = ["📦 *Список товаров для отзывов:*\n"]
    for i, p in enumerate(products, 1):
        desc = f"\n   _{p['description']}_" if p.get("description") else ""
        lines.append(f"{i}. *{p['name']}*\n   `{p['offer_id']}`{desc}")

    lines.append(f"\nВсего: {len(products)} шт.")
    return "\n".join(lines)
