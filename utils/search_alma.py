#!/usr/bin/env python3
"""Search public ALMA courses from the command line."""

import argparse
import json
import sys
from pathlib import Path
from typing import List

from alma import AlmaClient, AlmaCourse, AlmaError
from module_specs import filter_courses, parse_module_spec, read_module_specs


def _shorten(value: str, maximum: int = 30) -> str:
    if len(value) <= maximum:
        return value
    return value[: maximum - 1].rstrip() + "…"


def _lecturer(course: AlmaCourse) -> str:
    return course.responsible_lecturers or course.lecturers


def _print_table(courses: List[AlmaCourse]) -> None:
    semesters = list(dict.fromkeys(course.semester for course in courses if course.semester))
    if semesters:
        print(f"Semester: {', '.join(semesters)}\n")

    columns = (
        ("Nummer", [course.number for course in courses]),
        ("Art", [course.course_type for course in courses]),
        ("Titel", [_shorten(course.title) for course in courses]),
        ("Dozent/-in", [_lecturer(course) for course in courses]),
    )
    widths = [
        max(len(header), *(len(value) for value in values))
        for header, values in columns
    ]
    print("  ".join(header.ljust(width) for (header, _), width in zip(columns, widths)))
    print("  ".join("-" * width for width in widths))
    for index in range(len(courses)):
        print(
            "  ".join(
                values[index].ljust(width)
                for (_, values), width in zip(columns, widths)
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Veranstaltungen in der öffentlichen ALMA-Suche finden."
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Modulnummer, optional mit Typ-Suffix, z. B. INFM1110 oder INFM1110:S",
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
        help="auch Übungen und andere Veranstaltungsarten ausgeben",
    )
    parser.add_argument("--json", action="store_true", help="Treffer als JSON ausgeben")
    args = parser.parse_args()

    if args.query and args.file:
        parser.error("query und --file können nicht gemeinsam verwendet werden")
    if not args.query and not args.file:
        parser.error("ein query oder --file ist erforderlich")

    try:
        if args.file:
            queries = read_module_specs(args.file)
            if not queries:
                raise ValueError(f"{args.file} enthält keine Suchbegriffe")
        else:
            assert args.query is not None
            queries = [args.query]

        client = AlmaClient()
        courses = []
        for value in queries:
            query, requested_type = parse_module_spec(value)
            query_courses = filter_courses(
                client.search(query, semester=args.semester),
                module_number=query,
                requested_type=requested_type,
                include_all_types=args.all,
            )
            courses.extend(query_courses)
    except (AlmaError, OSError, ValueError) as error:
        parser.exit(1, f"Fehler: {error}\n")

    if args.json:
        json.dump(
            [course.to_dict() for course in courses],
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        print()
    elif courses:
        _print_table(courses)
    else:
        print("Keine Treffer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
