from __future__ import annotations

import re
from dataclasses import dataclass


CONTROLLED_TAGS = frozenset({"ENCUT", "ML_RCUT1", "ML_RCUT2"})
SUPPORTED_TRUE_SPELLINGS = frozenset({"T", ".TRUE.", ".T.", "TRUE"})
_SEMICOLON_CLEANUP_MARKER = "\0DPmoire-lite-clean-semicolons\0"


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


class IncarValueConflictError(ValueError):
    pass


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
class IncarDuplicateDiagnostic:
    tag: str
    assignments: tuple[IncarAssignment, ...]
    repairable: bool
    blocking: bool


@dataclass(frozen=True)
class IncarAnalysis:
    document: IncarDocument
    duplicate_diagnostics: tuple[IncarDuplicateDiagnostic, ...]

    @property
    def repairable_warnings(self) -> tuple[IncarDuplicateDiagnostic, ...]:
        return tuple(item for item in self.duplicate_diagnostics if item.repairable)

    @property
    def blocking_conflicts(self) -> tuple[IncarDuplicateDiagnostic, ...]:
        return tuple(item for item in self.duplicate_diagnostics if item.blocking)

    def effective_value(self, tag: str) -> str | None:
        normalized_tag = tag.upper()
        assignments = self.document.assignments_for(normalized_tag)
        if not assignments:
            return None

        conflict = next(
            (
                item
                for item in self.blocking_conflicts
                if item.tag == normalized_tag
            ),
            None,
        )
        if conflict is not None:
            definitions = ", ".join(
                f"line {item.location.line}: {item.value}"
                for item in conflict.assignments
            )
            raise IncarValueConflictError(
                f"{self.document.source_name} contains conflicting definitions "
                f"for {normalized_tag}: {definitions}"
            )
        return assignments[0].value

    def is_effectively_true(self, tag: str) -> bool:
        value = self.effective_value(tag)
        return value is not None and value.upper() in SUPPORTED_TRUE_SPELLINGS

    @property
    def missing_ml_rcut_tags(self) -> tuple[str, ...]:
        if not self.is_effectively_true("ML_LMLFF"):
            return ()
        return tuple(
            tag
            for tag in ("ML_RCUT1", "ML_RCUT2")
            if not self.document.assignments_for(tag)
        )


@dataclass(frozen=True)
class IncarRenderDiagnostic:
    tag: str
    kind: str
    locations: tuple[SourceLocation, ...]


