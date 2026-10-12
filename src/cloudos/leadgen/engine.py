"""Batch orchestration with inventory gating, source rotation and self-healing."""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from cloudos.config import REPO_ROOT
from cloudos.leadgen import store
from cloudos.leadgen.enrich import Enrichment, Fetcher, enrich_website
from cloudos.leadgen.qualify import OUTREACH_READY_LEVELS, qualify
from cloudos.leadgen.sources import ALL_SOURCES, Source, SourceError
from cloudos.leadgen.store import Candidate

log = logging.getLogger("cloudos.leadgen")
CONFIG_PATH = REPO_ROOT / "config" / "lead_engine.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class RunStats:
    queries_run: int = 0
    raw_results: int = 0
    duplicates: int = 0
    new_companies: int = 0
    new_qualified: int = 0
    new_outreach_ready: int = 0
    rejected: int = 0
    low: int = 0
    recovered: int = 0
    source_errors: dict = field(default_factory=dict)
    per_source: dict = field(default_factory=dict)
    samples: list = field(default_factory=list)

    def bump(self, source: str, key: str, n: int = 1) -> None:
        self.per_source.setdefault(source, {}).setdefault(key, 0)
        self.per_source[source][key] += n


def _personalization(cand: Candidate, enr: Enrichment | None) -> dict:
    facts = dict(enr.facts) if enr else {}
    facts.update({k: v for k, v in {
        "google_reviews": cand.review_count, "google_rating": cand.rating,
        "maps_category": cand.extra.get("maps_category"), "osm_id": cand.extra.get("osm_id"),
        "evidence_url": enr.best_email.source_url if enr and enr.best_email else (enr.final_url if enr else ""),
    }.items() if v})
    return facts


