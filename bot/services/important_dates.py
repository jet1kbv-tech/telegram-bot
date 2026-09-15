from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Iterable

MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")
MONTH_NUMBERS = {name: number for number, name in enumerate(MONTHS) if name}
MONTH_NUMBERS.update({"январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5,
                      "июнь": 6, "июль": 7, "август": 8, "сентябрь": 9,
                      "октябрь": 10, "ноябрь": 11, "декабрь": 12})


def effective_occurrence(item: dict, year: int) -> date:
    month, day = int(item["month"]), int(item["day"])
    if month == 2 and day == 29 and not calendar.isleap(year):
        day = 28
    return date(year, month, day)


def next_occurrence(item: dict, today: date) -> date:
    occurrence = effective_occurrence(item, today.year)
    return occurrence if occurrence >= today else effective_occurrence(item, today.year + 1)


def days_until(item: dict, today: date) -> int:
    return (next_occurrence(item, today) - today).days


def age_on_next_occurrence(item: dict, today: date) -> int | None:
    year = item.get("year")
    return next_occurrence(item, today).year - int(year) if year is not None else None


def sort_birthdays(items: Iterable[dict], today: date) -> list[dict]:
    return sorted(items, key=lambda item: (next_occurrence(item, today), comparison_title(item["title"]), item["id"]))


def occurrence_in_range(item: dict, start: date, end: date) -> date | None:
    for year in range(start.year, end.year + 1):
        occurrence = effective_occurrence(item, year)
        if start <= occurrence <= end:
            return occurrence
    return None


def is_visible(item: dict, actor_key: str) -> bool:
    return item.get("visibility") in {"shared", actor_key}


def comparison_title(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def parse_birthday_date(value: str) -> tuple[int, int, int | None]:
    text = " ".join(value.strip().casefold().split())
    numeric = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?", text)
    words = re.fullmatch(r"(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?", text)
    if numeric:
        day, month, year = numeric.groups()
    elif words and words.group(2) in MONTH_NUMBERS:
        day, month, year = words.group(1), str(MONTH_NUMBERS[words.group(2)]), words.group(3)
    else:
        raise ValueError("unsupported birthday date")
    item = {"month": int(month), "day": int(day)}
    effective_occurrence(item, 2000)
    parsed_year = int(year) if year else None
    if parsed_year is not None:
        date(parsed_year, int(month), int(day))
    return int(month), int(day), parsed_year


def format_date(item: dict, include_year: bool = False) -> str:
    result = f"{item['day']} {MONTHS[item['month']]}"
    return f"{result} {item['year']}" if include_year and item.get("year") is not None else result


def russian_age(age: int) -> str:
    if age % 10 == 1 and age % 100 != 11:
        unit = "год"
    elif age % 10 in {2, 3, 4} and age % 100 not in {12, 13, 14}:
        unit = "года"
    else:
        unit = "лет"
    return f"{age} {unit}"
