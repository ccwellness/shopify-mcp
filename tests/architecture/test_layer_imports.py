"""Architecture / import-graph tests (TR-25, TR-26).

Enforce the layered architecture from the design doc Section 11A so that
layer-rule violations break CI rather than silently rotting over time.

Layer model (dependencies flow downward only):
  L5 Presentation : app.blueprints, app.cli, app.__init__ (composition root)
  L4 Services     : app.services
  L3 Domain       : app.domain  (pure Python — no Flask, no SQLAlchemy)
  L2 Repositories : app.repositories  (only layer outside app.db that imports SQLAlchemy)
  L1 Infrastructure: app.db, app.shopify, app.jobs

Composition-root files are the only modules allowed to wire concrete
implementations across layers; they are exempt from cross-layer bans.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app"
MCP_ROOT = ROOT / "mcp_server"

COMPOSITION_ROOT: frozenset[Path] = frozenset(
    {
        APP_ROOT / "__init__.py",
        APP_ROOT / "cli.py",
        APP_ROOT / "container.py",
        # The MCP server's __init__.py is documentation only; the entrypoint
        # `__main__.py` wires the lazy Container singleton.
        MCP_ROOT / "__main__.py",
        MCP_ROOT / "server.py",
    }
)


def _imports_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module)
    return out


def _files_under(*parts: str) -> list[Path]:
    base = APP_ROOT.joinpath(*parts)
    py_file = base.with_suffix(".py")
    if py_file.is_file():
        return [py_file]
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _matches(module: str, *prefixes: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _violations(
    files: list[Path],
    banned: tuple[str, ...],
) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for path in files:
        if path in COMPOSITION_ROOT:
            continue
        for imp in sorted(_imports_in(path)):
            if _matches(imp, *banned):
                out.append((path, imp))
    return out


def _format(rule: str, vs: list[tuple[Path, str]]) -> str:
    lines = [f"\n{rule}", f"  ({len(vs)} violation(s)):"]
    for path, imp in vs:
        lines.append(f"    {path.relative_to(ROOT)} imports {imp!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# L3 Domain — pure-Python protocols + dataclasses. No framework imports.
# ---------------------------------------------------------------------------


def test_domain_layer_is_pure_python() -> None:
    files = _files_under("domain")
    banned = (
        "flask",
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.services",
        "app.blueprints",
        "app.shopify",
        "app.jobs",
    )
    vs = _violations(files, banned)
    assert not vs, _format("L3 Domain must be pure Python (TR-26).", vs)


# ---------------------------------------------------------------------------
# L2 Repositories — only layer outside app.db that imports sqlalchemy.
# Repos may not call services / blueprints / shopify / jobs.
# ---------------------------------------------------------------------------


def test_repositories_do_not_call_upper_layers_or_shopify() -> None:
    files = _files_under("repositories")
    banned = (
        "flask",
        "app.services",
        "app.blueprints",
        "app.shopify",
        "app.jobs",
    )
    vs = _violations(files, banned)
    assert not vs, _format(
        "L2 Repositories must not import services / blueprints / shopify / jobs.",
        vs,
    )


# ---------------------------------------------------------------------------
# L4 Services — must use repository protocols (app.domain.repositories), never
# concrete SQLAlchemy or app.db / app.repositories. The composition root wires
# a concrete UnitOfWork in.
# ---------------------------------------------------------------------------


def test_services_use_protocols_not_concrete_db() -> None:
    files = _files_under("services")
    banned = (
        "flask",
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.blueprints",
    )
    vs = _violations(files, banned)
    assert not vs, _format(
        "L4 Services must depend on repository protocols, not concrete SQLAlchemy.",
        vs,
    )


# ---------------------------------------------------------------------------
# L1 Infrastructure — app.shopify and app.jobs are leaf adapters. Must not
# reach into the persistence layer or call upward into services / blueprints.
# ---------------------------------------------------------------------------


def test_shopify_layer_is_isolated_adapter() -> None:
    files = _files_under("shopify")
    banned = (
        "flask",
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.services",
        "app.blueprints",
        "app.jobs",
    )
    vs = _violations(files, banned)
    assert not vs, _format("L1 app.shopify must be an isolated HTTP/GraphQL adapter.", vs)


def test_jobs_layer_is_isolated_adapter() -> None:
    files = _files_under("jobs")
    banned = (
        "flask",
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.services",
        "app.blueprints",
    )
    vs = _violations(files, banned)
    assert not vs, _format("L1 app.jobs must be an isolated queue adapter.", vs)


# ---------------------------------------------------------------------------
# L5 Presentation — blueprints render / route. They go through services and
# may reference domain types, but never SQLAlchemy or repositories directly.
# ---------------------------------------------------------------------------


def test_blueprints_call_services_not_repositories() -> None:
    files = _files_under("blueprints")
    banned = (
        "sqlalchemy",
        "app.db",
        "app.repositories",
    )
    vs = _violations(files, banned)
    assert not vs, _format(
        "L5 Blueprints must go through services — "
        "no direct sqlalchemy / db / repositories imports.",
        vs,
    )


# ---------------------------------------------------------------------------
# L5 Presentation — MCP server. Same rules as blueprints: services only,
# never sqlalchemy / app.db / app.repositories. The composition root files
# (mcp_server/__main__.py, mcp_server/server.py) are exempt.
# ---------------------------------------------------------------------------


def test_mcp_server_calls_services_not_repositories() -> None:
    files = sorted(p for p in MCP_ROOT.rglob("*.py") if "__pycache__" not in p.parts)
    banned = (
        "flask",
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.blueprints",
    )
    vs = _violations(files, banned)
    assert not vs, _format(
        "L5 mcp_server must go through services — "
        "no flask / sqlalchemy / db / repositories / blueprints imports.",
        vs,
    )
