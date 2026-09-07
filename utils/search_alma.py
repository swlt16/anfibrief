#!/usr/bin/env python3
"""Search public ALMA courses from the command line."""

import argparse
import json
import sys
from pathlib import Path
from typing import List

from alma import AlmaClient, AlmaCourse, AlmaError


def _shorten(value: str, maximum: int = 30) -> str:
    if len(value) <= maximum:
        return value
    return value[: maximum - 1].rstrip() + "…"


def _lecturer(course: AlmaCourse) -> str:
    return course.responsible_lecturers or course.lecturers


def _read_queries(path: Path) -> List[str]:
    queries = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        query = line.partition("#")[0].strip()
        if query:
            queries.append(query)
    return queries


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
        help="z. B. INFM1110, ein Titel oder ein Name",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Datei mit einem Suchbegriff pro Zeile",
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
            queries = _read_queries(args.file)
            if not queries:
                raise ValueError(f"{args.file} enthält keine Suchbegriffe")
        else:
            assert args.query is not None
            queries = [args.query]

        client = AlmaClient()
        courses = [
            course
            for query in queries
            for course in client.search(query, semester=args.semester)
        ]
        if not args.all:
            courses = [course for course in courses if course.course_type == "Vorlesung"]
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