class LeadEngine:
    def __init__(self, conn_factory: Callable, cfg: dict | None = None, sources: dict[str, Source] | None = None):
        self.conn_factory = conn_factory
        self.cfg = cfg or load_config()
        self.sources = sources or {name: cls() for name, cls in ALL_SOURCES.items()
                                   if self.cfg["sources"].get(name, {}).get("enabled", True)}

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        with self.conn_factory() as conn:
            inv = store.inventory(conn)
        inv["goal"] = self.cfg["inventory"]["total_goal"]
        with self.conn_factory() as conn:
            inv["found_today"] = store.found_today(conn, self.cfg["inventory"].get("timezone", "America/Chicago"))
        inv["daily_new_target"] = self.cfg["inventory"].get("daily_new_target")
        inv["progress_pct"] = round(100 * inv["qualified_new"] / inv["goal"], 1) if inv["goal"] else 0
        return inv

    def needs_refill(self, force: bool = False) -> tuple[bool, int]:
        with self.conn_factory() as conn:
            ready = store.inventory(conn)["available_inventory"]
        return (force or ready < self.cfg["inventory"]["low_watermark"]), ready

    def _db_call(self, fn):
        """Run one short database unit. Retry once when the pooler has dropped the socket.

        Callers must not do network I/O inside ``fn``. The Google Maps scrape and
        website fetches sit between calls, so a connection is not held open across
        them. A connection held for the whole scrape is what the pooler closes;
        the next write then dies with 'SSL connection has been closed unexpectedly'.
        """
        import psycopg
        last = None
        for attempt in (1, 2):
            try:
                with self.conn_factory() as conn:
                    return fn(conn)
            except psycopg.OperationalError as exc:
                last = exc
                log.warning("lead engine database connection dropped (%s), attempt %s",
                            type(exc).__name__, attempt)
        raise last

    # -------------------------------------------------------------- one company
    def _screen_candidates(self, conn, cands: list[Candidate], stats: RunStats) -> list[Candidate]:
        fresh: list[Candidate] = []
        seen_in_batch: set[str] = set()
        for cand in cands:
            stats.raw_results += 1
            stats.bump(cand.source, "raw")
            key = cand.normalized_domain or f"{cand.normalized_name}|{cand.city.lower()}"
            match = store.find_existing(conn, cand, cand.listing_emails)
            if match or key in seen_in_batch:
                stats.duplicates += 1
                stats.bump(cand.source, "duplicate")
                store.log_discovery(conn, cand.source, cand.query, match.company_id if match else None, "duplicate",
                                    f"matched on {match.key}" if match else "repeat within batch")
                continue
            seen_in_batch.add(key)
            fresh.append(cand)
        conn.commit()
        return fresh

    def _enrich_candidates(self, fresh: list[Candidate], fetcher: Fetcher, deadline: float) -> list:
        """Website fetches. No database connection is held here."""
        workers = int(self.cfg["run"].get("enrich_workers", 8))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(
                lambda c: None if (not c.website or time.time() > deadline) else
                enrich_website(c.website, fetcher, [(e, "public listing") for e in c.listing_emails]),
                fresh))

    def _store_candidates(self, conn, fresh: list[Candidate], enriched: list, stats: RunStats) -> list[str]:
        new_ids = []
        for cand, enr in zip(fresh, enriched):
            emails = [f.email for f in enr.emails] if enr else []
            match = store.find_existing(conn, cand, emails)
            if match:
                stats.duplicates += 1
                stats.bump(cand.source, "duplicate")
                store.log_discovery(conn, cand.source, cand.query, match.company_id, "duplicate", f"matched on {match.key}")
                continue
            verdict = qualify(name=cand.name, website=cand.website, industry=cand.industry or "local", enrichment=enr,
                              chains=self.cfg.get("chains", []), review_count=cand.review_count, brand_tag=cand.brand_tag)
            if verdict.level == "REJECT":
                ostatus = "rejected"
            elif verdict.outreach_ready:
                ostatus = "outreach_ready"
            else:
                ostatus = "not_ready"
            company_id = store.insert_company(conn, cand, qualification=verdict.level, reason=verdict.reason,
                                              outreach_status=ostatus, personalization=_personalization(cand, enr),
                                              enriched=enr is not None)
            for f in (enr.emails if enr else []):
                store.add_contact(conn, company_id, f.email, f.status, role=f.role, source=cand.source, source_url=f.source_url)
            store.log_discovery(conn, cand.source, cand.query, company_id,
                                "new" if verdict.level != "REJECT" else "rejected", verdict.reason)
            conn.commit()
            new_ids.append(company_id)
            stats.new_companies += 1
            stats.bump(cand.source, "new")
            if enr and enr.emails:
                stats.bump(cand.source, "with_email")
            if enr and any(f.status == "invalid" for f in enr.emails):
                stats.bump(cand.source, "invalid_email")
            if verdict.level == "REJECT":
                stats.rejected += 1
                stats.bump(cand.source, "rejected")
            elif verdict.level == "LOW":
                stats.low += 1
                stats.bump(cand.source, "low")
            else:
                stats.new_qualified += 1
                stats.bump(cand.source, "qualified")
            if ostatus == "outreach_ready":
                stats.new_outreach_ready += 1
                if len(stats.samples) < 40:
                    stats.samples.append(company_id)
        return new_ids

    def process_candidates(self, conn, cands: list[Candidate], stats: RunStats, fetcher: Fetcher,
                           deadline: float) -> list[str]:
        """Dedupe -> enrich (parallel) -> dedupe again by email -> qualify -> store."""
        fresh = self._screen_candidates(conn, cands, stats)
        enriched = self._enrich_candidates(fresh, fetcher, deadline)
        return self._store_candidates(conn, fresh, enriched, stats)

    # --------------------------------------------------------- history recovery
    def _history_candidates(self, conn, limit: int):
        return conn.execute(
            """
            SELECT company_id, company_name, website, industry, city, state FROM companies
            WHERE is_historical AND first_contacted_at IS NULL AND outreach_status = 'not_ready'
              AND enriched_at IS NULL AND website IS NOT NULL
            ORDER BY discovered_at LIMIT %s
            """, (limit,)).fetchall()

    def _enrich_history(self, rows, fetcher: Fetcher) -> list:
        with ThreadPoolExecutor(max_workers=int(self.cfg["run"].get("enrich_workers", 8))) as pool:
            return list(pool.map(lambda r: enrich_website(r["website"], fetcher), rows))

    def recover_history(self, conn, stats: RunStats, fetcher: Fetcher, limit: int) -> int:
        rows = self._history_candidates(conn, limit)
        if not rows:
            return 0
        results = self._enrich_history(rows, fetcher)
        return self._apply_history(conn, rows, results, stats)

    def _recover_history_detached(self, stats: RunStats, fetcher: Fetcher, limit: int) -> int:
        """Same recovery, but the database is not held during the website fetches."""
        rows = self._db_call(lambda conn: self._history_candidates(conn, limit))
        if not rows:
            return 0
        results = self._enrich_history(rows, fetcher)
        return self._db_call(lambda conn: self._apply_history(conn, rows, results, stats))

    def _apply_history(self, conn, rows, results, stats: RunStats) -> int:
        recovered = 0
        for row, enr in zip(rows, results):
            stats.bump("history_recovery", "raw")
            verdict = qualify(name=row["company_name"], website=row["website"], industry=row["industry"] or "local",
                              enrichment=enr, chains=self.cfg.get("chains", []))
            # an email must not belong to a company we already contacted
            clash = False
            for f in enr.emails:
                other = store.find_existing(conn, Candidate(name="", website=""), [f.email])
                clash = clash or bool(other and other.company_id != str(row["company_id"]))
            ready = verdict.outreach_ready and not clash
            conn.execute(
                "UPDATE companies SET enriched_at = now(), qualification_status = %s, qualification_reason = %s, "
                "outreach_status = %s, personalization = %s::jsonb, updated_at = now() WHERE company_id = %s",
                (verdict.level if not clash else "REJECT",
                 ("recovered: " + verdict.reason) if not clash else "recovered email belongs to an already-contacted company",
                 "outreach_ready" if ready else ("rejected" if verdict.level == "REJECT" or clash else "not_ready"),
                 json.dumps(_personalization(Candidate(name=row["company_name"]), enr), default=str),
                 row["company_id"]))
            for f in enr.emails:
                store.add_contact(conn, str(row["company_id"]), f.email, f.status, role=f.role,
                                  source="history_recovery", source_url=f.source_url)
            store.log_discovery(conn, "history_recovery", "recheck", str(row["company_id"]),
                                "new" if ready else "rejected", verdict.reason)
            conn.commit()
            if ready:
                recovered += 1
                stats.recovered += 1
                stats.new_outreach_ready += 1
                stats.bump("history_recovery", "qualified")
            if enr.emails:
                stats.bump("history_recovery", "with_email")
        return recovered

    # --------------------------------------------------------------- the batch
    def discover(self, target_new_ready: int, max_minutes: float, sources: list[str] | None = None,
                 queries_per_source: int = 3) -> RunStats:
        """Run rotating queries across sources until ``target_new_ready`` NEW
        outreach-ready companies were added, the time budget ends, or every
        source is exhausted/benched."""
        stats = RunStats()
        deadline = time.time() + max_minutes * 60
        # Sources keep their existing two-argument public interface, so share
        # this per-run deadline through the in-memory configuration.  It is
        # removed below and is never written to the YAML file.
        self.cfg["_discovery_deadline"] = deadline
        active = [s for s in (sources or list(self.sources)) if s in self.sources]
        fetcher = Fetcher(timeout=float(self.cfg["run"].get("http_timeout", 12)),
                          max_pages=int(self.cfg["run"].get("max_pages_per_site", 4)))
        policy = self.cfg.get("failure_policy", {})
        exhausted: set[str] = set()
        try:
            if "history_recovery" in active:
                exhausted.add("history_recovery")
                try:
                    self._recover_history_detached(
                        stats, fetcher, int(self.cfg["sources"]["history_recovery"].get("batch", 40)))
                    self._db_call(lambda conn: store.source_success(conn, "history_recovery"))
                except Exception as exc:  # noqa: BLE001 - one broken source never stops the others
                    def _record_history_failure(conn, _exc=exc):
                        store.source_failure(conn, "history_recovery", f"{type(_exc).__name__}: {_exc}", policy)
                        conn.commit()
                    try:
                        self._db_call(_record_history_failure)
                    except Exception:
                        log.warning("could not record history_recovery failure")
                    stats.source_errors["history_recovery"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                    log.warning("source history_recovery failed: %s", type(exc).__name__)
            while time.time() < deadline and stats.new_outreach_ready < target_new_ready:
                progressed = False
                for name in active:
                    if name in exhausted or time.time() >= deadline or stats.new_outreach_ready >= target_new_ready:
                        continue
                    src = self.sources[name]
                    catalog = src.queries(self.cfg)

                    def plan_batch(conn, _name=name, _catalog=catalog):
                        ok, why = store.source_available(conn, _name)
                        if not ok:
                            return False, why, []
                        return True, "", store.next_queries(conn, _name, _catalog, queries_per_source)

                    try:
                        ok, why, batch = self._db_call(plan_batch)
                    except Exception as exc:  # noqa: BLE001
                        stats.source_errors[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
                        log.warning("source %s planning failed: %s", name, type(exc).__name__)
                        continue
                    if not ok:
                        stats.source_errors[name] = why
                        exhausted.add(name)
                        continue
                    if not batch:
                        exhausted.add(name)
                        continue
                    for q in batch:
                        if time.time() >= deadline or stats.new_outreach_ready >= target_new_ready:
                            break
                        try:
                            # Network only. The database connection is not checked out here.
                            cands = src.search(q, self.cfg)
                        except SourceError as exc:
                            def record_failure(conn, _name=name, _exc=exc, _q=q):
                                health = store.source_failure(conn, _name, str(_exc), policy)
                                store.mark_query(conn, _name, _q, 0, 0, str(_exc))
                                conn.commit()
                                return health
                            try:
                                health = self._db_call(record_failure)
                            except Exception as db_exc:  # noqa: BLE001
                                stats.source_errors[name] = f"{type(db_exc).__name__}: {str(db_exc)[:160]}"
                                break
                            stats.source_errors[name] = str(exc)
                            stats.bump(name, "errors")
                            log.warning("source %s failed (%s consecutive): %s", name,
                                        health["consecutive_failures"], type(exc).__name__)
                            if health["disabled_until"]:
                                exhausted.add(name)
                                break
                            continue

                        def take_fresh(conn, _name=name, _cands=cands):
                            store.source_success(conn, _name)
                            return self._screen_candidates(conn, _cands, stats)

                        try:
                            fresh = self._db_call(take_fresh)
                        except Exception as exc:  # noqa: BLE001
                            stats.source_errors[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
                            log.warning("source %s result write failed: %s", name, type(exc).__name__)
                            continue
                        before = stats.new_companies
                        try:
                            enriched = self._enrich_candidates(fresh, fetcher, deadline)
                        except Exception as exc:  # noqa: BLE001
                            stats.source_errors[f"{name}:batch"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                            log.warning("batch from %s failed, continuing: %s", name, type(exc).__name__)
                            enriched = [None] * len(fresh)

                        def store_batch(conn, _name=name, _fresh=fresh, _enriched=enriched, _q=q,
                                        _before=before, _cands=cands):
                            import psycopg
                            try:
                                self._store_candidates(conn, _fresh, _enriched, stats)
                            except psycopg.OperationalError:
                                raise
                            except Exception as exc:  # noqa: BLE001 - skip this batch, keep the run alive
                                conn.rollback()
                                stats.source_errors[f"{_name}:batch"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                                log.warning("batch from %s failed, continuing: %s", _name, type(exc).__name__)
                            store.mark_query(conn, _name, _q, len(_cands), stats.new_companies - _before)
                            conn.commit()

                        try:
                            self._db_call(store_batch)
                        except Exception as exc:  # noqa: BLE001
                            stats.source_errors[f"{name}:batch"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                            log.warning("batch from %s failed, continuing: %s", name, type(exc).__name__)
                            continue
                        stats.queries_run += 1
                        stats.bump(name, "queries")
                        progressed = True
                        log.info("source=%s results=%d new=%d ready_total_this_run=%d", name, len(cands),
                                 stats.new_companies - before, stats.new_outreach_ready)
                if not progressed and all(s in exhausted for s in active):
                    break
        finally:
            self.cfg.pop("_discovery_deadline", None)
            fetcher.close()
        return stats

    def cycle(self, max_minutes: float | None = None, force: bool = False) -> dict:
        """One automatic cycle (what the scheduler calls): make sure today's quota of
        NEW qualified companies is met; also refill if ready inventory is low."""
        inv = self.cfg["inventory"]
        tz = inv.get("timezone", "America/Chicago")
        with self.conn_factory() as conn:
            today = store.found_today(conn, tz)
        refill, ready = self.needs_refill(force)
        daily_gap = max(int(inv.get("daily_new_target", 0)) - today, 0)
        want = max(daily_gap, (inv["target_ready"] - ready) if refill else 0, 1 if force else 0)
        result: dict = {"found_today_before": today, "daily_new_target": inv.get("daily_new_target"),
                        "ready_before": ready, "refilled": False}
        if want > 0:
            stats = self.discover(want, max_minutes or float(self.cfg["run"]["max_minutes"]))
            result.update(refilled=True, stats={k: v for k, v in stats.__dict__.items() if k != "samples"})
        with self.conn_factory() as conn:
            result["found_today_after"] = store.found_today(conn, tz)
        with self.conn_factory() as conn:
            result["ready_after"] = store.inventory(conn)["available_inventory"]
        return result


def outreach_ready_rows(conn, limit: int) -> list[dict]:
    """Handoff payload: every field the outreach side needs, one row per company."""
    return conn.execute(
        """
        SELECT c.company_id::text, c.company_name, c.domain, c.website, c.industry, c.city, c.state, c.phone,
               c.discovery_source AS source, c.qualification_status AS qualification_level,
               c.qualification_reason, c.personalization AS personalization_facts,
               ct.email, ct.email_status, ct.source_url AS email_source_url
        FROM companies c
        JOIN LATERAL (SELECT email, email_status, source_url FROM contacts WHERE company_id = c.company_id
                      AND email_status IN ('validated','published') ORDER BY (email_status = 'validated') DESC,
                      (role = 'generic') DESC, discovered_at LIMIT 1) ct ON true
        WHERE c.outreach_status = 'outreach_ready' AND c.first_contacted_at IS NULL AND c.active
        ORDER BY (c.qualification_status = 'HIGH') DESC, c.discovered_at
        LIMIT %s
        """, (limit,)).fetchall()
