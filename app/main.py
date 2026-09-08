"""
LSG Status - Semáforo de disponibilidad para los servicios LSG-Auth y LSG-Core-API.

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

from app.notifier import send_notification

load_dotenv()

# Configuración (vía variables de entorno, ver .env.example)

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
    {
        "id": "lsg-estudio",
        "label": os.getenv("ESTUDIO_LABEL", "LSG-Estudio"),
        "url": os.getenv("ESTUDIO_DOCS_URL", "https://lsg.diinf.usach.cl/lsg-estudio/"),
    },
]

LATENCY_WARN_MS = float(os.getenv("LATENCY_WARN_MS", "300"))
LATENCY_CRIT_MS = float(os.getenv("LATENCY_CRIT_MS", "1000"))
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "5"))
POLL_INTERVAL_MS = int(os.getenv("POLL_INTERVAL_MS", "15000"))

LOG_TO_DB = os.getenv("LOG_TO_DB", "false").lower() == "true"
EXPERIMENT_TAG = os.getenv("EXPERIMENT_TAG", "lsg-status-monitor-v1")

NOTIFY_CONSECUTIVE_THRESHOLD = int(os.getenv("NOTIFY_CONSECUTIVE_THRESHOLD", "2"))

StatusLevel = Literal["green", "yellow", "red"]

app = FastAPI(title="LSG Status", version="1.0.0")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# Lógica de chequeo

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


# Notificaciones — poller en background con debounce anti-flapping
#
# Estado por servicio:
#   streak_status / streak_count -> racha del último status "crudo" devuelto
#       por check_service, usada solo para exigir NOTIFY_CONSECUTIVE_THRESHOLD
#       chequeos seguidos antes de considerar el cambio "real" (evita ruido
#       por un timeout aislado).
#   notified_status -> último status por el que YA se avisó (None = verde/
#       normal). Solo se notifica de nuevo si el status crudo, sostenido
#       durante el umbral, difiere de notified_status. Esto cubre las 3
#       severidades pedidas: entrada a rojo, entrada a amarillo (incluyendo
#       escalar/desescalar entre rojo y amarillo), y recuperación a verde.
_service_state: dict[str, dict] = {}

STATUS_ICON = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
STATUS_LABEL_ES = {"red": "caído", "yellow": "degradado", "green": "operativo"}


async def evaluate_and_notify(results: list[dict]) -> None:
    for r in results:
        state = _service_state.setdefault(
            r["id"], {"streak_status": None, "streak_count": 0, "notified_status": None}
        )

        if r["status"] == state["streak_status"]:
            state["streak_count"] += 1
        else:
            state["streak_status"] = r["status"]
            state["streak_count"] = 1

        sustained = state["streak_count"] >= NOTIFY_CONSECUTIVE_THRESHOLD
        changed = r["status"] != state["notified_status"]

        if not (sustained and changed):
            continue

        if r["status"] == "green":
            if state["notified_status"] is not None:
                await send_notification(
                    f"{STATUS_ICON['green']} **{r['label']}** se recuperó "
                    f"(HTTP {r['status_code']}, {r['latency_ms']} ms)\n{r['url']}"
                )
            state["notified_status"] = None
        else:
            detail = r["error"] or f"HTTP {r['status_code']} · {r['latency_ms']} ms"
            await send_notification(
                f"{STATUS_ICON[r['status']]} **{r['label']}** {STATUS_LABEL_ES[r['status']]} "
                f"— {detail}\n{r['url']}"
            )
            state["notified_status"] = r["status"]


async def background_poller() -> None:
    """Loop independiente de requests HTTP entrantes: corre mientras el
    contenedor esté vivo, sin depender de que alguien tenga el dashboard
    abierto en el navegador."""
    while True:
        try:
            results = await check_all_services()
            await maybe_log_to_db(results)
            await evaluate_and_notify(results)
        except Exception as exc:  # nunca debe tumbar el loop
            print(f"[poller] Error en ciclo de chequeo: {exc}")
        await asyncio.sleep(POLL_INTERVAL_MS / 1000)


@app.on_event("startup")
async def _start_background_poller() -> None:
    asyncio.create_task(background_poller())


# Endpoints

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
