from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..models import Job, JobStatus


class PostgresJobStore:
    """Durable, idempotent history on the existing Cloud AI OS database."""

    def __init__(self, conn: Any):
        self.conn = conn

    def upsert_discovered(self, job: Job) -> tuple[dict, bool]:
        row = job.to_dict()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_applications
                  (job_id, company, title, location, category, description_hash, job_url,
                   application_url, ats, ats_job_id, requisition_id, salary, discovered_at,
                   job_score, score_detail, application_complexity, resume_variant,
                   tailored_resume_path, status, skip_reason, metadata)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (job_id) DO UPDATE SET
                  job_url=EXCLUDED.job_url, application_url=EXCLUDED.application_url,
                  salary=EXCLUDED.salary, job_score=EXCLUDED.job_score,
                  score_detail=EXCLUDED.score_detail, metadata=EXCLUDED.metadata,
                  updated_at=now()
                RETURNING *, (xmax = 0) AS inserted
                """,
                (row["job_id"], job.company, job.title, job.location, job.category,
                 row["description_hash"], job.job_url, job.application_url, job.ats,
                 job.ats_job_id, job.requisition_id, job.salary, job.discovered_at,
                 job.job_score, json.dumps(job.score_detail), job.application_complexity,
                 job.resume_variant, job.tailored_resume_path, job.status.value,
                 job.skip_reason, json.dumps(job.metadata)),
            )
            saved = cur.fetchone()
        self.conn.commit()
        return saved, bool(saved.get("inserted"))

    def transition(self, job_id: str, status: JobStatus | str, **fields: Any) -> dict:
        allowed = {"skip_reason", "applied_at", "confirmation_id", "confirmation_text", "last_attempt", "attempt_count", "application_complexity", "resume_variant", "tailored_resume_path", "job_score", "score_detail"}
        status_value = status.value if isinstance(status, JobStatus) else str(status)
        changes = {k: v for k, v in fields.items() if k in allowed}
        assignments = ["status = %s", "updated_at = now()"]
        params: list[Any] = [status_value]
        for key, value in changes.items():
            assignments.append(f"{key} = %s")
            params.append(json.dumps(value) if key == "score_detail" else value)
        params.append(job_id)
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE job_applications SET {', '.join(assignments)} WHERE job_id = %s RETURNING *", params)
            row = cur.fetchone()
        self.conn.commit()
        if row is None:
            raise KeyError(job_id)
        return row

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM job_applications"
        params: list[Any] = []
        if status:
            sql += " WHERE status = %s"
            params.append(status)
        sql += " ORDER BY job_score DESC, discovered_at DESC LIMIT %s"
        params.append(limit)
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() or []

    def daily_counts(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT status, coalesce(skip_reason, '') AS skip_reason, count(*) AS n
                FROM job_applications
                WHERE updated_at >= date_trunc('day', now())
                GROUP BY status, coalesce(skip_reason, '')
            """)
            rows = cur.fetchall() or []
        result: dict[str, int] = {}
        for row in rows:
            result[str(row["status"])] = result.get(str(row["status"]), 0) + int(row["n"])
            if row.get("skip_reason"):
                result[f"skip:{row['skip_reason']}"] = int(row["n"])
        return result

    def qualified(self, limit: int) -> list[Job]:
        return [Job.from_mapping(dict(row)) for row in self.list(JobStatus.QUALIFIED.value, limit)]

    def start_run(self, mode: str, dry_run: bool) -> str:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO job_agent_runs (mode, dry_run) VALUES (%s, %s) RETURNING id", (mode, dry_run))
            run_id = str(cur.fetchone()["id"])
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, counts: dict[str, int], errors: list[dict]) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE job_agent_runs SET finished_at=now(), counts=%s::jsonb, errors=%s::jsonb WHERE id=%s", (json.dumps(counts), json.dumps(errors), run_id))
        self.conn.commit()
