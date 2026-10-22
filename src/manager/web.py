"""One private dashboard for local browsers and a Lambda Function URL."""

import asyncio
import hashlib
import hmac
import math
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, StrictBool

from .controller import Controller
from .energy_day import dashboard_daily_totals, dashboard_energy
from .soc_tracking import dashboard_soc
from .plan_insight import explain_plan
from .domain import PRAGUE, day_slots, next_boundary, utc_now
from .settings import load_settings
from .store import BusyError, make_store
from .tariff import high_tariff, prices, schedule_verified


def dashboard_prices(store, settings, now):
    """Show complete cached market days, independently of the future-only plan.

    Dashboard reads perform no market/API requests. Billed prices use the same
    tariff function and bounded-age FX cache as planning; missing inputs remain
    unknown rather than manufacturing a billed price.
    """
    today = now.astimezone(PRAGUE).date()
    fx = store.get("eur_conversion") or {}
    rate = fx.get("rate")
    try:
        valid_fx = (
            rate is not None
            and math.isfinite(rate)
            and 10 < rate < 100
            and 0 <= (today - date.fromisoformat(fx["published"])).days <= 7
        )
    except (KeyError, ValueError, TypeError):
        valid_fx = False
    result = []
    for day in (today, today + timedelta(days=1)):
        values = store.get(f"prices:{settings.price_source}:{day}")
        instants = day_slots(day)
        if values is None or len(values) != len(instants):
            continue
        if any(not isinstance(p, (int, float)) or not math.isfinite(p) for p in values):
            continue
        for t, spot in zip(instants, values):
            verified = schedule_verified(t, settings)
            high = high_tariff(t, settings.high_tariff_periods) if verified else True
            buy, sell = prices(spot, rate, high) if valid_fx else (None, None)
            result.append(
                {
                    "timestamp": t.isoformat(),
                    "spot_eur_mwh": spot,
                    "import_eur_kwh": buy,
                    "export_eur_kwh": sell,
                    "tariff": "VT" if high else "NT",
                    "tariff_verified": verified,
                    "elapsed": t + timedelta(minutes=15) <= now,
                }
            )
    return result


def poll_timeout(now, actuation=None):
    """Wake at a pending daily write deadline as well as quarter-hour ticks."""
    timeout = (next_boundary(now) - now).total_seconds()
    if actuation and actuation.get("status") == "waiting":
        try:
            deadline = datetime.fromisoformat(actuation["next_write_at"])
            remaining = (deadline - now).total_seconds()
            # Ignore expired deadlines: repeatedly retrying them would spin if
            # another readiness check still prevents dispatch.
            if remaining > 0:
                timeout = min(timeout, remaining)
        except (KeyError, TypeError, ValueError):
            pass
    return max(1, timeout)


def ensure_password():
    if os.environ.get("DASHBOARD_PASSWORD"):
        return
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        raise ValueError("DASHBOARD_PASSWORD is required on Lambda")
    value = secrets.token_urlsafe(24)
    target = Path(".env")
    with target.open("a") as f:
        f.write("\nDASHBOARD_PASSWORD=" + value + "\n")
    target.chmod(0o600)
    os.environ["DASHBOARD_PASSWORD"] = value


