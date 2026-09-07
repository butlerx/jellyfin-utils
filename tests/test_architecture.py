"""Mechanical checks for the production module boundaries."""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "jellyfin_utils"
ROOT_COMPOSITION_MODULE = PACKAGE_ROOT / "cli.py"
USER_DOMAIN_MODULES = {
    PACKAGE_ROOT / "user" / "models.py",
    PACKAGE_ROOT / "user" / "snapshot.py",
}
RENDER_SAFE_CLIENT_EXPORTS = {
    "LibraryItem",
    "display_name",
    "size_gb",
}


def _production_modules() -> tuple[Path, ...]:
    return tuple(sorted(PACKAGE_ROOT.rglob("*.py")))


def _module_name(path: Path) -> str:
    relative_parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    if relative_parts[-1] == "__init__":
        relative_parts.pop()
    return ".".join(("jellyfin_utils", *relative_parts))


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = _module_name(path).split(".")
    if path.name != "__init__.py":
        package_parts.pop()
    package_parts = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _imports(path: Path) -> tuple[tuple[str, str | None, str, int], ...]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imports: list[tuple[str, str | None, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, None, f"import {alias.name}", node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(path, node)
            source_module = f"{'.' * node.level}{node.module or ''}"
            imports.extend(
                (
                    module,
                    alias.name,
                    f"from {source_module} import {alias.name}",
                    node.lineno,
                )
                for alias in node.names
            )
    return tuple(imports)


def _feature_cli_modules() -> frozenset[str]:
    return frozenset(
        _module_name(path)
        for path in _production_modules()
        if path.name == "cli.py" and path != ROOT_COMPOSITION_MODULE
    )


def _record_imports_module(
    record: tuple[str, str | None, str, int],
    module_name: str,
) -> bool:
    imported_module, imported_name, _source, _line = record
    return imported_module == module_name or (
        imported_name is not None and f"{imported_module}.{imported_name}" == module_name
    )


def _record_imports_forbidden_module(
    record: tuple[str, str | None, str, int],
    forbidden_module: str,
) -> bool:
    imported_module, imported_name, _source, _line = record
    return (
        imported_module == forbidden_module
        or imported_module.startswith(f"{forbidden_module}.")
        or (imported_name is not None and f"{imported_module}.{imported_name}" == forbidden_module)
    )


def _diagnostic(path: Path, line: int, forbidden_import: str, rule: str) -> str:
    relative_path = path.relative_to(PROJECT_ROOT)
    return f"{relative_path}:{line}: forbidden import `{forbidden_import}`; {rule}"


def _missing_import_diagnostic(path: Path, required_import: str, rule: str) -> str:
    relative_path = path.relative_to(PROJECT_ROOT)
    return f"{relative_path}: missing required import `{required_import}`; {rule}"


def _assert_no_violations(violations: list[str]) -> None:
    assert not violations, "Architecture boundary violations:\n" + "\n".join(violations)


def test_only_root_cli_composes_feature_commands() -> None:
    feature_clis = _feature_cli_modules()
    violations = []
    for path in _production_modules():
        if path == ROOT_COMPOSITION_MODULE:
            continue

        # Package facades intentionally preserve old command imports for callers.
        if path.name != "__init__.py":
            violations.extend(
                _diagnostic(
                    path,
                    record[3],
                    record[2],
                    "only jellyfin_utils/cli.py may import feature CLIs for composition",
                )
                for record in _imports(path)
                if any(_record_imports_module(record, feature_cli) for feature_cli in feature_clis)
            )

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_command"
            ):
                relative_path = path.relative_to(PROJECT_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno}: forbidden Click `.add_command(...)`; "
                    "only jellyfin_utils/cli.py is the application composition root"
                )
    _assert_no_violations(violations)


def test_services_and_workflows_do_not_import_feature_clis() -> None:
    feature_clis = _feature_cli_modules()
    violations = []
    for path in _production_modules():
        if path.stem not in {"service", "workflow"}:
            continue
        violations.extend(
            _diagnostic(
                path,
                record[3],
                record[2],
                "service and workflow modules must not depend on feature CLIs",
            )
            for record in _imports(path)
            if any(_record_imports_module(record, feature_cli) for feature_cli in feature_clis)
        )
    _assert_no_violations(violations)


def test_render_modules_do_not_import_api_io() -> None:
    forbidden_modules = {
        "requests",
        "jellyfin_utils.http",
        "jellyfin_utils.jellyseerr",
        "jellyfin_utils.client.transport",
        "jellyfin_utils.client.pagination",
        "jellyfin_utils.client.library",
        "jellyfin_utils.client.watch",
    }
    violations = []
    for path in _production_modules():
        if path.stem != "render":
            continue
        for record in _imports(path):
            imported_module, imported_name, source, line = record
            forbidden = any(_record_imports_forbidden_module(record, module) for module in forbidden_modules)
            if imported_module == "jellyfin_utils.client":
                forbidden = imported_name not in RENDER_SAFE_CLIENT_EXPORTS
            if forbidden:
                violations.append(
                    _diagnostic(
                        path,
                        line,
                        source,
                        "render modules must not import API I/O or transport code",
                    )
                )
    _assert_no_violations(violations)


def test_user_models_and_snapshots_are_domain_only() -> None:
    forbidden_modules = {
        "click",
        "http",
        "os",
        "pathlib",
        "requests",
        "jellyfin_utils.http",
        "jellyfin_utils.client.transport",
    }
    violations = []
    for path in sorted(USER_DOMAIN_MODULES):
        for record in _imports(path):
            _imported_module, _imported_name, source, line = record
            if any(_record_imports_forbidden_module(record, module) for module in forbidden_modules):
                violations.append(
                    _diagnostic(
                        path,
                        line,
                        source,
                        "user models and snapshots must not import CLI, HTTP, or filesystem code",
                    )
                )
    _assert_no_violations(violations)


def test_analysis_cli_uses_watched_and_stale_services() -> None:
    path = PACKAGE_ROOT / "analysis" / "cli.py"
    imports = _imports(path)
    violations = []
    for feature in ("watched", "stale"):
        service_module = f"jellyfin_utils.{feature}.service"
        cli_module = f"jellyfin_utils.{feature}.cli"
        if not any(_record_imports_module(record, service_module) for record in imports):
            violations.append(
                _missing_import_diagnostic(
                    path,
                    service_module,
                    f"analysis/cli.py must import the {feature} service",
                )
            )
        violations.extend(
            _diagnostic(
                path,
                record[3],
                record[2],
                f"analysis/cli.py must use the {feature} service, not its CLI",
            )
            for record in imports
            if _record_imports_module(record, cli_module)
        )
    _assert_no_violations(violations)
