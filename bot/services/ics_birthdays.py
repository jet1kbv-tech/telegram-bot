from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from bot.services.important_dates import comparison_title

MAX_ICS_BYTES = 3 * 1024 * 1024
MAX_IMPORT_CANDIDATES = 1000
NEGATIVE_MARKERS = ("anniversary", "годовщина", "holiday", "праздник", "meeting", "встреча")
BIRTHDAY_MARKERS = ("birthday", "birthdays", "день рождения", "дни рождения")


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    title: str
    month: int
    day: int
    year: int | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    candidates: tuple[ImportCandidate, ...]
    event_count: int
    skipped_count: int


def _unescape(value: str) -> str:
    return value.replace(r"\n", " ").replace(r"\N", " ").replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\")


def clean_birthday_title(summary: str) -> str:
    value = " ".join(_unescape(summary).strip().split())
    value = re.sub(r"(?i)^birthday\s*:\s*", "", value)
    value = re.sub(r"(?i)^день рождения\s*:\s*", "", value)
    value = re.sub(r"(?i)^(.+?)(?:'s|’s)\s+birthday$", r"\1", value)
    return value.strip(" :-")


def _properties(lines: list[str]) -> dict[str, list[tuple[str, str]]]:
    props: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        name = left.split(";", 1)[0].upper()
        props.setdefault(name, []).append((left.upper(), value))
    return props


def extract_candidate(lines: list[str]) -> ImportCandidate | None:
    props = _properties(lines)
    if not props.get("SUMMARY") or not props.get("DTSTART"):
        return None
    summary = _unescape(props["SUMMARY"][0][1])
    lowered = summary.casefold()
    marker = any(word in lowered for word in BIRTHDAY_MARKERS)
    yearly = any("FREQ=YEARLY" in value.upper() for _, value in props.get("RRULE", []))
    all_day = "VALUE=DATE" in props["DTSTART"][0][0] or "T" not in props["DTSTART"][0][1]
    if any(word in lowered for word in NEGATIVE_MARKERS) or not marker or (not yearly and not all_day):
        return None
    raw_date = props["DTSTART"][0][1].strip()
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:T.*)?", raw_date)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    date(year, month, day)
    title = clean_birthday_title(summary)
    if not title:
        return None
    # DTSTART's year is reliable only with an explicit, export-neutral marker.
    # X-BIRTHDAY-YEAR avoids inventing ages from placeholder recurrence years.
    reliable = props.get("X-BIRTHDAY-YEAR", [])
    birth_year = int(reliable[0][1]) if reliable and reliable[0][1].isdigit() else None
    if birth_year is not None:
        date(birth_year, month, day)
    return ImportCandidate(title, month, day, birth_year)


def parse_ics_birthdays(content: bytes | str) -> ParseResult:
    if isinstance(content, bytes):
        if len(content) > MAX_ICS_BYTES:
            raise ValueError("file too large")
        text = content.decode("utf-8-sig")
    else:
        text = content
    if not text.strip() or "BEGIN:VCALENDAR" not in text.upper():
        raise ValueError("invalid calendar")
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    logical: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")) and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    events, current = [], None
    for line in logical:
        if line.upper() == "BEGIN:VEVENT":
            current = []
        elif line.upper() == "END:VEVENT" and current is not None:
            events.append(current); current = None
        elif current is not None:
            current.append(line)
    candidates, skipped = [], 0
    for event in events:
        try:
            candidate = extract_candidate(event)
        except (ValueError, OverflowError):
            skipped += 1; continue
        if candidate:
            candidates.append(candidate)
        elif any(marker in " ".join(event).casefold() for marker in BIRTHDAY_MARKERS):
            skipped += 1
    if len(candidates) > MAX_IMPORT_CANDIDATES:
        skipped += len(candidates) - MAX_IMPORT_CANDIDATES
        candidates = candidates[:MAX_IMPORT_CANDIDATES]
    return ParseResult(tuple(candidates), len(events), skipped)


def split_duplicates(candidates, existing):
    keys = {(comparison_title(item["title"]), item["month"], item["day"]) for item in existing}
    new, duplicates = [], []
    for item in candidates:
        key = (comparison_title(item.title), item.month, item.day)
        (duplicates if key in keys else new).append(item)
        keys.add(key)
    return new, duplicates
