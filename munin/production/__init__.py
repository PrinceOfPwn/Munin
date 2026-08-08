"""Production-grade persistence and security primitives for Munin."""

from . import store as _store
from .store_reconciliation import apply_store_reconciliation

# ``store.py`` is the one file where the Issue #18 frontend stack and later
# Discord/runtime fixes evolved heavily in parallel. Apply the small explicit
# reconciliation before exporting/constructing any store so neither side's
# contract is silently lost by the consolidation merge.
apply_store_reconciliation(_store)

ProductionStore = _store.ProductionStore
MuninStore = _store.MuninStore

__all__ = ["MuninStore", "ProductionStore"]
