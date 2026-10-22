import argparse
import asyncio
from datetime import date
import json
import logging

from .controller import Controller
from .providers import Providers
from .replay import replay
from .settings import load_settings
from .store import make_store


def cli():
    parser = argparse.ArgumentParser(
        description="Household battery manager: EUR pricing, native self-consumption, 10% reserve"
    )
    parser.add_argument("--config", default="manager.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--live", action="store_true")
    once = sub.add_parser("once")
    once.add_argument("--live", action="store_true")
    rep = sub.add_parser("replay")
    rep.add_argument("--date", type=date.fromisoformat, default=date(2025, 12, 10))
    rep.add_argument("--power-file", default="analysis/output/alphaess_power_2025.csv")
    mode = sub.add_parser("mode")
    mode.add_argument("state", choices=["on", "off"])
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    # Upstream error logs include device identifiers; UI/state uses sanitized errors.
    logging.getLogger("alphaess").setLevel(logging.CRITICAL)
    settings = load_settings(args.config)
    store = make_store(settings)
    if args.command == "serve":
        import uvicorn
        from .web import create_app

        print(
            "Dashboard: http://"
            + ("127.0.0.1" if args.host == "0.0.0.0" else args.host)
            + ":"
            + str(args.port)
        )
        print("Local household dashboard: no sign-in required.")
        uvicorn.run(
            create_app(settings, store, live=args.live, poll=True),
            host=args.host,
            port=args.port,
            log_level="warning",
            access_log=False,
        )
    elif args.command == "replay":
        result = asyncio.run(
            replay(
                args.date, settings, store, Providers(settings, store), args.power_file
            )
        )
        print(
            json.dumps(
                {
                    "source": result["source"],
                    "day": result["day"],
                    "intervals": len(result["decisions"]),
                    "notes": result["notes"],
                }
            )
        )
    else:
        controller = Controller(settings, store, live=args.live)
        result = asyncio.run(
            controller.run_once()
            if args.command == "once"
            else controller.set_enabled(args.state == "on")
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    cli()
