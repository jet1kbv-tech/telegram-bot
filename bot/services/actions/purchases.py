from __future__ import annotations

from datetime import datetime
from typing import Any

from bot.storage import make_id, normalize_purchase_item, storage


def create_purchase(arguments: dict[str, Any]) -> dict[str, Any]:
    title = str(arguments.get("title") or "").strip()
    if not title:
        raise ValueError("title_required")
    priority = arguments.get("priority") or ""
    if priority not in {"", "high", "medium", "low"}:
        raise ValueError("invalid_priority")
    price = arguments.get("price")
    if price is not None and (isinstance(price, bool) or not isinstance(price, int) or price < 0):
        raise ValueError("invalid_price")
    item = normalize_purchase_item({
        "id": make_id(), "title": title, "link": arguments.get("link") or "", "price": price,
        "priority": priority, "comment": arguments.get("comment") or "", "buyer": arguments.get("buyer") or "",
        "created_at": datetime.now().isoformat(timespec="seconds"), "bought_at": "",
    })
    if item is None:
        raise ValueError("invalid_purchase")
    def mutator(data: dict[str, Any]) -> None:
        data.setdefault("purchases", {}).setdefault("planned", []).append(item)
    storage.update(mutator)
    return item
