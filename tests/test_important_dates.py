from copy import deepcopy
from datetime import date

from bot.services.important_dates import (age_on_next_occurrence, days_until, effective_occurrence,
    is_visible, next_occurrence, occurrence_in_range, parse_birthday_date, sort_birthdays)
from bot.storage import JsonStorage, normalize_important_date


def birthday(**extra):
    return {"id": "b", "type": "birthday", "title": "Лёша", "month": 5, "day": 3,
            "year": None, "visibility": "shared", "note": "", "created_by": "vova", **extra}


def test_old_storage_and_normalization(tmp_path):
    store = JsonStorage(tmp_path / "data.json")
    store.path.write_text("{}", encoding="utf-8")
    assert store.load()["important_dates"] == []
    assert normalize_important_date(birthday(created_by="VOVA"))["created_by"] == "vova"
    assert normalize_important_date(birthday(year=1994))["year"] == 1994


def test_invalid_storage_values_are_rejected():
    assert normalize_important_date(birthday(month=13)) is None
    assert normalize_important_date(birthday(day=31, month=2)) is None
    assert normalize_important_date(birthday(day=29, month=2, year=2023)) is None
    assert normalize_important_date(birthday(visibility="everyone")) is None


def test_date_logic_today_future_past_age_and_range():
    today = date(2026, 9, 15)
    future = birthday(month=9, day=20, year=1994)
    past = birthday(month=9, day=10, year=1994)
    assert next_occurrence(future, today) == date(2026, 9, 20)
    assert age_on_next_occurrence(future, today) == 32
    assert next_occurrence(past, today) == date(2027, 9, 10)
    assert age_on_next_occurrence(past, today) == 33
    assert days_until(birthday(month=9, day=15), today) == 0
    assert age_on_next_occurrence(birthday(), today) is None
    assert occurrence_in_range(future, today, date(2026, 9, 20)) == date(2026, 9, 20)


def test_february_29_policy_and_new_year_sorting():
    leap = birthday(month=2, day=29)
    assert effective_occurrence(leap, 2028) == date(2028, 2, 29)
    assert effective_occurrence(leap, 2027) == date(2027, 2, 28)
    items = [birthday(id="jan", month=1, day=2), birthday(id="dec", month=12, day=20)]
    assert [x["id"] for x in sort_birthdays(items, date(2026, 12, 15))] == ["dec", "jan"]


def test_visibility():
    assert is_visible(birthday(), "vova") and is_visible(birthday(), "sasha")
    assert is_visible(birthday(visibility="vova"), "vova")
    assert not is_visible(birthday(visibility="vova"), "sasha")
    assert not is_visible(birthday(visibility="sasha"), "vova")


def test_manual_date_formats_and_invalid_values():
    assert parse_birthday_date("3 мая") == (5, 3, None)
    assert parse_birthday_date("03.05") == (5, 3, None)
    assert parse_birthday_date("03.05.1994") == (5, 3, 1994)
    for value in ("31 февраля", "32.05", "00.12"):
        try: parse_birthday_date(value)
        except ValueError: pass
        else: raise AssertionError(value)


def test_occurrence_helpers_do_not_mutate():
    item = birthday(month=2, day=29); before = deepcopy(item)
    next_occurrence(item, date(2027, 1, 1)); assert item == before
