"""M1.5: the public surface may not point at a page that does not exist.

The landing page shipped with 14 dead CTAs (``/pricing``, ``/signup``,
``/onboarding``, ...). A prospect who clicks "Start Free Trial" and gets a
404 is a lost sale and a credibility hole — the cheapest failure to prevent
and the easiest to reintroduce, so it is prevented *in the gate* rather than
by discipline.

Two invariants are enforced here:

1. **No dead internal link.** Every ``href`` under ``apps/dashboard/src`` must
   resolve to a real App Router page (``src/app/<route>/page.tsx``).
2. **No fake success.** The surface may not claim something the system does
   not do (the old waitlist form showed a success ``alert`` and recorded
   nothing). ``alert(`` is banned in the dashboard.

The test reads the source as text, so it runs in ``make check`` without the
JavaScript toolchain.

Status: the M1.5 sales surface landed (2026-10-21) — ``/pricing``, ``/login``,
``/signup``, ``/terms``, ``/privacy``, ``/dpa`` and ``/security`` exist, the
landing page points at them instead of at 14 dead routes, and the waitlist
form that showed a success ``alert`` while recording nothing is gone. The
``xfail(strict=True)`` markers that used to hold this debt are removed: the
debt is paid, so the assertions are now unconditional.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_SRC = Path(__file__).resolve().parents[2] / "apps" / "dashboard" / "src"
APP_DIR = DASHBOARD_SRC / "app"

# href="/pricing"  |  href={'/pricing'}  |  href="/pricing?plan=starter"
_HREF = re.compile(r"""href\s*=\s*["'](?P<href>[^"']+)["']""")
_TEMPLATE_HREF = re.compile(r"""href\s*=\s*\{`(?P<href>[^`]*)`\}""")

_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "//", "#")


def _source_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.tsx") if p.is_file())


def _route_page(href: str, app_dir: Path) -> Path:
    """Map an href to the App Router page file that would serve it."""
    path = href.split("?", 1)[0].split("#", 1)[0]
    if path in ("", "/"):
        return app_dir / "page.tsx"
    return app_dir / path.strip("/") / "page.tsx"


def _internal_hrefs(root: Path) -> list[tuple[str, str]]:
    """All (source file, href) pairs for internal links."""
    found: list[tuple[str, str]] = []
    for source in _source_files(root):
        text = source.read_text(encoding="utf-8")
        for match in _HREF.finditer(text):
            href = match.group("href")
            if not href.startswith(_EXTERNAL_PREFIXES):
                found.append((str(source.relative_to(root)), href))
        for match in _TEMPLATE_HREF.finditer(text):
            href = match.group("href")
            if href.startswith("/") and "${" not in href:
                found.append((str(source.relative_to(root)), href))
    return found


def _dead_links(root: Path) -> list[str]:
    dead: list[str] = []
    for source, href in _internal_hrefs(root):
        if not _route_page(href, root / "app").exists():
            dead.append(f"{source} -> {href}")
    return sorted(dead)


# ---------------------------------------------------------------------------
# The guard itself (always on): these prove the check has teeth.
# ---------------------------------------------------------------------------


def test_dashboard_has_source_files() -> None:
    """Guard against the guard silently passing on an empty directory."""
    assert _source_files(DASHBOARD_SRC), f"no .tsx files found under {DASHBOARD_SRC}"


def test_route_page_mapping() -> None:
    """The href -> page mapping is the whole logic of the guard."""
    assert _route_page("/", APP_DIR) == APP_DIR / "page.tsx"
    assert _route_page("/pricing", APP_DIR) == APP_DIR / "pricing" / "page.tsx"
    assert _route_page("/pricing?plan=starter", APP_DIR) == APP_DIR / "pricing" / "page.tsx"
    assert _route_page("/pricing#plans", APP_DIR) == APP_DIR / "pricing" / "page.tsx"
    assert _route_page("/a/b", APP_DIR) == APP_DIR / "a" / "b" / "page.tsx"


def test_guard_detects_a_dead_link(tmp_path: Path) -> None:
    """A synthetic dead link must be reported (the guard is not decorative)."""
    app = tmp_path / "app"
    app.mkdir(parents=True)
    (app / "page.tsx").write_text('<Link href="/pricing">Buy</Link>', encoding="utf-8")
    (app / "pricing").mkdir()
    (app / "pricing" / "page.tsx").write_text("export default function P() { return null; }")

    assert _dead_links(tmp_path) == []
    (app / "page.tsx").write_text('<Link href="/pricing">Buy</Link><Link href="/nope">X</Link>')
    assert _dead_links(tmp_path) == ["app/page.tsx -> /nope"]


def test_guard_ignores_external_links(tmp_path: Path) -> None:
    """External links are not routes; they must not be reported as dead."""
    app = tmp_path / "app"
    app.mkdir(parents=True)
    (app / "page.tsx").write_text(
        '<a href="https://wa.me/254700000000">wa</a><a href="mailto:x@y.z">mail</a>',
        encoding="utf-8",
    )
    assert _dead_links(tmp_path) == []


# ---------------------------------------------------------------------------
# The surface itself (unconditional since the M1.5 pages landed).
# ---------------------------------------------------------------------------


def test_no_dead_internal_links() -> None:
    """Every internal link must resolve to a page that exists."""
    dead = _dead_links(DASHBOARD_SRC)
    assert not dead, (
        "dead internal links (a prospect clicking these gets a 404):\n" + "\n".join(dead)
    )


def test_no_fake_success_alerts() -> None:
    """The surface may not claim a success the system does not record."""
    offenders = [
        str(source.relative_to(DASHBOARD_SRC))
        for source in _source_files(DASHBOARD_SRC)
        if "alert(" in source.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "alert( found in the dashboard (a success message the system does not "
        f"record): {offenders}"
    )


def test_home_page_exists() -> None:
    """The root route is the landing page and always exists."""
    assert (APP_DIR / "page.tsx").exists()


@pytest.mark.parametrize(
    "href", ["/pricing", "/login", "/signup", "/terms", "/privacy", "/dpa"]
)
def test_public_routes_exist(href: str) -> None:
    """The routes the sales surface promises must exist."""
    page = _route_page(href, APP_DIR)
    assert page.exists(), f"{href} has no page ({page})"
