import pytest
from pathlib import Path

from bot.services.ics_birthdays import ImportCandidate, clean_birthday_title, parse_ics_birthdays, split_duplicates


def calendar(*events):
    return "BEGIN:VCALENDAR\r\n" + "\r\n".join(
        "BEGIN:VEVENT\r\n" + event + "\r\nEND:VEVENT" for event in events) + "\r\nEND:VCALENDAR"


def test_yearly_date_folded_and_escaped_summary():
    result = parse_ics_birthdays(calendar(
        "SUMMARY:Birthday: Alex\\, Jr.\r\nDTSTART;VALUE=DATE:19940503\r\nRRULE:FREQ=YEAR\r\n LY"))
    assert result.event_count == 1
    assert result.candidates == (ImportCandidate("Alex, Jr.", 5, 3, None),)


def test_multiple_and_non_birthday_events_are_ignored_conservatively():
    result = parse_ics_birthdays(calendar(
        "SUMMARY:День рождения: Лёша\r\nDTSTART;VALUE=DATE:20000229\r\nRRULE:FREQ=YEARLY",
        "SUMMARY:Weekly meeting\r\nDTSTART:20260915T120000\r\nRRULE:FREQ=YEARLY",
        "SUMMARY:Anniversary birthday\r\nDTSTART;VALUE=DATE:20000101\r\nRRULE:FREQ=YEARLY"))
    assert [(x.title, x.month, x.day) for x in result.candidates] == [("Лёша", 2, 29)]


def test_explicit_birth_year_extension_is_only_reliable_year_heuristic():
    ordinary = parse_ics_birthdays(calendar(
        "SUMMARY:Birthday: Alex\r\nDTSTART;VALUE=DATE:19940503\r\nRRULE:FREQ=YEARLY"))
    reliable = parse_ics_birthdays(calendar(
        "SUMMARY:Birthday: Alex\r\nDTSTART;VALUE=DATE:20000503\r\nRRULE:FREQ=YEARLY\r\nX-BIRTHDAY-YEAR:1994"))
    assert ordinary.candidates[0].year is None
    assert reliable.candidates[0].year == 1994


def test_malformed_candidate_does_not_crash_and_is_counted():
    result = parse_ics_birthdays(calendar(
        "SUMMARY:Birthday: Bad\r\nDTSTART;VALUE=DATE:20260231\r\nRRULE:FREQ=YEARLY",
        "SUMMARY:Birthday: Good\r\nDTSTART;VALUE=DATE:20260503\r\nRRULE:FREQ=YEARLY"))
    assert [x.title for x in result.candidates] == ["Good"] and result.skipped_count == 1


def test_empty_and_malformed_calendar_rejected():
    for value in ("", "not an ics"):
        with pytest.raises(ValueError): parse_ics_birthdays(value)


def test_duplicates_ignore_year_and_normalize_case_space_and_yo():
    candidates = [ImportCandidate("  ЛЁША ", 5, 3, 1994), ImportCandidate("Мама", 11, 12)]
    new, duplicate = split_duplicates(candidates, [{"title": "леша", "month": 5, "day": 3, "year": None}])
    assert [x.title for x in new] == ["Мама"] and [x.title for x in duplicate] == ["  ЛЁША "]


def test_title_cleanup():
    assert clean_birthday_title("Alex's birthday") == "Alex"
    assert clean_birthday_title("День рождения: Лёша") == "Лёша"


def test_realistic_google_contacts_birthday_calendar_without_summary_markers():
    content = (Path(__file__).parent / "fixtures" / "google_birthdays.ics").read_bytes()
    result = parse_ics_birthdays(content)
    assert [(item.title, item.month, item.day, item.year) for item in result.candidates] == [
        ("Alex", 5, 3, None),
        ("Лёша", 2, 29, None),
    ]


def test_markerless_yearly_google_event_outside_contacts_calendar_is_ignored():
    content = calendar("SUMMARY:Quarterly planning\r\nDTSTART;VALUE=DATE:20260503\r\nRRULE:FREQ=YEARLY")
    content = content.replace("BEGIN:VCALENDAR", "BEGIN:VCALENDAR\r\nPRODID:-//Google Inc//Google Calendar 70.9054//EN\r\nX-WR-CALNAME:Work")
    assert parse_ics_birthdays(content).candidates == ()
