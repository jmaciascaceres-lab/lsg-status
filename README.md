# LSG Status - Semáforo de servicios

Mini-servicio (FastAPI) que monitorea en tiempo real la disponibilidad de `LSG-Auth`, `LSG-Core-API` y `LSG-Estudio`, mostrando un dashboard tipo semáforo (🟢/🟡/🔴) y exponiendo un endpoint JSON consumible por scripts de monitoreo, Ansible o dashboards externos.

> **Baseline:** este proyecto es complementario a los servicios productivos definidos en la propuesta de tesis y la nota técnica LSG (pipeline sensores → perfil → motor de reglas → adaptadores). No modifica ni reemplaza `LSG-Auth` / `LSG-Core-API`; solo los observa desde afuera vía sus endpoints `/docs` (Swagger UI / OpenAPI).

**Versión:** 1.1

## 1. Servicios monitoreados

| Servicio | URL verificada | Método |
| --- | --- | --- |
| LSG-Auth | `https://lsg.diinf.usach.cl/lsg-auth/docs` | `GET` |
| LSG-Core-API | `https://lsg.diinf.usach.cl/lsg-core-api/docs` | `GET` |
| LSG-Estudio | `https://lsg.diinf.usach.cl/lsg-estudio/` | `GET` |

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
    },
    {
      "id": "lsg-estudio",
      "label": "LSG-Estudio",
      "url": "https://lsg.diinf.usach.cl/lsg-estudio/",
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

## 6. Alcance (MoSCoW) de esta primera versión

| Prioridad | Requisito |
| --- | --- |
| **Must** | Chequear disponibilidad de `/docs` de LSG-Auth y LSG-Core-API |
| **Must** | Clasificar estado en verde/amarillo/rojo según código HTTP y latencia |
| **Must** | Dashboard web auto-refrescable sin dependencias externas de frontend |
| **Should** | Script CLI standalone para cron/Ansible (sin levantar el servicio web) |
| **Should** | Despliegue vía Docker, integrable a la red del compose principal de LSG |
| **Could** | Notificaciones (correo/Slack/Webhook) ante transición a rojo |

## 7. Notas de seguridad

- Este monitor solo realiza `GET` a endpoints públicos de documentación (`/docs`); no requiere ni transmite credenciales JWT.

---

## Changelog

### v1.0 (2026-07-01)

**Features:**
- **`lsg-status`** - Nuevo servicio para monitorear el estado de los servicios LSG.

### v1.1 (2026-09-08)

**Features:**
- **`lsg-status`** - Nuevo servicio para monitorear el estado de los servicios LSG.

---

## Referencias

- [1] González-Ibáñez, R., Macías-Cáceres, J., Villalta-Paucar, M. (2025). LifeSync-Games: A Technical Note on a Novel Framework for Video Game Development. 2025 44th International Conference of the Chilean Computer Science Society (SCCC), Valparaiso, Chile, pp. 1-4, doi: 10.1109/SCCC67219.2025.11420722.<br>
- [2] González-Ibáñez R., Macías-Cáceres J., Villalta-Paucar M., (2025). LifeSync-Games: Toward a Video Game Paradigm for Promoting Responsible Gaming and Human Development. arXiv preprint: 2510.19691 [cs.HC].<br>
- [3] Macías-Cáceres J., Gutiérrez-Vela F., Paderewski-Rodriguez P., González-Ibáñez, R., (2026). LifeSync-Games: Signal-Driven Pervasive Game Design: The LifeSync-Games Framework as a Player Experience Integration Layer. arXiv preprint: 2609.03169 [cs.HC].