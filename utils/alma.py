"""Small, dependency-free client for ALMA's public course search.

ALMA/HISinOne does not expose the search shown in the browser as a simple
JSON endpoint.  It is a stateful JSF/Webflow form whose field names and
tokens may change between requests.  This module therefore reads the form
before every search instead of copying the transient values from a HAR file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_BASE_URL = "https://alma.uni-tuebingen.de"
FLOW_PATH = (
    "/alma/pages/startFlow.xhtml?_flowId=searchCourseNonStaff-flow"
    "&navigationPosition=studiesOffered%2CsearchCourses&recordRequest=true"
)


class AlmaError(RuntimeError):
    """Raised when ALMA cannot be reached or its response cannot be parsed."""


@dataclass(frozen=True)
class AlmaCourse:
    """One course returned by ALMA's public course search."""

    number: str
    title: str
    course_type: str
    responsible_lecturers: str
    lecturers: str
    organisation: str
    detail_url: str
    semester: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class _SearchForm:
    action: str
    values: Dict[str, str]
    query_field: str
    semester_field: Optional[str]
    semesters: List[Tuple[str, str]]
    search_button: str


class _SearchFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_form = False
        self.form: Optional[_SearchForm] = None
        self._action = ""
        self._values: Dict[str, str] = {}
        self._query_field = ""
        self._semester_field: Optional[str] = None
        self._semesters: List[Tuple[str, str]] = []
        self._search_button = ""
        self._in_semester_select = False
        self._option_value: Optional[str] = None
        self._option_selected = False
        self._option_text: List[str] = []
        self._selected_semester: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        attributes = {name: value or "" for name, value in attrs}

        if tag == "form" and attributes.get("id") == "genericSearchMask":
            self.in_form = True
            self._action = attributes.get("action", "")
            return
        if not self.in_form:
            return

        if tag == "input":
            name = attributes.get("name", "")
            input_type = attributes.get("type", "text").lower()
            if not name:
                return
            if input_type == "hidden":
                self._values[name] = attributes.get("value", "")
            elif input_type == "text":
                if "autofocus-behavior" in attributes.get("class", ""):
                    self._query_field = name
                elif name.endswith(("termSelect_focus", "termSelect_filter")):
                    self._values[name] = ""
        elif tag == "button":
            name = attributes.get("name", "")
            if name.endswith(":buttonsBottom:search"):
                self._search_button = name
        elif tag == "select":
            name = attributes.get("name", "")
            if name.endswith("termSelect_input"):
                self._in_semester_select = True
                self._semester_field = name
        elif tag == "option" and self._in_semester_select:
            self._option_value = attributes.get("value", "")
            self._option_selected = "selected" in attributes
            self._option_text = []

    def handle_data(self, data: str) -> None:
        if self._option_value is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._option_value is not None:
            label = " ".join("".join(self._option_text).split())
            self._semesters.append((label, self._option_value))
            if self._option_selected:
                self._selected_semester = self._option_value
            self._option_value = None
            self._option_selected = False
            self._option_text = []
        elif tag == "select" and self._in_semester_select:
            self._in_semester_select = False
        elif tag == "form" and self.in_form:
            self.in_form = False
            if self._semester_field and self._semesters:
                selected = self._selected_semester or self._semesters[0][1]
                self._values[self._semester_field] = selected
            if self._action and self._query_field and self._search_button:
                self.form = _SearchForm(
                    action=self._action,
                    values=self._values,
                    query_field=self._query_field,
                    semester_field=self._semester_field,
                    semesters=self._semesters,
                    search_button=self._search_button,
                )


@dataclass
class _Cell:
    tag: str
    text: List[str]
    links: List[str]


