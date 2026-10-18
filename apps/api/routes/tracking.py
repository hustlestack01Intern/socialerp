"""
Public order tracking routes (Phase 7 remainder).

Two endpoints, both PUBLIC (no auth) and both deliberately PII-free:

- ``GET /api/v1/tracking/{order_id}?t=<token>``  → JSON projection (for the
  WhatsApp agent and the dashboard to embed).
- ``GET /tracking/{order_id}?t=<token>``         → a small self-contained
  HTML status page.

Both FAIL CLOSED (UNCERTAINTY U29): the ``t`` query parameter must be a
valid, unexpired capability token minted by
``packages.core.delivery.tracking_token.sign_tracking_token`` and bound to
the order's own tenant. A missing, malformed, tampered, expired or
wrong-tenant token returns the same 404 as an unknown order id — the
endpoints never reveal whether the order exists.

Both return only order/delivery status, the zone name, and a timestamped
timeline — never the customer's phone, name or address, and never the
rider's identity or location (see ``packages.core.delivery.tracking`` and
A4 in the Phase 7 plan).
"""

from __future__ import annotations

from html import escape
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import get_db
from packages.core.delivery.tracking import TrackingView, build_tracking

router = APIRouter(prefix="/api/v1/tracking", tags=["tracking"])
page_router = APIRouter(prefix="/tracking", tags=["tracking"])


def _as_dict(view: TrackingView) -> dict[str, object]:
    return {
        "order_ref": view.order_ref,
        "order_status": view.order_status,
        "delivery_status": view.delivery_status,
        "zone_name": view.zone_name,
        "payment_method": view.payment_method,
        "steps": [{"label": s.label, "at": s.at.isoformat()} for s in view.steps],
    }


def _not_found() -> HTTPException:
    # Deliberately identical for "unknown order" and "invalid token" (U29).
    return HTTPException(status_code=404, detail="Order not found")


@router.get("/{order_id}")
async def tracking_json(
    order_id: UUID,
    t: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Customer-safe tracking projection as JSON (public, no PII, token-gated)."""
    view = await build_tracking(db, order_id, token=t)
    if view is None:
        raise _not_found()
    return _as_dict(view)


@page_router.get("/{order_id}", response_class=HTMLResponse)
async def tracking_page(
    order_id: UUID,
    t: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Minimal public HTML tracking page (public, no PII, token-gated)."""
    view = await build_tracking(db, order_id, token=t)
    if view is None:
        raise _not_found()

    steps_html = "\n".join(
        f"      <li><strong>{escape(s.label)}</strong>"
        f" <span class='t'>{escape(s.at.strftime('%d %b %H:%M'))}</span></li>"
        for s in view.steps
    )
    zone = f" · {escape(view.zone_name)}" if view.zone_name else ""
    if view.delivery_status:
        delivery_label = escape(view.delivery_status.replace("_", " "))
    else:
        delivery_label = "Preparing"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Order {escape(view.order_ref)} — tracking</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0;
       margin:0;display:flex;justify-content:center;padding:2rem;}}
 .card{{background:#1e293b;border-radius:16px;padding:1.5rem;max-width:28rem;width:100%;
       box-shadow:0 10px 30px rgba(0,0,0,.4);}}
 h1{{font-size:1.25rem;margin:0 0 .25rem;}}
 .status{{display:inline-block;padding:.25rem .75rem;border-radius:999px;background:#0ea5e9;
       color:#04263a;font-weight:600;text-transform:capitalize;margin:.5rem 0 1rem;}}
 .meta{{color:#94a3b8;font-size:.875rem;margin-bottom:1rem;}}
 ul{{list-style:none;padding:0;margin:0;}}
 li{{padding:.5rem 0;border-bottom:1px solid #334155;display:flex;justify-content:space-between;}}
 .t{{color:#94a3b8;font-size:.8rem;}}
 footer{{color:#64748b;font-size:.75rem;margin-top:1.25rem;text-align:center;}}
</style>
</head>
<body>
  <div class="card">
    <h1>Order {escape(view.order_ref)}</h1>
    <div class="status">{escape(view.order_status.replace("_", " "))}</div>
    <div class="meta">
      {delivery_label}{zone}
    </div>
    <ul>
{steps_html}
    </ul>
    <footer>Questions? Reply on WhatsApp or message the shop.</footer>
  </div>
</body>
</html>
"""


__all__ = ["page_router", "router"]
