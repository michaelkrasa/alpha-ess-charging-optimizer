"""Scheduled quarter-hour controller and authenticated dashboard Function URL."""

import asyncio
import os
import logging

from mangum import Mangum

from src.manager.controller import Controller
from src.manager.settings import load_settings
from src.manager.store import BusyError, make_store


def lambda_handler(event, context):
    logging.getLogger("alphaess").setLevel(logging.CRITICAL)
    settings = load_settings()
    store = make_store(settings)
    live = os.environ.get("MANAGER_LIVE", "false").lower() == "true"
    if event.get("requestContext", {}).get("http"):
        from src.manager.web import create_app

        return Mangum(create_app(settings, store, live=live), lifespan="off")(
            event, context
        )
    try:
        return asyncio.run(Controller(settings, store, live=live).run_once())
    except BusyError:
        return {
            "status": "busy",
            "message": "Another controller operation owns the lease",
        }
