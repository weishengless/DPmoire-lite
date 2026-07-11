from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLocation:
    line: int
    column: int


class IncarParseError(ValueError):
    def __init__(self, message: str, *, source_name: str, location: SourceLocation):
        self.source_name = source_name
        self.location = location
        super().__init__(
            f"{source_name}: {message} at line {location.line}, column {location.column}"
        )


@dataclass(frozen=True)
class IncarAssignment:
    tag: str
    normalized_tag: str
    value: str
    raw: str
    location: SourceLocation


@dataclass(frozen=True)
class IncarStatement:
    raw: str
    start: int
    end: int
    location: SourceLocation
    assignment: IncarAssignment | None


@dataclass(frozen=True)
class IncarDocument:
    source_text: str
    source_name: str
    statements: tuple[IncarStatement, ...]

    @property
    def assignments(self) -> tuple[IncarAssignment, ...]:
        return tuple(
            statement.assignment
            for statement in self.statements
            if statement.assignment is not None
        )

    def assignments_for(self, tag: str) -> list[IncarAssignment]:
        normalized_tag = tag.upper()
        return [
            assignment
            for assignment in self.assignments
            if assignment.normalized_tag == normalized_tag
        ]


def parse_incar(source_text: str, *, source_name: str = "<memory>") -> IncarDocument:
    statements: list[IncarStatement] = []
    statement_start = 0
    quote: str | None = None
    quote_start: int | None = None
    in_comment = False
    index = 0

    while index < len(source_text):
        char = source_text[index]

        if in_comment:
            if char == "\n":
                in_comment = False
                statement_start = index + 1
            index += 1
            continue

        if char in {'"', "'"} and not _is_escaped(source_text, index):
            if quote is None:
                quote = char
                quote_start = index
            elif quote == char:
                quote = None
                quote_start = None
            index += 1
            continue

        if quote is None and char in {"#", "!"}:
            _append_statement(statements, source_text, statement_start, index)
            in_comment = True
            index += 1
            continue

        if quote is None and char == ";":
            _append_statement(statements, source_text, statement_start, index)
            statement_start = index + 1
            index += 1
            continue

        if quote is None and char == "\n":
            if not source_text[statement_start:index].rstrip().endswith("\\"):
                _append_statement(statements, source_text, statement_start, index)
                statement_start = index + 1

        index += 1

    if quote is not None and quote_start is not None:
        raise IncarParseError(
            "unclosed quote",
            source_name=source_name,
            location=_location(source_text, quote_start),
        )

    if not in_comment:
        _append_statement(statements, source_text, statement_start, len(source_text))

    return IncarDocument(
        source_text=source_text,
        source_name=source_name,
        statements=tuple(statements),
    )


def _append_statement(
    statements: list[IncarStatement], source_text: str, start: int, end: int
) -> None:
    source_raw = source_text[start:end]
    stripped = source_raw.strip()
    if not stripped:
        return

    leading = len(source_raw) - len(source_raw.lstrip())
    content_start = start + leading
    location = _location(source_text, content_start)
    assignment = _parse_assignment(stripped, location)
    statements.append(
        IncarStatement(
            raw=source_raw,
            start=start,
            end=end,
            location=location,
            assignment=assignment,
        )
    )


def _parse_assignment(raw: str, location: SourceLocation) -> IncarAssignment | None:
    equals_index = _find_unquoted_equals(raw)
    if equals_index is None:
        return None

    tag = raw[:equals_index].strip()
    if not tag:
        return None
    value = re.sub(r"\\\r?\n[ \t]*", " ", raw[equals_index + 1 :]).strip()
    return IncarAssignment(
        tag=tag,
        normalized_tag=tag.upper(),
        value=value,
        raw=raw,
        location=location,
    )


def _find_unquoted_equals(text: str) -> int | None:
    quote: str | None = None
    for index, char in enumerate(text):
        if char in {'"', "'"} and not _is_escaped(text, index):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        elif char == "=" and quote is None:
            return index
    return None


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _location(source_text: str, offset: int) -> SourceLocation:
    line = source_text.count("\n", 0, offset) + 1
    last_newline = source_text.rfind("\n", 0, offset)
    return SourceLocation(line=line, column=offset - last_newline)
