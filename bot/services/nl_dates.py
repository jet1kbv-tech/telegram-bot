from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
MONTH_NAMES = {**MONTHS, "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
               "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
               "январе": 1, "феврале": 2, "марте": 3, "апреле": 4, "мае": 5, "июне": 6,
               "июле": 7, "августе": 8, "сентябре": 9, "октябре": 10, "ноябре": 11, "декабре": 12}
WEEKDAYS = {
    "понедельник": 0, "понедельника": 0, "понедельнику": 0,
    "вторник": 1, "вторника": 1, "вторнику": 1,
    "среду": 2, "среда": 2, "среды": 2,
    "четверг": 3, "четверга": 3, "четвергу": 3,
    "пятницу": 4, "пятница": 4, "пятницы": 4,
    "субботу": 5, "суббота": 5, "субботы": 5,
    "воскресенье": 6, "воскресенья": 6,
}


class DateExpressionError(ValueError):
    pass


def zoned_now(timezone: str, now: datetime | None = None) -> datetime:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise DateExpressionError("unknown_timezone") from exc
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def resolve_date_expression(expression: str, *, now: datetime, timezone: str) -> str:
    local_now = zoned_now(timezone, now)
    text = re.sub(r"\s+", " ", expression.strip().casefold())
    today = local_now.date()
    relative = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
    if text in relative:
        return (today + timedelta(days=relative[text])).isoformat()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    numeric = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", text)
    if numeric:
        return _next_date(int(numeric.group(1)), int(numeric.group(2)), today).isoformat()
    named = re.fullmatch(r"(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?", text)
    if named and named.group(2) in MONTHS:
        day, month = int(named.group(1)), MONTHS[named.group(2)]
        if named.group(3):
            try:
                return date(int(named.group(3)), month, day).isoformat()
            except ValueError as exc:
                raise DateExpressionError("invalid_date") from exc
        return _next_date(day, month, today).isoformat()
    next_week = "следующ" in text
    for word, weekday in WEEKDAYS.items():
        if re.search(rf"\b{word}\b", text):
            delta = (weekday - today.weekday()) % 7
            if next_week:
                delta = delta + 7 if delta else 7
            elif delta == 0:
                delta = 7
            return (today + timedelta(days=delta)).isoformat()
    raise DateExpressionError("unsupported_date")


def resolve_time_expression(expression: str) -> str:
    text = re.sub(r"\s+", " ", expression.strip().casefold())
    vague = {"вечером": "19:00", "утром": "09:00", "днем": "13:00", "днём": "13:00"}
    if text in vague:
        return vague[text]
    match = re.search(r"(?<!\d)([01]?\d|2[0-3])(?:[:.]([0-5]\d))?(?!\d)", text)
    if not match:
        raise DateExpressionError("unsupported_time")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if "вечера" in text and hour < 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def resolve_date_range(expression: str, *, now: datetime, timezone: str) -> tuple[str, str]:
    """Resolve supported NL ranges using ISO weeks (Monday–Sunday) and Sat–Sun weekends."""
    local_now = zoned_now(timezone, now)
    today = local_now.date()
    text = re.sub(r"\s+", " ", expression.strip().casefold())
    text = re.sub(r"^(?:на|в)\s+", "", text)
    if text in {"выходные", "эти выходные", "следующие выходные"}:
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        if text == "следующие выходные":
            saturday += timedelta(days=7)
        return saturday.isoformat(), (saturday + timedelta(days=1)).isoformat()
    if text in {"эта неделя", "этой неделе", "следующая неделя", "следующей неделе"}:
        monday = today - timedelta(days=today.weekday())
        if text.startswith("следующ"):
            monday += timedelta(days=7)
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    if text in MONTH_NAMES:
        month = MONTH_NAMES[text]
        year = today.year + (1 if month < today.month else 0)
        first = date(year, month, 1)
        following = date(year + (month == 12), month % 12 + 1, 1)
        return first.isoformat(), (following - timedelta(days=1)).isoformat()
    single = resolve_date_expression(text, now=local_now, timezone=timezone)
    return single, single


def _next_date(day: int, month: int, today: date) -> date:
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    raise DateExpressionError("invalid_date")
