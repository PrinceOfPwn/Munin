"""Run strict Production Suite fixture cleanup in an Actions `always()` step."""

from __future__ import annotations

import argparse

from munin.mcp.config import get_settings
from munin.production.store import ProductionStore
from munin.production.testing import cleanup_test_run, janitor_expired_test_runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-run-id", default="")
    parser.add_argument("--fixture-user", default="", help="exact llm_smoke_* fixture username")
    parser.add_argument("--janitor", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.db_url.startswith(("libsql://", "libsqls://")):
        print("production fixture cleanup skipped: authoritative Turso is not configured")
        return 0
    store = ProductionStore.for_settings(settings, master_key=ProductionStore.master_key_from_environment())
    count = janitor_expired_test_runs(store) if args.janitor else cleanup_test_run(store, test_run_id=args.test_run_id)
    if args.fixture_user:
        deleted = store.delete_user_for_test(username=args.fixture_user)
        print(f"production fixture cleanup removed fixture user={args.fixture_user!r}: {deleted}")
    print(f"production fixture cleanup removed {count} conversation fixture(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
