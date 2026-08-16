"""The agent process.

Three jobs in one supervisor: drain the journal, poll the sensors, bridge
the VSDC. They run as threads rather than separate services because a
pharmacy is not going to operate three daemons, and because losing one
silently is worse than the coupling.

Configuration is a TOML file beside the journal. Environment variables
override it, so a site can be commissioned by editing one file and a
fleet can be configured by its deployment.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import tomllib
from pathlib import Path

from medix_agent.journal import Journal
from medix_agent.sensors import FileDriver, MockDriver, Monitor
from medix_agent.sync import Credentials, SyncClient
from medix_agent import vsdc

log = logging.getLogger("medix_agent")

DEFAULT_CONFIG = Path.home() / ".medix" / "agent.toml"
DEFAULT_JOURNAL = Path.home() / ".medix" / "journal.sqlite3"


def load_config(path: Path) -> dict:
    """File first, environment second.

    A missing file is not fatal: a container-deployed agent may be
    configured entirely by environment, and refusing to start without a
    file it does not need would be an obstacle rather than a check.
    """
    config: dict = {}
    if path.exists():
        config = tomllib.loads(path.read_text(encoding="utf-8"))

    server = config.setdefault("server", {})
    server.setdefault("url", os.environ.get("MEDIX_URL", "http://localhost:8000"))
    server.setdefault("device", os.environ.get("MEDIX_DEVICE", ""))
    server.setdefault("username", os.environ.get("MEDIX_USERNAME", ""))
    server.setdefault("password", os.environ.get("MEDIX_PASSWORD", ""))

    config.setdefault("journal", {}).setdefault(
        "path", os.environ.get("MEDIX_JOURNAL", str(DEFAULT_JOURNAL))
    )
    config.setdefault("sensors", {}).setdefault(
        "driver", os.environ.get("MEDIX_SENSOR_DRIVER", "none")
    )
    config.setdefault("vsdc", {}).setdefault(
        "mode", os.environ.get("MEDIX_VSDC_MODE", "none")
    )
    return config


def build_driver(settings: dict):
    kind = settings.get("driver", "none")
    if kind == "file":
        return FileDriver(settings["path"])
    if kind == "mock":
        return MockDriver([(settings.get("code", "FRIDGE-1"), 4.0)])
    return None


def run(config: dict) -> int:
    server = config["server"]
    if not server["device"]:
        # Refused rather than defaulted. An agent with no device code
        # would be rejected by the server on every envelope, and the
        # useful message is here rather than in a retry loop.
        log.error("No device code configured. Set MEDIX_DEVICE or server.device.")
        return 2

    journal = Journal(config["journal"]["path"])
    client = SyncClient(
        Credentials(
            base_url=server["url"],
            device_code=server["device"],
            username=server["username"],
            password=server["password"],
        ),
        journal,
    )

    stopping = threading.Event()

    def shutdown(signum, frame):
        # The journal is durable, so stopping mid-drain loses nothing —
        # whatever did not send is still pending on the next start.
        log.info("Stopping.")
        stopping.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    threads = [
        threading.Thread(target=client.run_forever, name="sync", daemon=True),
    ]

    driver = build_driver(config["sensors"])
    if driver is not None:
        monitor = Monitor(driver, client, journal)
        threads.append(
            threading.Thread(
                target=lambda: monitor.run_forever(
                    interval=int(config["sensors"].get("interval", 300))
                ),
                name="sensors",
                daemon=True,
            )
        )
    else:
        log.info("No sensor driver configured; temperature monitoring is off.")

    bridge = vsdc.build(config["vsdc"])
    log.info("VSDC transport: %s", type(bridge).__name__)

    for thread in threads:
        thread.start()
    log.info(
        "Agent running for %s against %s", server["device"], server["url"]
    )

    while not stopping.is_set():
        stopping.wait(1)
        for thread in threads:
            if not thread.is_alive():
                # A dead worker is the failure this supervisor exists to
                # notice. Exiting lets the service manager restart the
                # whole process, which is more reliable than trying to
                # resurrect one thread in place.
                log.error("Worker %s died; exiting for restart.", thread.name)
                return 1

    counts = journal.counts()
    log.info("Stopped. Journal: %s", counts)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="medix-agent")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print the journal state and exit.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = load_config(args.config)

    if args.status:
        journal = Journal(config["journal"]["path"])
        for state, count in sorted(journal.counts().items()):
            print(f"{state:10s} {count}")
        return 0

    return run(config)


if __name__ == "__main__":
    sys.exit(main())
