"""Temporary compatibility bridge for the pre-package module layout.

The public command names documented before the 2026-08 reorganization remain
available while implementation modules live in responsibility-based packages.
"""

from importlib import import_module
from types import ModuleType


def reexport(namespace: dict[str, object], module_name: str) -> ModuleType:
    """Expose a relocated module's API in a legacy module namespace."""
    module = import_module(module_name)
    namespace.update(
        {
            name: value
            for name, value in vars(module).items()
            if name not in {"__builtins__", "__cached__", "__loader__", "__name__", "__package__", "__spec__"}
        }
    )
    return module
