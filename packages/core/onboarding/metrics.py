"""
Onboarding Wizard Metrics

Prometheus metrics for the 30-minute onboarding wizard SLO.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# =============================================================================
# Metric Definitions
# =============================================================================

# Step duration histogram (per step, status, fallback type)
ONBOARDING_STEP_DURATION = Histogram(
    "onboarding_wizard_step_duration_seconds",
    "Time spent in each onboarding step",
    ["step", "status", "fallback_type"],
    buckets=[60, 120, 180, 240, 300, 480, 600, 900, 1800, 3600],
)

# Total wizard duration histogram
ONBOARDING_TOTAL_DURATION = Histogram(
    "onboarding_wizard_total_duration_seconds",
    "Total onboarding wizard duration",
    ["status", "fallback_type"],
    buckets=[60, 120, 180, 240, 300, 480, 600, 900, 1800, 3600],
)

# Step started counter
ONBOARDING_STEP_STARTED = Counter(
    "onboarding_wizard_step_started_total",
    "Onboarding steps started",
    ["step"],
)

# Step completed counter
ONBOARDING_STEP_COMPLETED = Counter(
    "onboarding_wizard_step_completed_total",
    "Onboarding steps completed",
    ["step", "status", "fallback_type"],
)

# Active sessions gauge
ONBOARDING_ACTIVE_SESSIONS = Gauge(
    "onboarding_wizard_active_sessions",
    "Currently active onboarding sessions",
)

# Fallback counter
ONBOARDING_FALLBACK = Counter(
    "onboarding_wizard_fallback_total",
    "Fallback triggers during onboarding",
    ["step", "fallback_type"],
)

# SLO breach counter
ONBOARDING_SLO_BREACH = Counter(
    "onboarding_wizard_slo_breach_total",
    "SLO breaches (step or total)",
    ["scope"],
)

# Launch approved counter
ONBOARDING_LAUNCH_APPROVED = Counter(
    "onboarding_wizard_launch_approved_total",
    "Onboarding launch approvals",
)

# Wizard aborted counter
ONBOARDING_WIZARD_ABORTED = Counter(
    "onboarding_wizard_aborted_total",
    "Onboarding wizards aborted",
    ["step", "reason"],
)

# =============================================================================
# Step Target Constants (for alerting)
# =============================================================================

STEP_TARGETS = {
    1: 180,   # 3 min
    2: 480,   # 8 min
    3: 480,   # 8 min
    4: 300,   # 5 min
    5: 240,   # 4 min
    6: 120,   # 2 min
}

TOTAL_TARGET_SECONDS = sum(STEP_TARGETS.values())  # 1800 = 30 min


# =============================================================================
# Helper Functions
# =============================================================================


def record_step_start(step: int) -> None:
    """Record step start."""
    ONBOARDING_STEP_STARTED.labels(step=step).inc()
    ONBOARDING_ACTIVE_SESSIONS.inc()


def record_step_complete(
    step: int,
    duration_seconds: float,
    status: str,
    fallback_type: str = "none",
) -> None:
    """Record step completion."""
    ONBOARDING_STEP_DURATION.labels(
        step=step, status=status, fallback_type=fallback_type
    ).observe(duration_seconds)
    ONBOARDING_STEP_COMPLETED.labels(
        step=step, status=status, fallback_type=fallback_type
    ).inc()
    ONBOARDING_ACTIVE_SESSIONS.dec()

    # Check SLO for this step
    target = STEP_TARGETS.get(step, 0)
    if duration_seconds > target * 2:
        ONBOARDING_SLO_BREACH.labels(scope=f"step_{step}").inc()


def record_fallback(step: int, fallback_type: str) -> None:
    """Record a fallback trigger."""
    ONBOARDING_FALLBACK.labels(step=step, fallback_type=fallback_type).inc()


def record_launch_approved() -> None:
    """Record launch approval."""
    ONBOARDING_LAUNCH_APPROVED.inc()
    ONBOARDING_ACTIVE_SESSIONS.dec()


def record_wizard_aborted(step: int | None, reason: str = "unknown") -> None:
    """Record wizard abortion."""
    step_str = str(step) if step else "unknown"
    ONBOARDING_WIZARD_ABORTED.labels(step=step_str, reason=reason).inc()
    ONBOARDING_ACTIVE_SESSIONS.dec()


def record_total_duration(
    duration_seconds: float,
    status: str,
    fallback_type: str = "none",
) -> None:
    """Record total wizard duration."""
    ONBOARDING_TOTAL_DURATION.labels(
        status=status, fallback_type=fallback_type
    ).observe(duration_seconds)

    # Check overall SLO
    if duration_seconds > 1800:  # 30 min
        ONBOARDING_SLO_BREACH.labels(scope="total").inc()


# =============================================================================
# Alert Rules (for reference - implement in PrometheusRule)
# =============================================================================

ALERT_RULES = """
groups:
- name: onboarding-wizard-slo
  rules:
  - alert: OnboardingMedianExceeds30Min
    expr: |
      histogram_quantile(0.50, sum(rate(onboarding_wizard_total_duration_seconds_bucket[1h])) by (le)) > 1800
    for: 15m
    labels: {severity: "warning", team: "onboarding"}
    annotations:
      summary: "Median onboarding time > 30 min"
      description: "p50 = {{ $value | humanizeDuration }} over last hour"

  - alert: OnboardingStepMedianExceeds2xTarget
    expr: |
      sum by (step) (histogram_quantile(0.50, sum(rate(onboarding_wizard_step_duration_seconds_bucket{status="COMPLETED"}[1h])) by (le, step))) 
      > 
      sum by (step) (onboarding_wizard_step_target_seconds * 2)
    for: 10m
    labels: {severity: "warning", team: "onboarding"}
    annotations:
      summary: "Step {{ $labels.step }} median > 2x target"

  - alert: OnboardingFallbackRateHigh
    expr: |
      sum by (step, fallback_type) (rate(onboarding_wizard_fallback_total[1h])) 
      / 
      sum by (step) (rate(onboarding_wizard_step_started_total[1h])) > 0.2
    for: 5m
    labels: {severity: "critical", team: "onboarding"}
    annotations:
      summary: "Fallback rate > 20% for step {{ $labels.step }} ({{ $labels.fallback_type }})"
"""

# =============================================================================
# Target seconds for alerting (Prometheus metric)
# =============================================================================

# This would be exposed as a metric for alerting reference
# onboarding_wizard_step_target_seconds{step="1"} 180
# onboarding_wizard_step_target_seconds{step="2"} 480
# etc.