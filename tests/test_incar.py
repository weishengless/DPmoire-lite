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


def test_render_updates_no_space_and_lowercase_controlled_tags():
    document = parse_incar("encut=400\nml_rcut1 = 6\nML_RCUT2=6\n")

    result = document.render({"ENCUT": "600", "ML_RCUT1": "7.2", "ML_RCUT2": "7.2"})

    assert result.text == "ENCUT = 600\nML_RCUT1 = 7.2\nML_RCUT2 = 7.2\n"


def test_render_preserves_unrelated_semicolon_statements():
    document = parse_incar("ENCUT=400; ISMEAR=-1; SIGMA=0.2;\n")

    result = document.render({"ENCUT": "600"})

    assert result.text == "ENCUT = 600; ISMEAR=-1; SIGMA=0.2;\n"


def test_render_disables_all_controlled_duplicates_and_emits_one_value():
    document = parse_incar("ENCUT=400  # old\nPREC=Accurate\nencut=520  # newer\n")

    result = document.render({"ENCUT": "600"})

    rendered = parse_incar(result.text)
    assert [item.value for item in rendered.assignments_for("ENCUT")] == ["600"]
    assert "# DPmoire-lite disabled duplicate: ENCUT=400" in result.text
    assert "# DPmoire-lite disabled duplicate: encut=520" in result.text
    assert "ENCUT = 600  # DPmoire-lite generated value" in result.text
    assert [item.tag for item in result.diagnostics] == ["ENCUT"]


def test_render_disables_later_equal_user_duplicates():
    document = parse_incar("ISMEAR=-1\nISMEAR = -1\nENCUT=400\n")

    result = document.render({"ENCUT": "600"})

    rendered = parse_incar(result.text)
    assert len(rendered.assignments_for("ISMEAR")) == 1
    assert "# DPmoire-lite disabled duplicate: ISMEAR = -1" in result.text
    assert [item.tag for item in result.diagnostics] == ["ISMEAR"]


def test_render_refuses_conflicting_user_duplicates():
    document = parse_incar("ISMEAR=-1\nISMEAR=0\nENCUT=400\n", source_name="input/INCAR")

    with pytest.raises(ValueError, match="ISMEAR"):
        document.render({"ENCUT": "600"})


def test_render_adds_missing_encut_with_annotation():
    result = parse_incar("PREC=Accurate\n").render({"ENCUT": "600"})

    assert result.text == (
        "PREC=Accurate\n"
        "ENCUT = 600  # DPmoire-lite generated; missing from source template\n"
    )
    assert [(item.tag, item.kind) for item in result.diagnostics] == [
        ("ENCUT", "missing_controlled_tag")
    ]


def test_render_adds_only_missing_rcut_when_mlff_is_enabled():
    document = parse_incar("ML_LMLFF=T\nENCUT=400\nML_RCUT1=6\n")

    result = document.render({"ENCUT": "600", "ML_RCUT1": "7.2", "ML_RCUT2": "7.2"})

    rendered = parse_incar(result.text)
    assert [item.value for item in rendered.assignments_for("ML_RCUT1")] == ["7.2"]
    assert [item.value for item in rendered.assignments_for("ML_RCUT2")] == ["7.2"]
    assert "ML_RCUT2 = 7.2  # DPmoire-lite generated; missing from source template" in result.text
    assert "ML_RCUT1 = 7.2  # DPmoire-lite generated; missing" not in result.text


def test_render_does_not_add_rcut_when_mlff_is_disabled():
    document = parse_incar("ML_LMLFF=F\nENCUT=400\n")

    result = document.render({"ENCUT": "600", "ML_RCUT1": "7.2", "ML_RCUT2": "7.2"})

    assert "ML_RCUT1" not in result.text
    assert "ML_RCUT2" not in result.text


def test_render_never_rewrites_langevin_gamma():
    document = parse_incar("LANGEVIN_GAMMA = 10 20 30\nENCUT=400\n")

    result = document.render({"ENCUT": "600", "LANGEVIN_GAMMA": "1 1 1"})

    assert "LANGEVIN_GAMMA = 10 20 30" in result.text
    assert "LANGEVIN_GAMMA = 1 1 1" not in result.text


def test_render_preserves_source_comments_and_statement_order():
    source = "# header\nPREC=Accurate\nENCUT=400 # cutoff note\nISMEAR=-1\n"

    result = parse_incar(source).render({"ENCUT": "600"})

    assert "# header" in result.text
    assert "# cutoff note" in result.text
    assert result.text.index("PREC=Accurate") < result.text.index("ENCUT = 600")
    assert result.text.index("ENCUT = 600") < result.text.index("ISMEAR=-1")
