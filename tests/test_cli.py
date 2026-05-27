from dpmoire_lite.cli import main


def test_main_help_exits_cleanly(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    captured = capsys.readouterr()
    assert "DPmoireLite" in captured.out
    assert "build" in captured.out
    assert "collect" in captured.out
    assert "init-example" in captured.out


def test_collect_requires_stage(capsys):
    try:
        main(["collect", "config.yaml"])
    except SystemExit as exc:
        assert exc.code != 0
    captured = capsys.readouterr()
    assert "--stage" in captured.err


def test_init_example_copies_bundled_template(tmp_path):
    target = tmp_path / "my_case"

    assert main(["init-example", str(target)]) == 0
    assert (target / "config.yaml").is_file()
    assert (target / "input" / "rlx_INCAR").is_file()
    assert (target / "scripts" / "DFT_script.sh").is_file()