def create_app(
    settings=None,
    store=None,
    live=False,
    poll=False,
    controller=None,
    require_auth=None,
):
    settings = settings or load_settings()
    # Local household service is intentionally passwordless. Cloud URLs retain
    # authentication even if a caller accidentally requests a local override.
    cloud = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    auth_required = cloud or bool(require_auth)
    if auth_required:
        ensure_password()
    store = store or make_store(settings)
    controller = controller or Controller(settings, store, live=live)
    wake = asyncio.Event()

    async def loop():
        while True:
            busy = False
            try:
                await controller.run_once()
            except BusyError:
                busy = True
            except Exception:
                # Persistent-store outages should be visible to the process supervisor.
                import logging

                logging.getLogger(__name__).error(
                    "Controller cycle failed before status could be saved"
                )
            now = utc_now()
            try:
                actuation = store.get("actuation")
            except Exception:
                actuation = None
            timeout = poll_timeout(now, actuation)
            if busy:
                timeout = min(timeout, 5)
            try:
                await asyncio.wait_for(wake.wait(), timeout)
            except asyncio.TimeoutError:
                pass
            wake.clear()

    async def background_cycle():
        try:
            await controller.run_once()
        except BusyError:
            pass

    async def monitor_loop():
        while True:
            try:
                await controller.monitor_once()
            except BusyError:
                pass
            except Exception:
                # observe() records a sanitized failure; monitoring never writes settings.
                pass
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(loop()) if poll else None
        monitor = (
            asyncio.create_task(monitor_loop())
            if poll and getattr(controller, "live", False)
            else None
        )
        yield
        for running in (task, monitor):
            if not running:
                continue
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.controller = controller
    security = HTTPBasic(auto_error=False)

    def session_token(expires):
        value = str(expires)
        signature = hmac.new(
            os.environ["DASHBOARD_PASSWORD"].encode(), value.encode(), hashlib.sha256
        ).hexdigest()
        return value + "." + signature

    def session_valid(request):
        token = request.cookies.get("energy_session", "")
        try:
            expires = int(token.split(".")[0])
            return (
                time.time() < expires < time.time() + 86400
                and secrets.compare_digest(token, session_token(expires))
            )
        except (ValueError, IndexError):
            return False

    def authorize(
        request: Request, value: HTTPBasicCredentials | None = Depends(security)
    ):
        if not auth_required:
            return
        if session_valid(request):
            return
        if value is None:
            raise HTTPException(401, "Sign in to the household dashboard")
        valid_user = secrets.compare_digest(
            value.username.encode(), os.getenv("DASHBOARD_USER", "household").encode()
        )
        valid_password = secrets.compare_digest(
            value.password.encode(), os.environ["DASHBOARD_PASSWORD"].encode()
        )
        if not (valid_user and valid_password):
            raise HTTPException(
                401,
                "Sign in to the household dashboard",
                headers={"WWW-Authenticate": 'Basic realm="Household energy"'},
            )

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request, value: HTTPBasicCredentials | None = Depends(security)
    ):
        try:
            authorize(request, value)
        except HTTPException:
            return Path(__file__).with_name("login.html").read_text()
        return Path(__file__).with_name("dashboard.html").read_text()

    class LoginInput(BaseModel):
        password: str

    @app.post("/api/login")
    async def login(value: LoginInput, request: Request, response: Response):
        if not auth_required:
            return {"signed_in": True, "authentication_required": False}
        if request.headers.get("X-Control-Request") != "dashboard":
            raise HTTPException(403, "Use the sign-in form")
        if len(value.password) > 256 or not secrets.compare_digest(
            value.password.encode(), os.environ["DASHBOARD_PASSWORD"].encode()
        ):
            raise HTTPException(401, "Incorrect household password")
        response.set_cookie(
            "energy_session",
            session_token(int(time.time() + 43200)),
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=43200,
        )
        return {"signed_in": True}

    @app.post("/api/logout", dependencies=[Depends(authorize)])
    async def logout(response: Response):
        response.delete_cookie("energy_session")
        return {"signed_out": True}

    @app.get("/api/state", dependencies=[Depends(authorize)])
    async def state():
        now = utc_now()
        history = store.history(96)
        plan = store.get("plan")
        if plan and plan.get("mode", "periodic") != settings.control_mode:
            plan = None
        energy = dashboard_energy(store, now, history)
        price_rows = dashboard_prices(store, settings, now)
        return {
            "now": now.isoformat(),
            "price_intervals": price_rows,
            "plan_insights": explain_plan(plan, price_rows),
            "control": controller.control(),
            "health": store.get("health"),
            "telemetry": store.get("telemetry"),
            "device_connection": store.get("device_connection"),
            "energy_day": energy,
            "daily_energy": dashboard_daily_totals(store, now),
            "soc_tracking": dashboard_soc(store, now, energy),
            "plan": plan,
            "replay": store.get("replay"),
            "actuation": store.get("actuation"),
            "device_schedule": store.get("device_schedule"),
            "applied_schedule": store.get("applied_schedule"),
            "planner_comparison": store.get("planner_comparison"),
            "standard_planner": settings.standard_planner,
            "load_profile": store.get("load_profile"),
            "authentication_required": auth_required,
            "readiness": controller.readiness(),
            "live": controller.live,
            "reserve_percent": settings.reserve_percent,
            "import_limit_kw": settings.import_limit_kw,
            "price_source": settings.price_source,
            "control_mode": settings.control_mode,
            "write_interval_seconds": settings.legacy_min_write_interval_seconds,
            "daily_write": max(
                (
                    x
                    for x in [
                        store.get("legacy_charge_write"),
                        store.get("legacy_discharge_write"),
                    ]
                    if x
                ),
                key=lambda x: x["attempted_at"],
                default=None,
            ),
            "history": history,
        }

    class ControlInput(BaseModel):
        enabled: StrictBool

    @app.post("/api/control", dependencies=[Depends(authorize)])
    async def control(
        value: ControlInput, request: Request, background: BackgroundTasks
    ):
        # JSON + custom header require a same-origin fetch/preflight. No CORS allowed.
        if request.headers.get("X-Control-Request") != "dashboard":
            raise HTTPException(403, "Use the dashboard control")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin controls are disabled")
        try:
            result = await controller.set_enabled(value.enabled)
        except BusyError:
            raise HTTPException(
                409, "Controller is finishing a cycle; retry shortly"
            ) from None
        if poll:
            wake.set()
        else:
            background.add_task(background_cycle)
        return result

    return app