class _ResultParser(HTMLParser):
    def __init__(self, base_url: str, semester: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.semester = semester
        self.courses: List[AlmaCourse] = []
        self._table_depth = 0
        self._in_result_table = False
        self._row: Optional[List[_Cell]] = None
        self._cell: Optional[_Cell] = None
        self._columns: Optional[Dict[str, int]] = None

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "table":
            self._table_depth += 1
            classes = attributes.get("class", "").split()
            if not self._in_result_table and "tableWithBorder" in classes:
                self._in_result_table = True
                self._table_depth = 1
                self._columns = None
            return
        if not self._in_result_table or self._table_depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("th", "td") and self._row is not None:
            self._cell = _Cell(tag=tag, text=[], links=[])
        elif tag == "a" and self._cell is not None:
            href = attributes.get("href")
            if href:
                self._cell.links.append(href)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._in_result_table and self._table_depth == 1:
                self._in_result_table = False
                self._row = None
                self._cell = None
            self._table_depth = max(0, self._table_depth - 1)
            return
        if not self._in_result_table or self._table_depth != 1:
            return
        if tag in ("th", "td") and self._cell is not None and self._row is not None:
            self._cell.text = [" ".join("".join(self._cell.text).split())]
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._consume_row(self._row)
            self._row = None

    def _consume_row(self, row: List[_Cell]) -> None:
        texts = [cell.text[0] if cell.text else "" for cell in row]
        if row and all(cell.tag == "th" for cell in row):
            headers = {text: index for index, text in enumerate(texts)}
            if "Nummer" in headers and "Titel der Veranstaltung" in headers:
                self._columns = headers
            return
        if not self._columns:
            return

        def value(header: str) -> str:
            index = self._columns.get(header)
            return texts[index] if index is not None and index < len(texts) else ""

        number = value("Nummer")
        title = value("Titel der Veranstaltung")
        if not number and not title:
            return
        detail_path = next(
            (
                link
                for cell in row
                for link in cell.links
                if "_flowId=detailView-flow" in link
            ),
            "",
        )
        self.courses.append(
            AlmaCourse(
                number=number,
                title=title,
                course_type=value("Veranstaltungsart"),
                responsible_lecturers=value("Dozent/-in (verantwortlich)"),
                lecturers=value("Dozent/-in (durchführend)"),
                organisation=value("Organisationseinheit"),
                detail_url=urljoin(self.base_url, detail_path) if detail_path else "",
                semester=self.semester,
            )
        )


class AlmaClient:
    """Client for the anonymous course search at alma.uni-tuebingen.de."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._session_initialized = False

    def search(self, query: str, semester: Optional[str] = None) -> List[AlmaCourse]:
        """Search courses by number, title, or lecturer.

        ``semester`` may be an ALMA value such as ``eq|31|2026`` or a displayed
        label such as ``Wintersemester 2026``.  If omitted, ALMA's selected
        semester is used.
        """
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")

        # Establish an anonymous JSESSIONID before starting the Webflow.  Going
        # directly to the flow without it currently causes a redirect loop.
        if not self._session_initialized:
            self._get(urljoin(self.base_url, "/alma/"))
            self._session_initialized = True
        form_html = self._get(urljoin(self.base_url, FLOW_PATH))
        parser = _SearchFormParser()
        parser.feed(form_html)
        form = parser.form
        if form is None:
            raise AlmaError("ALMA search form could not be found")

        values = dict(form.values)
        values[form.query_field] = query
        values["activePageElementId"] = form.query_field
        values[form.search_button] = " "
        selected_semester = (
            values.get(form.semester_field, "") if form.semester_field else ""
        )
        if semester is not None:
            if form.semester_field is None:
                raise AlmaError("ALMA search form contains no semester field")
            selected_semester = self._resolve_semester(semester, form.semesters)
            values[form.semester_field] = selected_semester

        semester_label = next(
            (
                label
                for label, value in form.semesters
                if value == selected_semester
            ),
            selected_semester,
        )

        result_html = self._post(urljoin(self.base_url, form.action), values)
        result_parser = _ResultParser(self.base_url, semester=semester_label)
        result_parser.feed(result_html)
        return result_parser.courses

    @staticmethod
    def _resolve_semester(semester: str, choices: Sequence[Tuple[str, str]]) -> str:
        requested = semester.strip().casefold()
        for label, value in choices:
            if requested in (label.casefold(), value.casefold()):
                return value
        available = ", ".join(label for label, _ in choices)
        raise ValueError(f"unknown semester {semester!r}; available: {available}")

    def _get(self, url: str) -> str:
        return self._request(Request(url, headers=self._headers()))

    def _post(self, url: str, values: Dict[str, str]) -> str:
        data = urlencode(values).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self._request(Request(url, data=data, headers=headers, method="POST"))

    def _request(self, request: Request) -> str:
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as error:
            raise AlmaError(f"ALMA returned HTTP {error.code} for {request.full_url}") from error
        except URLError as error:
            raise AlmaError(f"could not reach ALMA: {error.reason}") from error

    @staticmethod
    def _headers() -> Dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "User-Agent": "anfibrief-alma-client/1.0",
        }
