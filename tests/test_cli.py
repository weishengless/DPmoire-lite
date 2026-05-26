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
