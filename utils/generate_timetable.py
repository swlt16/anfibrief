#!/usr/bin/env python3
"""Generate a weekly terminal timetable from ALMA module numbers."""

import argparse
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import List

from alma import AlmaClient, AlmaCourse, AlmaError, AlmaMeeting
from module_specs import filter_courses, parse_module_spec, read_module_specs


DAY_ORDER = {
    "Mo": 0,
    "Montag": 0,
    "Di": 1,
    "Dienstag": 1,
    "Mi": 2,
    "Mittwoch": 2,
    "Do": 3,
    "Donnerstag": 3,
    "Fr": 4,
    "Freitag": 4,
    "Sa": 5,
    "Samstag": 5,
    "So": 6,
    "Sonntag": 6,
}

WEEKDAYS = (
    ("Mo", "Montag"),
    ("Di", "Dienstag"),
    ("Mi", "Mittwoch"),
    ("Do", "Donnerstag"),
    ("Fr", "Freitag"),
    ("Sa", "Samstag"),
    ("So", "Sonntag"),
)

CELL_WIDTH = 22


@dataclass(frozen=True)
class TimetableEntry:
    course: AlmaCourse
    meeting: AlmaMeeting


def _is_single_event(meeting: AlmaMeeting) -> bool:
    return meeting.rhythm.casefold() == "einzeltermin"


def _minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _weekday_key(value: str) -> str:
    day_number = DAY_ORDER.get(value)
    return WEEKDAYS[day_number][0] if day_number is not None else value


def _entry_lines(entry: TimetableEntry) -> List[str]:
    return textwrap.wrap(
        entry.course.title,
        width=CELL_WIDTH,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


def _print_row(cells: List[List[str]], left: str = "") -> None:
    height = max(1, *(len(cell) for cell in cells))
    for line_number in range(height):
        label = left if line_number == 0 else ""
        rendered_cells = [
            (cell[line_number] if line_number < len(cell) else "").ljust(CELL_WIDTH)
            for cell in cells
        ]
        print(f"{label:<11} | " + " | ".join(rendered_cells))


def _print_timetable(entries: List[TimetableEntry]) -> None:
    semesters = list(
        dict.fromkeys(entry.course.semester for entry in entries if entry.course.semester)
    )
    if semesters:
        print(f"Semester: {', '.join(semesters)}\n")

    entries.sort(
        key=lambda entry: (
            DAY_ORDER.get(entry.meeting.weekday, 99),
            entry.meeting.start_time,
            entry.course.number,
            entry.course.course_type,
        )
    )

    present_days = {_weekday_key(entry.meeting.weekday) for entry in entries}
    days = list(WEEKDAYS[:5])
    days.extend(day for day in WEEKDAYS[5:] if day[0] in present_days)

    starts = [_minutes(entry.meeting.start_time) for entry in entries]
    ends = [_minutes(entry.meeting.end_time) for entry in entries]
    first_block = min(8 * 60, min(starts) // 120 * 120)
    last_block = max(20 * 60, ((max(ends) + 119) // 120) * 120)

    _print_row([[day_name] for _, day_name in days], left="Zeit")
    separator = "-" * 11 + "-+-" + "-+-".join("-" * CELL_WIDTH for _ in days)
    print(separator)

    for block_start in range(first_block, last_block, 120):
        block_end = block_start + 120
        cells: List[List[str]] = []
        for day_key, _ in days:
            cell_entries = [
                entry
                for entry in entries
                if _weekday_key(entry.meeting.weekday) == day_key
                and _minutes(entry.meeting.start_time) < block_end
                and _minutes(entry.meeting.end_time) > block_start
            ]
            cell_lines: List[str] = []
            for index, entry in enumerate(cell_entries):
                if index:
                    cell_lines.append("·" * CELL_WIDTH)
                cell_lines.extend(_entry_lines(entry))
            cells.append(cell_lines)
        label = f"{block_start // 60:02}:00–{block_end // 60:02}:00"
        _print_row(cells, left=label)
        if block_end < last_block:
            print(separator)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wochenstundenplan aus öffentlichen ALMA-Terminen erzeugen."
    )
    parser.add_argument(
        "modules",
        nargs="*",
        help="Modulnummern mit optionalem Typ-Suffix, z. B. INFM1110:V",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Datei mit einer Modulnummer pro Zeile",
    )
    parser.add_argument(
        "--semester",
        help="z. B. 'Wintersemester 2026' (Standard: aktuell in ALMA ausgewählt)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="bei Modulnummern ohne Suffix alle Veranstaltungsarten verwenden",
    )
    parser.add_argument(
        "--include-single-events",
        action="store_true",
        help="auch Einzeltermine wie Klausuren anzeigen",
    )
    args = parser.parse_args()

    try:
        specs = list(args.modules)
        if args.file:
            specs.extend(read_module_specs(args.file))
        if not specs:
            parser.error("mindestens eine Modulnummer oder --file ist erforderlich")

        client = AlmaClient()
        entries: List[TimetableEntry] = []
        for spec in specs:
            module_number, requested_type = parse_module_spec(spec)
            courses = filter_courses(
                client.search(module_number, semester=args.semester),
                module_number=module_number,
                requested_type=requested_type,
                include_all_types=args.all,
            )
            if not courses:
                print(f"Warnung: keine passende Veranstaltung für {spec}", file=sys.stderr)
                continue
            for course in courses:
                meetings = client.get_meetings(course)
                recurring_meetings = [
                    meeting
                    for meeting in meetings
                    if args.include_single_events or not _is_single_event(meeting)
                ]
                if not recurring_meetings:
                    print(
                        f"Warnung: keine regelmäßigen Termine für {spec}",
                        file=sys.stderr,
                    )
                entries.extend(
                    TimetableEntry(course=course, meeting=meeting)
                    for meeting in recurring_meetings
                )
    except (AlmaError, OSError, ValueError) as error:
        parser.exit(1, f"Fehler: {error}\n")

    if entries:
        _print_timetable(entries)
    else:
        print("Keine Termine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
