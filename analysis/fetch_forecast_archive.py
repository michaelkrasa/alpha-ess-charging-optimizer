"""Cache original IFS forecast runs for causal backtests; no inverter access.

Run separately for each period, including eight preceding warm-up days.
Existing valid runs are reused; unavailable runs remain explicit failures.
"""

import argparse
import asyncio, datetime, json, pathlib, time
import httpx, yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--start", required=True, type=datetime.date.fromisoformat)
parser.add_argument("--end", required=True, type=datetime.date.fromisoformat)
args = parser.parse_args()
if args.end < args.start:
    parser.error("end precedes start")
OUT = ROOT / "data/backtests/weather"
OUT.mkdir(parents=True, exist_ok=True)
CFG = yaml.safe_load((ROOT / "solar_config.yaml").read_text())
counts = {"success": 0, "cached": 0, "failed": 0}
failures = []
started = time.time()


def runs(start, end):
    d = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    while d <= last:
        for h in (0, 6, 12, 18):
            yield f"{d}T{h:02d}:00"
        d += datetime.timedelta(days=1)


async def main():
    requested = list(runs(str(args.start), str(args.end)))
    queue = asyncio.Queue()
    for run in requested:
        queue.put_nowait(run)
    print(
        json.dumps({"status": "started", "requested_runs": queue.qsize()}),
        flush=True,
    )
    async with httpx.AsyncClient(
        timeout=65, limits=httpx.Limits(max_connections=3)
    ) as client:

        async def worker():
            while not queue.empty():
                try:
                    run = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                p = OUT / (run[:13] + ".json")
                if p.exists():
                    try:
                        existing = json.loads(p.read_text())
                        if (
                            existing.get("hourly", {}).get("time")
                            and existing.get("run", "")[:13] == run[:13]
                        ):
                            counts["cached"] += 1
                            queue.task_done()
                            continue
                    except Exception:
                        pass
                params = {
                    "latitude": CFG["latitude"],
                    "longitude": CFG["longitude"],
                    "tilt": CFG.get("tilt", 15),
                    "azimuth": CFG.get("azimuth", -30),
                    "hourly": "global_tilted_irradiance",
                    "models": "ecmwf_ifs",
                    "run": run,
                    "forecast_days": 3,
                    "timezone": "UTC",
                }
                problem = "unknown"
                for attempt in range(6):
                    await asyncio.sleep(0.65)
                    try:
                        r = await client.get(
                            "https://single-runs-api.open-meteo.com/v1/forecast",
                            params=params,
                        )
                        if r.status_code in (429, 500, 502, 503, 504):
                            problem = f"HTTP {r.status_code}"
                            retry = r.headers.get("Retry-After", "")
                            await asyncio.sleep(
                                min(
                                    60,
                                    float(retry)
                                    if retry.isdigit()
                                    else 3 * (2**attempt),
                                )
                            )
                            continue
                        if r.status_code != 200:
                            problem = f"HTTP {r.status_code}"
                            break
                        data = r.json()
                        hourly = data.get("hourly", {})
                        values = hourly.get("global_tilted_irradiance", [])
                        if not hourly.get("time") or not any(
                            v is not None for v in values
                        ):
                            problem = "empty GTI data"
                            break
                        data.update(
                            source="open-meteo-single-runs",
                            model="ecmwf_ifs",
                            run=run + ":00+00:00",
                            fetched_at=datetime.datetime.now(
                                datetime.timezone.utc
                            ).isoformat(),
                        )
                        temp = p.with_suffix(".tmp")
                        temp.write_text(json.dumps(data, separators=(",", ":")))
                        temp.replace(p)
                        counts["success"] += 1
                        break
                    except Exception as exc:
                        problem = type(exc).__name__
                        await asyncio.sleep(min(30, 2**attempt))
                else:
                    pass
                if not p.exists():
                    counts["failed"] += 1
                    failures.append({"run": run, "error": problem})
                queue.task_done()
                done = sum(counts.values())
                if done % 50 == 0:
                    print(
                        json.dumps(
                            {
                                "status": "progress",
                                **counts,
                                "elapsed_s": round(time.time() - started),
                            }
                        ),
                        flush=True,
                    )

        await asyncio.gather(*(worker() for _ in range(3)))
    report = {
        "source": "https://open-meteo.com/en/docs/single-runs-api",
        "model": "ecmwf_ifs",
        "periods": [[str(args.start), str(args.end)]],
        "init_hours_utc": [0, 6, 12, 18],
        "forecast_days": 3,
        **counts,
        "failures": failures,
        "elapsed_s": round(time.time() - started),
    }
    (OUT / f"fetch_manifest_{args.start}_{args.end}.json").write_text(
        json.dumps(report, indent=2)
    )
    print(
        json.dumps(
            {
                "status": "complete",
                **counts,
                "failures": failures[:20],
                "elapsed_s": round(time.time() - started),
            }
        ),
        flush=True,
    )


asyncio.run(main())
