"""Owner insights: deterministic, tenant-scoped, read-only reports and anomalies."""

from packages.core.insights.service import (
    DEFAULT_FACTOR,
    DEFAULT_MIN_BASELINE,
    DEFAULT_TOP_K,
    DEFAULT_WINDOW_DAYS,
    Anomaly,
    DailyReport,
    InsightsService,
    PeriodReport,
    StockAlert,
    TopItem,
    detect_anomaly_from_counts,
)

__all__ = [
    "DEFAULT_FACTOR",
    "DEFAULT_MIN_BASELINE",
    "DEFAULT_TOP_K",
    "DEFAULT_WINDOW_DAYS",
    "Anomaly",
    "DailyReport",
    "InsightsService",
    "PeriodReport",
    "StockAlert",
    "TopItem",
    "detect_anomaly_from_counts",
]
