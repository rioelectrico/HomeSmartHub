"""Command-line entry point for the Portero device simulator."""

import argparse
import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from portero_simulator.client import DeviceSimulator
from portero_simulator.config import SimulatorSettings


def configure_logging(*, verbose: bool) -> None:
    """Enable simulator diagnostics without enabling credential-bearing frame logs."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("portero_simulator").setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    """Build the public CLI without accepting secrets on the command line."""

    parser = argparse.ArgumentParser(
        prog="portero-device-simulator",
        description="Simulate one Portero protocol V1 device.",
        epilog="DEVICE_SECRET is required in the environment and is never printed.",
    )
    parser.add_argument("--backend-url")
    parser.add_argument("--device-id")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--firmware-version")
    parser.add_argument("--hardware-model")
    parser.add_argument("--ethernet", choices=("online", "offline", "error"))
    parser.add_argument("--camera", choices=("ready", "unavailable", "error"))
    parser.add_argument("--microphone", choices=("ready", "unavailable", "error"))
    parser.add_argument("--speaker", choices=("ready", "unavailable", "error"))
    parser.add_argument("--free-heap-bytes", type=int)
    parser.add_argument("--reconnect-max-seconds", type=float)
    parser.add_argument(
        "--reconnect-jitter",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--http-timeout-seconds", type=float)
    parser.add_argument("--verbose", action="store_true")
    return parser


def _settings_from_namespace(namespace: argparse.Namespace) -> SimulatorSettings:
    values = {
        key: value
        for key, value in vars(namespace).items()
        if key != "verbose" and value is not None
    }
    return SimulatorSettings(**values)


def settings_from_args(argv: Sequence[str] | None = None) -> SimulatorSettings:
    """Parse CLI overrides and let Pydantic Settings fill environment values."""

    parser = build_parser()
    namespace = parser.parse_args(argv)
    try:
        return _settings_from_namespace(namespace)
    except ValidationError:
        parser.error("invalid configuration; check required environment variables and CLI values")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the simulator until interrupted."""

    parser = build_parser()
    namespace = parser.parse_args(argv)
    try:
        settings = _settings_from_namespace(namespace)
    except ValidationError:
        parser.error("invalid configuration; check required environment variables and CLI values")
    configure_logging(verbose=namespace.verbose)
    try:
        asyncio.run(DeviceSimulator(settings).run_forever())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("simulator stopped")


if __name__ == "__main__":
    main()
