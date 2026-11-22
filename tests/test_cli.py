from model_radar.cli import main


def test_init_db_and_help(tmp_path):
    db = tmp_path / "radar.sqlite"
    config = tmp_path / "config.yaml"
    config.write_text("timezone: Asia/Shanghai\nhuggingface:\n  enabled: false\nproviders: {}\nrss: []\n", encoding="utf-8")
    assert main(["init-db", "--config", str(config), "--db", str(db)]) == 0
    assert db.exists()
    assert main(["init-db", "--config", str(config), "--db", str(db)]) == 0


def test_unknown_command():
    try:
        main(["nope"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected argparse to exit")
