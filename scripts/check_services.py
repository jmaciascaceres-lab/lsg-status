#!/usr/bin/env python3
"""
check_services.py - chequeo standalone (sin FastAPI) de los servicios LSG.

Pensado para ejecutarse vía cron, systemd timer, o como tarea Ansible
(módulo `script` o `command`), sin levantar el dashboard web.

Uso:
    python check_services.py
    python check_services.py --json
    python check_services.py --quiet   # solo exit code, útil para monitoring

Exit codes:
    0 -> todos los servicios en verde
    1 -> al menos un servicio en amarillo (degradado)
    2 -> al menos un servicio en rojo (caído)
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

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

# Grupo "Vitrina" (LSG-Web): mismo listado que app/main.py, para que cron/Ansible
# también cubran estos servicios. vitrina-cloud-mysql queda fuera (no expone HTTP).
VITRINA_GROUP_LABEL = os.getenv("VITRINA_GROUP_LABEL", "Vitrina (LSG-Web)")

VITRINA_SERVICES = [
    {
        "id": "vitrina-difusion",
        "label": os.getenv("VITRINA_DIFUSION_LABEL", "Difusion"),
        "url": os.getenv("VITRINA_DIFUSION_URL", "https://vitrina.diinf.usach.cl/home/"),
        "group": VITRINA_GROUP_LABEL,
    },
    {
        "id": "vitrina-frontend",
        "label": os.getenv("VITRINA_FRONTEND_LABEL", "Vitrina Frontend"),
        "url": os.getenv("VITRINA_FRONTEND_URL", "https://vitrina.diinf.usach.cl/vitrina/"),
        "group": VITRINA_GROUP_LABEL,
    },
    {
        "id": "vitrina-api",
        "label": os.getenv("VITRINA_API_LABEL", "Vitrina API"),
        "url": os.getenv("VITRINA_API_DOCS_URL", "https://vitrina.diinf.usach.cl/vitrina/api/v1/docs"),
        "group": VITRINA_GROUP_LABEL,
        "enabled": os.getenv("VITRINA_API_ENABLED", "false").lower() == "true",
    },
    {
        "id": "vitrina-auth",
        "label": os.getenv("VITRINA_AUTH_LABEL", "Auth (Vitrina)"),
        "url": os.getenv("VITRINA_AUTH_DOCS_URL", "https://vitrina.diinf.usach.cl/auth/api/v1/docs"),
        "group": VITRINA_GROUP_LABEL,
        "enabled": os.getenv("VITRINA_AUTH_ENABLED", "false").lower() == "true",
    },
    {
        "id": "vitrina-cloud-website",
        "label": os.getenv("VITRINA_CLOUD_WEBSITE_LABEL", "Cloud Website"),
        "url": os.getenv("VITRINA_CLOUD_WEBSITE_URL", "https://vitrina.diinf.usach.cl/cloud/"),
        "group": VITRINA_GROUP_LABEL,
    },
    {
        "id": "vitrina-cloud-api-get",
        "label": os.getenv("VITRINA_CLOUD_API_GET_LABEL", "Cloud API GET"),
        "url": os.getenv("VITRINA_CLOUD_API_GET_HEALTH_URL", "https://vitrina.diinf.usach.cl/cloud/api/get/health"),
        "group": VITRINA_GROUP_LABEL,
    },
    {
        "id": "vitrina-cloud-api-post",
        "label": os.getenv("VITRINA_CLOUD_API_POST_LABEL", "Cloud API POST"),
        "url": os.getenv("VITRINA_CLOUD_API_POST_HEALTH_URL", "https://vitrina.diinf.usach.cl/cloud/api/post/health"),
        "group": VITRINA_GROUP_LABEL,
    },
    {
        "id": "vitrina-cloud-attributes",
        "label": os.getenv("VITRINA_CLOUD_ATTRIBUTES_LABEL", "Cloud Attributes"),
        "url": os.getenv("VITRINA_CLOUD_ATTRIBUTES_HEALTH_URL", "https://vitrina.diinf.usach.cl/cloud/api/attributes/health"),
        "group": VITRINA_GROUP_LABEL,
    },
    {
        "id": "vitrina-cloud-user-mgmt",
        "label": os.getenv("VITRINA_CLOUD_USER_MGMT_LABEL", "Cloud User Mgmt"),
        "url": os.getenv("VITRINA_CLOUD_USER_MGMT_HEALTH_URL", "https://vitrina.diinf.usach.cl/cloud/api/users/health"),
        "group": VITRINA_GROUP_LABEL,
    },
]

SERVICES = SERVICES + VITRINA_SERVICES

LATENCY_WARN_MS = float(os.getenv("LATENCY_WARN_MS", "300"))
LATENCY_CRIT_MS = float(os.getenv("LATENCY_CRIT_MS", "1000"))
TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "5"))


def check(service: dict) -> dict:
    if service.get("enabled") is False:
        return {
            "id": service["id"],
            "label": service["label"],
            "url": service["url"],
            "group": service.get("group"),
            "status": "disabled",
            "status_code": None,
            "latency_ms": None,
            "error": None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    start = time.perf_counter()
    status = "green"
    status_code = None
    error = None
    try:
        req = urllib.request.Request(service["url"], headers={"User-Agent": "lsg-status-cli/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status_code = resp.status
        latency_ms = (time.perf_counter() - start) * 1000
        if status_code >= 500:
            status = "red"
        elif status_code >= 400 or latency_ms >= LATENCY_CRIT_MS or latency_ms >= LATENCY_WARN_MS:
            status = "yellow"
    except urllib.error.HTTPError as e:
        latency_ms = (time.perf_counter() - start) * 1000
        status_code = e.code
        status = "red" if e.code >= 500 else "yellow"
        error = str(e)
    except Exception as e:  # timeout, DNS, conexión rechazada, etc.
        latency_ms = (time.perf_counter() - start) * 1000
        status = "red"
        error = str(e)

    return {
        "id": service["id"],
        "label": service["label"],
        "url": service["url"],
        "group": service.get("group"),
        "status": status,
        "status_code": status_code,
        "latency_ms": round(latency_ms, 1),
        "error": error,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Chequeo de estado de servicios LSG")
    parser.add_argument("--json", action="store_true", help="Salida en formato JSON")
    parser.add_argument("--quiet", action="store_true", help="Sin salida; solo exit code")
    args = parser.parse_args()

    results = [check(s) for s in SERVICES]

    worst = "green"
    for r in results:
        if r["status"] == "red":
            worst = "red"
            break
        if r["status"] == "yellow":
            worst = "yellow"

    if not args.quiet:
        if args.json:
            print(json.dumps({"overall": worst, "services": results}, indent=2, ensure_ascii=False))
        else:
            icons = {"green": "🟢", "yellow": "🟡", "red": "🔴", "disabled": "⚪"}

            def print_row(r, indent=""):
                latency = f"{r['latency_ms']:>7.1f} ms" if r["latency_ms"] is not None else " (pendiente de integración)"
                print(f"{indent}{icons[r['status']]} {r['label']:<14} "
                      f"HTTP {str(r['status_code']):<4} "
                      f"{latency}  {r['url']}"
                      + (f"  -- {r['error']}" if r["error"] else ""))

            # Servicios sin grupo: una línea cada uno (comportamiento original).
            for r in results:
                if not r.get("group"):
                    print_row(r)

            # Servicios agrupados (p.ej. Vitrina/LSG-Web): encabezado con el peor
            # estado del grupo y cada sub-servicio indentado debajo. Los 'disabled'
            # no cuentan para el estado del grupo (ver worst_status en app/main.py).
            grouped: dict[str, list[dict]] = {}
            for r in results:
                if r.get("group"):
                    grouped.setdefault(r["group"], []).append(r)

            for group_label, members in grouped.items():
                active = [m for m in members if m["status"] != "disabled"]
                group_status = "disabled"
                for m in active:
                    if m["status"] == "red":
                        group_status = "red"
                        break
                    if m["status"] == "yellow":
                        group_status = "yellow"
                    elif group_status == "disabled":
                        group_status = "green"
                print(f"\n{icons[group_status]} {group_label}")
                for m in members:
                    print_row(m, indent="   ")

            print(f"\nEstado general: {icons[worst]} {worst.upper()}")

    sys.exit({"green": 0, "yellow": 1, "red": 2}[worst])


if __name__ == "__main__":
    main()
