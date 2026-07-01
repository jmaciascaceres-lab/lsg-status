"""
notifier.py — envío de notificaciones a webhooks tipo Discord o Slack.

Diseño deliberadamente minimalista: un webhook es solo una URL secreta que
permite postear en un canal (no lee mensajes, no accede a la cuenta, se
revoca con un clic). No se maneja ningún otro tipo de credencial aquí.

La plataforma se detecta automáticamente por el dominio de la URL, así que
WEBHOOK_URLS admite mezclar Discord y Slack sin cambiar código.
"""

import os
import httpx

WEBHOOK_URLS = [u.strip() for u in os.getenv("WEBHOOK_URLS", "").split(",") if u.strip()]
NOTIFICATIONS_ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true"
WEBHOOK_TIMEOUT_S = float(os.getenv("WEBHOOK_TIMEOUT_S", "10"))


def _build_payload(url: str, text: str) -> dict:
    """Arma el JSON según la plataforma detectada por el dominio del webhook."""
    if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        return {"content": text}
    if "hooks.slack.com" in url:
        return {"text": text}
    # Fallback genérico: la mayoría de webhooks custom aceptan {"text": ...}
    return {"text": text}


async def send_notification(text: str) -> None:
    """Envía `text` a todos los webhooks configurados. No lanza excepciones
    hacia arriba: un fallo de notificación nunca debe tumbar el poller."""
    if not NOTIFICATIONS_ENABLED or not WEBHOOK_URLS:
        return

    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
        for url in WEBHOOK_URLS:
            try:
                resp = await client.post(url, json=_build_payload(url, text))
                if resp.status_code >= 300:
                    print(f"[notifier] Webhook respondió {resp.status_code}: {resp.text[:200]}")
            except httpx.HTTPError as exc:
                print(f"[notifier] Error enviando notificación: {exc}")
