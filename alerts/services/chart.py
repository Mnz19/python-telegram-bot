"""Renders a small PNG chart of an alert's price history.

Uses matplotlib's non-interactive Agg backend (no display, no GUI deps) so
it's safe to call from a Celery worker or the bot process alike.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from ..models import Alert  # noqa: E402

MIN_POINTS_FOR_CHART = 2


def render_price_history_png(alert: Alert) -> bytes | None:
    """Returns a PNG of price-over-time for `alert`, or None if there isn't
    enough history yet to make a chart worthwhile."""
    history = list(alert.price_history.order_by("checked_at").values("checked_at", "price"))
    if len(history) < MIN_POINTS_FOR_CHART:
        return None

    dates = [row["checked_at"] for row in history]
    prices = [float(row["price"]) for row in history]

    fig, ax = plt.subplots(figsize=(6, 3), dpi=150)
    ax.plot(dates, prices, marker="o", color="#2f6fed", linewidth=2)
    ax.set_title(f"{alert.origin} → {alert.destination or 'vários destinos'}")
    ax.set_ylabel("Preço")
    fig.autofmt_xdate()
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()
