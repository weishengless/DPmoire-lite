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


def test_controlled_duplicates_are_repairable_even_when_values_differ():
    analysis = parse_incar("ENCUT=400\nencut = 520\n").analyze()

    duplicate = analysis.duplicate_diagnostics[0]
    assert duplicate.tag == "ENCUT"
    assert duplicate.repairable is True
    assert duplicate.blocking is False
    assert [item.value for item in duplicate.assignments] == ["400", "520"]


def test_user_duplicates_with_equal_normalized_text_are_repairable():
    analysis = parse_incar("ISMEAR=-1\nismear = -1  \n").analyze()

    duplicate = analysis.duplicate_diagnostics[0]
    assert duplicate.tag == "ISMEAR"
    assert duplicate.repairable is True
    assert duplicate.blocking is False


def test_user_duplicates_with_different_text_are_blocking():
    analysis = parse_incar("SIGMA=0\nsigma = 0.0\n").analyze()

    conflict = analysis.blocking_conflicts[0]
    assert conflict.tag == "SIGMA"
    assert conflict.repairable is False
    assert [item.location.line for item in conflict.assignments] == [1, 2]


def test_comment_text_is_not_a_duplicate_definition():
    analysis = parse_incar("ENCUT=400 # ENCUT=520\n! ENCUT=600\n").analyze()

    assert analysis.duplicate_diagnostics == ()
    assert analysis.effective_value("ENCUT") == "400"


def test_duplicate_detection_spans_semicolon_statements():
    analysis = parse_incar("ENCUT=400; ISMEAR=-1; encut=520\n").analyze()

    duplicate = analysis.duplicate_diagnostics[0]
    assert duplicate.tag == "ENCUT"
    assert [item.location.column for item in duplicate.assignments] == [1, 23]
    assert analysis.effective_value("ISMEAR") == "-1"


@pytest.mark.parametrize("spelling", ["T", ".TRUE.", ".T.", "TRUE"])
def test_effective_luse_vdw_accepts_supported_true_spellings(spelling):
    analysis = parse_incar(f"luse_vdw = {spelling}\n").analyze()

    assert analysis.is_effectively_true("LUSE_VDW") is True


def test_conflicting_luse_vdw_is_blocking():
    analysis = parse_incar("LUSE_VDW=T\nluse_vdw = F\n").analyze()

    assert [item.tag for item in analysis.blocking_conflicts] == ["LUSE_VDW"]
    with pytest.raises(ValueError, match="LUSE_VDW"):
        analysis.effective_value("LUSE_VDW")


@pytest.mark.parametrize(
    ("ml_lmlff", "expected_missing"),
    [("T", ("ML_RCUT1", "ML_RCUT2")), ("F", ())],
)
def test_ml_lmlff_effective_value_controls_missing_rcut_policy(
    ml_lmlff, expected_missing
):
    analysis = parse_incar(f"ML_LMLFF={ml_lmlff}\n").analyze()

    assert analysis.missing_ml_rcut_tags == expected_missing
