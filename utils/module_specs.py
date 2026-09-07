"""Shared parsing and filtering for ALMA command line tools."""

from pathlib import Path
from typing import List, Optional, Tuple

from alma import AlmaCourse


COURSE_TYPE_CODES = {
    "V": "Vorlesung",
    "S": "Seminar",
    "Ü": "Übung",
    "U": "Übung",
    "P": "Praktikum",
    "T": "Tutorium",
    "K": "Kolloquium",
}


def read_module_specs(path: Path) -> List[str]:
    specs = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        spec = line.partition("#")[0].strip()
        if spec:
            specs.append(spec)
    return specs


def parse_module_spec(value: str) -> Tuple[str, Optional[str]]:
    module_number, separator, type_code = value.rpartition(":")
    if not separator:
        return value.strip(), None

    type_code = type_code.strip().upper()
    if type_code in COURSE_TYPE_CODES:
        module_number = module_number.strip()
        if not module_number:
            raise ValueError("vor dem Veranstaltungstyp fehlt die Modulnummer")
        return module_number, COURSE_TYPE_CODES[type_code]

    if len(type_code) <= 2:
        valid_codes = ", ".join(COURSE_TYPE_CODES)
        raise ValueError(
            f"unbekannter Veranstaltungstyp {type_code!r}; erlaubt: {valid_codes}"
        )
    return value.strip(), None


def filter_courses(
    courses: List[AlmaCourse],
    module_number: str,
    requested_type: Optional[str],
    include_all_types: bool = False,
) -> List[AlmaCourse]:
    course_type = requested_type
    if course_type is None and not include_all_types:
        course_type = "Vorlesung"
    return [
        course
        for course in courses
        if course.number == module_number
        and (course_type is None or course.course_type == course_type)
    ]

