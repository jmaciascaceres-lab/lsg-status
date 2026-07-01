"""
LSG Status — semáforo de disponibilidad para los servicios LSG-Auth y LSG-Core-API.

Consulta periódicamente los endpoints /docs (OpenAPI/Swagger UI) de cada servicio
y expone:
  - GET /            -> dashboard HTML (semáforo)
  - GET /api/status  -> JSON con el estado de cada servicio
  - GET /healthz     -> health check del propio monitor

Diseñado para integrarse al ecosistema LSG (FastAPI + Docker), pudiendo
desplegarse junto a LSG-Auth y LSG-Core-API vía docker-compose.
"""

import os
import time
import asyncio
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.requests import Request
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------
# Configuración (vía variables de entorno, ver .env.example)
# --------------------------------------------------------------------------

SERVICES = [
    {
        "id": "lsg-auth",
        "label": os.getenv("AUTH_LABEL", "LSG-Auth"),
        "url": os.getenv("AUTH_DOCS_URL", "https://lsg.diinf.usach.cl/lsg-auth/docs"),
    },
    {
        "id": "lsg-core-api",
        "label": os.getenv("CORE_API_LABEL", "LSG-Core-API"),
        "url": os.getenv("CORE_API_DOCS_URL", "https://lsg.diinf.usach.cl/lsg-core-api/docs"),
    },
]

LATENCY_WARN_MS = float(os.getenv("LATENCY_WARN_MS", "300"))
LATENCY_CRIT_MS = float(os.getenv("LATENCY_CRIT_MS", "1000"))
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "5"))
POLL_INTERVAL_MS = int(os.getenv("POLL_INTERVAL_MS", "15000"))

LOG_TO_DB = os.getenv("LOG_TO_DB", "false").lower() == "true"
EXPERIMENT_TAG = os.getenv("EXPERIMENT_TAG", "lsg-status-monitor-v1")

StatusLevel = Literal["green", "yellow", "red"]

app = FastAPI(title="LSG Status", version="1.0.0")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# --------------------------------------------------------------------------
# Lógica de chequeo
# --------------------------------------------------------------------------

async def check_service(client: httpx.AsyncClient, service: dict) -> dict:
    """Realiza un GET al endpoint /docs del servicio y clasifica su estado."""
    start = time.perf_counter()
    status_level: StatusLevel
    status_code = None
    error_message = None

    try:
        response = await client.get(service["url"], timeout=REQUEST_TIMEOUT_S)
        latency_ms = (time.perf_counter() - start) * 1000
        status_code = response.status_code

        if response.status_code >= 500:
            status_level = "red"
        elif response.status_code >= 400:
            status_level = "yellow"
        elif latency_ms >= LATENCY_CRIT_MS:
            status_level = "yellow"
        elif latency_ms >= LATENCY_WARN_MS:
            status_level = "yellow"
        else:
            status_level = "green"

    except httpx.TimeoutException:
        latency_ms = (time.perf_counter() - start) * 1000
        status_level = "red"
        error_message = f"Timeout tras {REQUEST_TIMEOUT_S}s"
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        status_level = "red"
        error_message = str(exc)

    return {
        "id": service["id"],
        "label": service["label"],
        "url": service["url"],
        "status": status_level,
        "status_code": status_code,
        "latency_ms": round(latency_ms, 1),
        "error": error_message,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def check_all_services() -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *(check_service(client, svc) for svc in SERVICES)
        )
    return list(results)


async def maybe_log_to_db(results: list[dict]) -> None:
    """
    Punto de extensión: si LOG_TO_DB=true, persistir cada chequeo en la tabla
    `interaction_logs` reutilizando la convención experiment_tag + JSONB.
    No implementado por defecto para mantener este monitor sin dependencias
    de base de datos; ver README para el patch SQL sugerido.
    """
    if not LOG_TO_DB:
        return
    # Ejemplo de integración (descomentar y adaptar con su capa de acceso a datos):
    #
    # async with db_session() as session:
    #     for r in results:
    #         await session.execute(
    #             insert(interaction_logs).values(
    #                 experiment_tag=EXPERIMENT_TAG,
    #                 event_type="service_status_check",
    #                 metrics={
    #                     "service_id": r["id"],
    #                     "status": r["status"],
    #                     "status_code": r["status_code"],
    #                     "latency_ms": r["latency_ms"],
    #                     "error": r["error"],
    #                 },
    #                 created_at=r["checked_at"],
    #             )
    #         )
    #     await session.commit()
    pass


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/api/status", response_class=JSONResponse)
async def api_status():
    results = await check_all_services()
    await maybe_log_to_db(results)
    overall: StatusLevel = "green"
    if any(r["status"] == "red" for r in results):
        overall = "red"
    elif any(r["status"] == "yellow" for r in results):
        overall = "yellow"
    return {
        "overall": overall,
        "services": results,
        "poll_interval_ms": POLL_INTERVAL_MS,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "poll_interval_ms": POLL_INTERVAL_MS},
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
