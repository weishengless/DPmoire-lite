import pytest

from dpmoire_lite.incar import IncarParseError, parse_incar


@pytest.mark.parametrize(
    ("source", "source_tag", "value"),
    [
        ("ENCUT=400\n", "ENCUT", "400"),
        ("encut = 450\n", "encut", "450"),
        ("  EnCuT   =   520  \n", "EnCuT", "520"),
    ],
)
def test_parse_incar_accepts_assignment_spacing_and_case(source, source_tag, value):
    document = parse_incar(source)

    assignment = document.assignments_for("ENCUT")[0]
    assert assignment.tag == source_tag
    assert assignment.normalized_tag == "ENCUT"
    assert assignment.value == value


def test_parse_incar_splits_semicolon_statements():
    document = parse_incar("encut = 450; ISMEAR=-1\n")

    assert [item.normalized_tag for item in document.assignments] == ["ENCUT", "ISMEAR"]
    assert [item.value for item in document.assignments] == ["450", "-1"]


def test_parse_incar_ignores_hash_and_bang_comments():
    source = "ENCUT=400 # ISMEAR=-1\n! SIGMA=0.2\nLREAL=Auto; # PREC=Low\n"
    document = parse_incar(source)

    assert [item.normalized_tag for item in document.assignments] == ["ENCUT", "LREAL"]
    assert document.assignments_for("ISMEAR") == []
    assert document.assignments_for("SIGMA") == []
    assert document.source_text == source


def test_parse_incar_preserves_quoted_special_characters():
    document = parse_incar('SYSTEM = "a;#b!c"; ENCUT=400\n')

    system = document.assignments_for("SYSTEM")[0]
    assert system.value == '"a;#b!c"'
    assert system.raw == 'SYSTEM = "a;#b!c"'
    assert [item.normalized_tag for item in document.assignments] == ["SYSTEM", "ENCUT"]


def test_parse_incar_handles_backslash_continuation():
    source = "LONG_VALUE = one two \\\n             three\nENCUT=400\n"
    document = parse_incar(source)

    long_value = document.assignments_for("LONG_VALUE")[0]
    assert long_value.value.split() == ["one", "two", "three"]
    assert "\\\n" in long_value.raw
    assert [item.normalized_tag for item in document.assignments] == ["LONG_VALUE", "ENCUT"]


def test_parse_incar_records_statement_locations():
    document = parse_incar("  ENCUT=400; ISMEAR=-1\nSIGMA = 0.2\n")

    encut, ismear, sigma = document.assignments
    assert (encut.location.line, encut.location.column) == (1, 3)
    assert (ismear.location.line, ismear.location.column) == (1, 14)
    assert (sigma.location.line, sigma.location.column) == (2, 1)


def test_parse_incar_reports_unsafe_unclosed_quote_with_location():
    with pytest.raises(IncarParseError) as exc_info:
        parse_incar("ENCUT=400\nSYSTEM = \"unfinished\n", source_name="input/INCAR")

    error = exc_info.value
    assert (error.location.line, error.location.column) == (2, 10)
    assert "input/INCAR" in str(error)
    assert "unclosed quote" in str(error).lower()
    assert "line 2" in str(error).lower()