@dataclass(frozen=True)
class IncarRenderResult:
    text: str
    diagnostics: tuple[IncarRenderDiagnostic, ...]


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

    def analyze(self) -> IncarAnalysis:
        assignments_by_tag: dict[str, list[IncarAssignment]] = {}
        for assignment in self.assignments:
            assignments_by_tag.setdefault(assignment.normalized_tag, []).append(
                assignment
            )

        diagnostics = []
        for tag, assignments in assignments_by_tag.items():
            if len(assignments) < 2:
                continue
            controlled = tag in CONTROLLED_TAGS
            equal_user_values = len({item.value for item in assignments}) == 1
            repairable = controlled or equal_user_values
            diagnostics.append(
                IncarDuplicateDiagnostic(
                    tag=tag,
                    assignments=tuple(assignments),
                    repairable=repairable,
                    blocking=not repairable,
                )
            )

        return IncarAnalysis(
            document=self,
            duplicate_diagnostics=tuple(diagnostics),
        )

    def render(self, controlled_values: dict[str, object]) -> IncarRenderResult:
        analysis = self.analyze()
        if analysis.blocking_conflicts:
            analysis.effective_value(analysis.blocking_conflicts[0].tag)

        values = {
            tag.upper(): str(value)
            for tag, value in controlled_values.items()
            if tag.upper() in CONTROLLED_TAGS
        }
        statements_by_assignment = {
            id(statement.assignment): statement
            for statement in self.statements
            if statement.assignment is not None
        }
        edits: list[tuple[int, int, str]] = []
        comments_by_line_start: dict[int, list[str]] = {}
        semicolon_cleanup_line_starts: set[int] = set()
        diagnostics: list[IncarRenderDiagnostic] = []

        for duplicate in analysis.repairable_warnings:
            kind = (
                "controlled_duplicate"
                if duplicate.tag in CONTROLLED_TAGS
                else "equal_user_duplicate"
            )
            diagnostics.append(
                IncarRenderDiagnostic(
                    tag=duplicate.tag,
                    kind=kind,
                    locations=tuple(
                        assignment.location for assignment in duplicate.assignments
                    ),
                )
            )

            assignments_to_disable = duplicate.assignments
            if duplicate.tag not in CONTROLLED_TAGS:
                assignments_to_disable = duplicate.assignments[1:]

            for assignment in assignments_to_disable:
                statement = statements_by_assignment[id(assignment)]
                line_start = self.source_text.rfind("\n", 0, statement.start) + 1
                comments_by_line_start.setdefault(line_start, []).append(
                    "# DPmoire-lite disabled duplicate: "
                    f"{assignment.raw.strip()}\n"
                )
                line_end = self.source_text.find("\n", line_start)
                if line_end == -1:
                    line_end = len(self.source_text)
                if ";" in self.source_text[line_start:line_end]:
                    semicolon_cleanup_line_starts.add(line_start)
                replacement = ""
                if (
                    duplicate.tag in CONTROLLED_TAGS
                    and assignment is duplicate.assignments[-1]
                    and duplicate.tag in values
                ):
                    replacement = (
                        f"{duplicate.tag} = {values[duplicate.tag]}  "
                        "# DPmoire-lite generated value"
                    )
                edits.append((statement.start, statement.end, replacement))

        duplicate_tags = {item.tag for item in analysis.duplicate_diagnostics}
        for tag, value in values.items():
            assignments = self.assignments_for(tag)
            if len(assignments) == 1 and tag not in duplicate_tags:
                statement = statements_by_assignment[id(assignments[0])]
                replacement = f"{tag} = {value}"
                if (
                    statement.end < len(self.source_text)
                    and self.source_text[statement.end] in {"#", "!"}
                ):
                    replacement += " "
                edits.append((statement.start, statement.end, replacement))

        missing_tags = []
        if "ENCUT" in values and not self.assignments_for("ENCUT"):
            missing_tags.append("ENCUT")
        missing_tags.extend(
            tag
            for tag in analysis.missing_ml_rcut_tags
            if tag in values
        )

        for tag in missing_tags:
            diagnostics.append(
                IncarRenderDiagnostic(
                    tag=tag,
                    kind="missing_controlled_tag",
                    locations=(),
                )
            )

        insertion_edits = [
            (
                line_start,
                line_start,
                "".join(comments)
                + (
                    _SEMICOLON_CLEANUP_MARKER
                    if line_start in semicolon_cleanup_line_starts
                    else ""
                ),
            )
            for line_start, comments in comments_by_line_start.items()
        ]
        rendered = _apply_edits(self.source_text, insertion_edits + edits)
        rendered = _clean_empty_semicolon_segments(rendered)

        if missing_tags:
            if rendered and not rendered.endswith(("\n", "\r")):
                rendered += "\n"
            for tag in missing_tags:
                rendered += (
                    f"{tag} = {values[tag]}  "
                    "# DPmoire-lite generated; missing from source template\n"
                )

        return IncarRenderResult(text=rendered, diagnostics=tuple(diagnostics))


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


def _apply_edits(source_text: str, edits: list[tuple[int, int, str]]) -> str:
    output = []
    cursor = 0
    for start, end, replacement in sorted(
        edits, key=lambda item: (item[0], item[1] != item[0])
    ):
        output.append(source_text[cursor:start])
        output.append(replacement)
        cursor = max(cursor, end)
    output.append(source_text[cursor:])
    return "".join(output)


def _clean_empty_semicolon_segments(text: str) -> str:
    cleaned_lines = []
    for line in text.splitlines(keepends=True):
        if _SEMICOLON_CLEANUP_MARKER not in line:
            cleaned_lines.append(line)
            continue
        line = line.replace(_SEMICOLON_CLEANUP_MARKER, "", 1)
        line = re.sub(r"^([ \t]*);[ \t]*", r"\1", line)
        line = re.sub(r";[ \t]*;", ";", line)
        line = re.sub(r";[ \t]*(?=([#!]|\r?$))", " ", line)
        cleaned_lines.append(line)
    return "".join(cleaned_lines)
