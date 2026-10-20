"""Stable import surface for the proven WhatsApp helpers.

This module re-exports the helpers from ``whatsapp_lambda`` verbatim so the new
agent code has a stable place to import from while the WhatsApp send/parse
behavior stays identical to the reference implementation. Nothing is
reimplemented here.
"""

from whatsapp_lambda import (  # noqa: F401
    send_whatsapp_message,
    extract_webhook_data,
    extract_text_messages,
    normalize_destination,
)

__all__ = [
    "send_whatsapp_message",
    "extract_webhook_data",
    "extract_text_messages",
    "normalize_destination",
]
