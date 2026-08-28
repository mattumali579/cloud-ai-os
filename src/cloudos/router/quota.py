"""Daily quota accounting for free-tier providers (contracts §5 quota_usage, §9).

Two modes:

* **DB mode** — when ``settings.database_url`` is set, counts live in the
  ``quota_usage`` table (pk: provider, day) via a lazy ``from cloudos import db``
  (Agent 4's module). This is the shared/deployed mode.
* **Local/no-db mode** — when ``DATABASE_URL`` is empty, counts live in an
  in-process dict. Counts reset on process restart and are not shared between
  processes; acceptable for local dev only, and documented as such.

Failure policy (contracts §0 — fail closed): a quota-store failure must NEVER
crash routing, and must never be treated as "under budget". Store errors raise
:class:`QuotaStoreError`; the router treats that as OVER budget.

``units`` exists in the schema for finer-grained accounting (tokens/neurons).
v1 gates on **request counts** only: Cloudflare does not report per-response
neuron cost, so requests/day is the only deterministic local measure. ``units``
is accepted by :meth:`QuotaStore.increment` and persisted, but not consulted
by the budget gate.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from cloudos.config import get_settings

log = logging.getLogger("cloudos.router.quota")


class QuotaStoreError(RuntimeError):
    """Quota store unavailable. Callers MUST fail closed (treat as over budget)."""


@dataclass(frozen=True)
class Usage:
    requests: int = 0
    units: int = 0


def today() -> date:
    """The quota day: UTC calendar date (provider free tiers reset on UTC days)."""
    return datetime.now(timezone.utc).date()


class QuotaStore:
    """check/increment daily usage per provider. See module docstring for modes."""

    def __init__(self) -> None:
        self._local: dict[tuple[str, str], tuple[int, int]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _db_mode() -> bool:
        return bool(get_settings().database_url)

    def get_usage(self, provider: str, day: date) -> Usage:
        """Current usage for (provider, day). Raises QuotaStoreError on DB failure."""
        if self._db_mode():
            try:
                from cloudos import db  # lazy: Agent 4's module; absent in partial checkouts

                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT requests, units FROM quota_usage "
                            "WHERE provider = %s AND day = %s",
                            (provider, day),
                        )
                        row = cur.fetchone()
            except Exception as exc:  # noqa: BLE001 — anything here means "store down"
                log.error("quota get_usage(%s, %s) store failure: %s", provider, day, exc)
                raise QuotaStoreError(f"get_usage failed for provider={provider}") from exc
            if not row:
                return Usage()
            if isinstance(row, dict):  # db contract returns dict rows
                return Usage(int(row.get("requests") or 0), int(row.get("units") or 0))
            return Usage(int(row[0] or 0), int(row[1] or 0))

        with self._lock:
            requests, units = self._local.get((provider, day.isoformat()), (0, 0))
        return Usage(requests, units)

    def increment(self, provider: str, day: date, requests: int = 1, units: int = 0) -> None:
        """Add usage for (provider, day). Raises QuotaStoreError on DB failure."""
        if self._db_mode():
            try:
                from cloudos import db

                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO quota_usage (provider, day, requests, units) "
                            "VALUES (%s, %s, %s, %s) "
                            "ON CONFLICT (provider, day) DO UPDATE SET "
                            "requests = quota_usage.requests + EXCLUDED.requests, "
                            "units = quota_usage.units + EXCLUDED.units",
                            (provider, day, requests, units),
                        )
                    try:
                        conn.commit()
                    except Exception:  # noqa: BLE001 — pool may hand out autocommit conns
                        pass
            except Exception as exc:  # noqa: BLE001
                log.error("quota increment(%s, %s) store failure: %s", provider, day, exc)
                raise QuotaStoreError(f"increment failed for provider={provider}") from exc
            return

        with self._lock:
            key = (provider, day.isoformat())
            cur_requests, cur_units = self._local.get(key, (0, 0))
            self._local[key] = (cur_requests + requests, cur_units + units)


_store: Optional[QuotaStore] = None


def get_store() -> QuotaStore:
    """Process-wide QuotaStore singleton."""
    global _store
    if _store is None:
        _store = QuotaStore()
    return _store


def reset_store() -> None:
    """Test helper: drop the singleton (clears in-process counts)."""
    global _store
    _store = None
