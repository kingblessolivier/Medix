"""Configuration and the supervisor.

The one behaviour worth insisting on here: an agent with no device code
refuses to start. Defaulting it would produce a process that looks
healthy while the server rejects every envelope it sends, and the useful
message belongs at startup rather than buried in a retry loop.
"""

from pathlib import Path

from medix_agent import main
from medix_agent.sensors import FileDriver, MockDriver


class TestConfig:
    def test_a_missing_file_is_not_fatal(self, tmp_path, monkeypatch):
        """A container agent may be configured entirely by environment."""
        monkeypatch.setenv("MEDIX_DEVICE", "TILL-1")
        config = main.load_config(tmp_path / "absent.toml")
        assert config["server"]["device"] == "TILL-1"

    def test_the_file_wins_over_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEDIX_DEVICE", "FROM-ENV")
        path = tmp_path / "agent.toml"
        path.write_text('[server]\ndevice = "FROM-FILE"\n', encoding="utf-8")

        assert main.load_config(path)["server"]["device"] == "FROM-FILE"

    def test_the_environment_fills_what_the_file_omits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEDIX_URL", "https://medix.rw")
        path = tmp_path / "agent.toml"
        path.write_text('[server]\ndevice = "TILL-1"\n', encoding="utf-8")

        config = main.load_config(path)
        assert config["server"]["url"] == "https://medix.rw"
        assert config["server"]["device"] == "TILL-1"

    def test_defaults_are_safe(self, tmp_path, monkeypatch):
        for name in ("MEDIX_URL", "MEDIX_DEVICE", "MEDIX_SENSOR_DRIVER", "MEDIX_VSDC_MODE"):
            monkeypatch.delenv(name, raising=False)
        config = main.load_config(tmp_path / "absent.toml")

        assert config["server"]["device"] == ""
        assert config["sensors"]["driver"] == "none"
        assert config["vsdc"]["mode"] == "none"


class TestStartup:
    def test_no_device_code_refuses_to_start(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEDIX_DEVICE", raising=False)
        config = main.load_config(tmp_path / "absent.toml")
        config["journal"]["path"] = str(tmp_path / "journal.sqlite3")

        assert main.run(config) == 2

    def test_no_driver_means_no_monitor(self):
        """Temperature monitoring off is a state, not a crash."""
        assert main.build_driver({"driver": "none"}) is None
        assert main.build_driver({}) is None

    def test_a_file_driver_is_built_from_its_path(self, tmp_path):
        driver = main.build_driver({"driver": "file", "path": str(tmp_path / "s.json")})
        assert isinstance(driver, FileDriver)

    def test_a_mock_driver_lets_a_site_be_commissioned_early(self):
        assert isinstance(main.build_driver({"driver": "mock"}), MockDriver)


class TestStatus:
    def test_status_prints_the_journal_and_exits(self, tmp_path, capsys, monkeypatch):
        from medix_agent.journal import Journal

        path = tmp_path / "journal.sqlite3"
        Journal(path).record("sale", {})
        monkeypatch.setenv("MEDIX_JOURNAL", str(path))

        code = main.main(["--status", "--config", str(tmp_path / "absent.toml")])

        assert code == 0
        assert "pending" in capsys.readouterr().out


class TestDefaults:
    def test_the_journal_lives_beside_the_config(self):
        assert main.DEFAULT_JOURNAL.parent == main.DEFAULT_CONFIG.parent
        assert isinstance(main.DEFAULT_JOURNAL, Path)
