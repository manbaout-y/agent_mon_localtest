from __future__ import annotations

from functools import wraps
import inspect
from types import ModuleType
from typing import Any, Callable


def install_module_facade(
    facade_globals: dict[str, Any],
    implementation: ModuleType,
    *,
    public_names: set[str] | None = None,
) -> None:
    """Expose a script facade for a package module while preserving monkeypatch tests."""
    names = sorted(public_names or _default_public_names(implementation))
    original_wrappers: dict[str, Any] = {}

    def apply_overrides() -> dict[str, Any]:
        backups: dict[str, Any] = {}
        for name, value in list(facade_globals.items()):
            if name.startswith("_") or not hasattr(implementation, name):
                continue
            original = original_wrappers.get(name)
            if original is not None and value is original:
                continue
            backups[name] = getattr(implementation, name)
            setattr(implementation, name, value)
        return backups

    def restore_overrides(backups: dict[str, Any]) -> None:
        for name, value in backups.items():
            setattr(implementation, name, value)

    def call_implementation(name: str, *args: Any, **kwargs: Any) -> Any:
        backups = apply_overrides()
        try:
            return getattr(implementation, name)(*args, **kwargs)
        finally:
            restore_overrides(backups)

    def make_wrapper(name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return call_implementation(name, *args, **kwargs)

        return wrapper

    for name in names:
        value = getattr(implementation, name)
        if inspect.isfunction(value) and value.__module__ == implementation.__name__:
            wrapper = make_wrapper(name, value)
            facade_globals[name] = wrapper
            original_wrappers[name] = wrapper
        else:
            facade_globals[name] = value

    facade_globals["_apply_module_overrides"] = apply_overrides
    facade_globals["_restore_module_overrides"] = restore_overrides
    facade_globals["_call_module"] = call_implementation
    facade_globals["_ORIGINAL_MODULE_WRAPPERS"] = original_wrappers


def _default_public_names(implementation: ModuleType) -> set[str]:
    names: set[str] = set()
    for name, value in vars(implementation).items():
        if name.startswith("_"):
            continue
        if name.isupper():
            names.add(name)
            continue
        if inspect.isfunction(value) and value.__module__ == implementation.__name__:
            names.add(name)
    return names
