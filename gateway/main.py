"""Composition root for the OpenRoIS Kachaka thin gateway.

Wires the BusAdapter (real KachakaAdapter or MockAdapter) to the RoISHandler
and WSServer. This is the only file that imports both the adapter and the
gateway. When the monorepo's openrois-engine and openrois-gateway packages
land (M1/M2), handler.py and server.py are replaced by monorepo imports.
The adapter does not change.

Usage:
    python gateway/main.py --target 192.168.1.100:26400 --port 31166
    python gateway/main.py --mock --port 31166
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure the gateway package and the bus-adapter package are importable.
# The gateway directory is a scripts-only layout (no pyproject.toml), so we
# add the parent directory to sys.path for `from gateway.handler import ...`.
# The bus-adapter package is installed via `pip install -e bus-adapter/`.
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenRoIS Kachaka thin gateway (WebSocket + JSON-RPC 2.0)"
    )
    parser.add_argument(
        "--target",
        default="192.168.1.100:26400",
        help="Kachaka gRPC target address (default: 192.168.1.100:26400)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="WebSocket bind host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=31166,
        help="WebSocket bind port (default: 31166)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Event poll interval in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock adapter (no real robot, hardcoded responses)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.mock:
        from gateway.mock_adapter import MockAdapter

        adapter = MockAdapter()
        logging.info("Using MockAdapter (no real robot)")
    else:
        from openrois_bus_kachaka import KachakaAdapter, KachakaAdapterConfig

        config = KachakaAdapterConfig(
            target=args.target,
            poll_interval_sec=args.poll_interval,
        )
        adapter = KachakaAdapter(config)
        logging.info("Using KachakaAdapter (target=%s)", args.target)

    from gateway.handler import RoISHandler
    from gateway.server import WSServer

    handler = RoISHandler(adapter=adapter)
    server = WSServer(handler=handler, host=args.host, port=args.port)

    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())