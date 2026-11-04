"""Isolated Kejtu replacement candidate, not the installed product.

Imports perform no IO or configuration discovery. Bootstrap alone composes the
thin core's concrete adapters. No runtime dependency on knowledge_engine_next.
"""
from __future__ import annotations

# Single version owner: packaging and CLI reference this attribute.
__version__ = "0.0.1"

__all__ = ["__version__"]
