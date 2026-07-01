# LSG Status - Semáforo de servicios

Mini-servicio (FastAPI) que monitorea en tiempo real la disponibilidad de `LSG-Auth` y `LSG-Core-API`, mostrando un dashboard tipo semáforo (🟢/🟡/🔴) y exponiendo un endpoint JSON consumible por scripts de monitoreo, Ansible o dashboards externos.

> **Baseline:** este proyecto es complementario a los servicios productivos definidos en la propuesta de tesis y la nota técnica LSG (pipeline sensores → perfil → motor de reglas → adaptadores). No modifica ni reemplaza `LSG-Auth` / `LSG-Core-API`; solo los observa desde afuera vía sus endpoints `/docs` (Swagger UI / OpenAPI).

## 1. Servicios monitoreados

| Servicio | URL verificada | Método |
| --- | --- | --- |
| LSG-Auth | `https://lsg.diinf.usach.cl/lsg-auth/docs` | `GET` |
| LSG-Core-API | `https://lsg.diinf.usach.cl/lsg-core-api/docs` | `GET` |

Criterio de semáforo (configurable vía `.env`):

| Color | Condición |
| --- | --- |
| 🟢 Verde | HTTP 2xx/3xx y latencia < `LATENCY_WARN_MS` (300 ms por defecto) |
| 🟡 Amarillo | HTTP 4xx, o 2xx con latencia ≥ `LATENCY_WARN_MS` |
| 🔴 Rojo | HTTP 5xx, timeout, o error de conexión/DNS |

## 2. Estructura del repo

```
lsg-status/
├── app/
│   ├── main.py              # API FastAPI + lógica de chequeo
│   └── templates/index.html # Dashboard semáforo (HTML + JS vanilla)
├── scripts/
│   └── check_services.py    # CLI standalone (sin FastAPI) para cron/Ansible
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## 3. Levantar el dashboard

### Opción A - Docker (recomendado, consistente con el resto de LSG)

```bash
cp .env.example .env
docker compose up -d --build
# Dashboard disponible en http://localhost:8090
```

### Opción B - Local sin Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8090
```

## 4. Endpoints expuestos por este monitor

```bash
# Dashboard HTML (semáforo visual)
curl -s http://localhost:8090/

# Estado en JSON de ambos servicios
curl -s http://localhost:8090/api/status | jq

# Health check del propio monitor (para uptime checks externos)
curl -s http://localhost:8090/healthz
```

Ejemplo de respuesta de `/api/status`:

```json
{
  "overall": "green",
  "poll_interval_ms": 15000,
  "services": [
    {
      "id": "lsg-auth",
      "label": "LSG-Auth",
      "url": "https://lsg.diinf.usach.cl/lsg-auth/docs",
      "status": "green",
      "status_code": 200,
      "latency_ms": 142.3,
      "error": null,
      "checked_at": "2026-06-30T15:04:02.118Z"
    },
    {
      "id": "lsg-core-api",
      "label": "LSG-Core-API",
      "url": "https://lsg.diinf.usach.cl/lsg-core-api/docs",
      "status": "green",
      "status_code": 200,
      "latency_ms": 98.7,
      "error": null,
      "checked_at": "2026-06-30T15:04:02.203Z"
    }
  ]
}
```

## 5. Uso como script standalone (cron / Ansible / systemd)

Para monitoreo sin levantar el servicio web (por ejemplo, una tarea
programada en DIINF-USACH que solo necesita un exit code):

```bash
python scripts/check_services.py            # salida legible
python scripts/check_services.py --json      # salida JSON
python scripts/check_services.py --quiet     # solo exit code
echo $?   # 0=verde, 1=amarillo, 2=rojo
```

Ejemplo de tarea Ansible:

```yaml
- name: Verificar estado de servicios LSG
  command: python3 /opt/lsg-status/scripts/check_services.py --quiet
  register: lsg_health
  failed_when: lsg_health.rc == 2
  changed_when: false
```

Ejemplo de cron (cada 5 minutos, log a archivo):

```cron
*/5 * * * * /usr/bin/python3 /opt/lsg-status/scripts/check_services.py --json >> /var/log/lsg-status.log 2>&1
```

## 6. Integración opcional con `interaction_logs` (FONDECYT)

Si se desea conservar el historial de disponibilidad junto con el resto de métricas del proyecto (manteniendo la convención `experiment_tag` + columna JSONB), se sugiere el siguiente patch SQL para reutilizar el esquema existente sin crear una tabla nueva:

```sql
-- Patch sugerido: no crea tabla nueva, reutiliza interaction_logs.
-- Verificar que experiment_tag y la columna JSONB de métricas existan
-- antes de aplicar (ajustar nombre de columna JSONB según esquema real).

INSERT INTO interaction_logs (experiment_tag, event_type, metrics, created_at)
VALUES (
    'lsg-status-monitor-v1',
    'service_status_check',
    '{
        "service_id": "lsg-auth",
        "status": "green",
        "status_code": 200,
        "latency_ms": 142.3,
        "error": null
    }'::jsonb,
    NOW()
);
```

El punto de extensión está dejado listo (pero deshabilitado por defecto, `LOG_TO_DB=false`) en `app/main.py`, función `maybe_log_to_db()`. Activar solo si se cuenta con acceso de escritura a la base de datos compartida del proyecto y tras validar el patch con el equipo de datos.

## 7. Alcance (MoSCoW) de esta primera versión

| Prioridad | Requisito |
| --- | --- |
| **Must** | Chequear disponibilidad de `/docs` de LSG-Auth y LSG-Core-API |
| **Must** | Clasificar estado en verde/amarillo/rojo según código HTTP y latencia |
| **Must** | Dashboard web auto-refrescable sin dependencias externas de frontend |
| **Should** | Script CLI standalone para cron/Ansible (sin levantar el servicio web) |
| **Should** | Despliegue vía Docker, integrable a la red del compose principal de LSG |
| **Could** | Persistencia de histórico en `interaction_logs` (FONDECYT) |
| **Could** | Notificaciones (correo/Slack/Webhook) ante transición a rojo |
| **Won't (v1)** | Autenticación/roles sobre el dashboard (asumido en red interna/VPN) |
| **Won't (v1)** | Verificación de endpoints autenticados (más allá de `/docs`) |

## 8. Notas de seguridad

- Este monitor solo realiza `GET` a endpoints públicos de documentación (`/docs`); no requiere ni transmite credenciales JWT.
- Si se expone el dashboard fuera de la red de DIINF-USACH, restringir acceso (reverse proxy con auth básica, IP allowlist, o VPN) para no exponer públicamente el estado interno de la infraestructura.

---

## Referencias

- R. González-Ibáñez, J. I. Macías-Cáceres and M. V. Paucar, "LifeSync-Games: A Technical Note on a Novel Framework for Video Game Development," 2025 44th International Conference of the Chilean Computer Science Society (SCCC), Valparaiso, Chile, 2025, pp. 1-4, doi: 10.1109/SCCC67219.2025.11420722.
- González-Ibáñez R., Macías-Cáceres J., Villalta-Paucar M. (2025). *LifeSync-Games: Toward a Video Game Paradigm for Promoting Responsible Gaming and Human Development*. arXiv:2510.19691 [cs.HC]. DOI: https://arxiv.org/abs/2510.19691