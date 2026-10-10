import os
import sys
import json
import psycopg

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass
from datetime import datetime, timezone
from pathlib import Path
from research.config import BASE_DIR, DEFAULT_DB_URL, SUBDIRS
from research.discovery import run_discovery, load_manifest, save_manifest
from research.processor import process_single_source
from research.synthesis import build_cross_analysis_and_rankings
from research.drive_sync import sync_subfolder_to_drive

CHECKPOINT_FILE = BASE_DIR / "07_VERIFICATION_AND_LOGS" / "pipeline_checkpoint.json"

def sync_to_postgres(sources: list[dict], extractions: list[dict]):
    try:
        with psycopg.connect(DEFAULT_DB_URL) as conn:
            with conn.cursor() as cur:
                # Upsert sources
                for s in sources:
                    cur.execute("""
                        INSERT INTO research_sources (
                            source_id, platform, external_id, url, title, creator, 
                            published_at, content_hash, status, error_reason, raw_path, is_authorized
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            error_reason = EXCLUDED.error_reason,
                            raw_path = EXCLUDED.raw_path;
                    """, (
                        s.get("source_id"), s.get("platform"), s.get("external_id"),
                        s.get("url"), s.get("title"), s.get("creator"),
                        s.get("published_at"), s.get("content_hash"), s.get("status"),
                        s.get("error_reason"), s.get("raw_path"), s.get("is_authorized", True)
                    ))
                    
                # Upsert extractions
                for e in extractions:
                    cur.execute("""
                        INSERT INTO research_extractions (
                            extraction_id, source_id, business_model, revenue_mechanism,
                            startup_cost_est, resources_required, acquisition_strategy,
                            creator_workflow, claimed_earnings, earnings_evidence,
                            missing_evidence, contradictions_risks, reproduction_steps,
                            feasibility_score, personalization_fit, ai_processed
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (extraction_id) DO UPDATE SET
                            business_model = EXCLUDED.business_model,
                            revenue_mechanism = EXCLUDED.revenue_mechanism,
                            feasibility_score = EXCLUDED.feasibility_score,
                            personalization_fit = EXCLUDED.personalization_fit;
                    """, (
                        f"ext_{e.get('source_id')}", e.get("source_id"), e.get("business_model"),
                        e.get("revenue_mechanism"), e.get("startup_cost_est"), e.get("resources_required"),
                        e.get("customer_acquisition_strategy"), e.get("creator_workflow"),
                        e.get("claimed_earnings"), e.get("earnings_evidence"),
                        e.get("missing_evidence_and_scrutiny"), e.get("contradictions_and_risks"),
                        e.get("practical_reproduction_steps"), e.get("feasibility_score"),
                        e.get("personalization_fit"), False
                    ))
            conn.commit()
    except Exception as e:
        print(f"Postgres sync warning (will continue with local storage): {e}")

def run_pipeline(sync_drive: bool = True):
    print("=== [ONLINE_MONEY_RESEARCH] Pipeline Started ===")
    start_time = datetime.now(timezone.utc).isoformat()
    
    # 1. Discovery
    disc_res = run_discovery()
    manifest = load_manifest()
    sources = manifest.get("sources", {})
    
    # 2. Substantive Extraction
    processed_extractions = []
    extracted_count = 0
    blocked_count = 0
    pending_count = 0
    
    for sid, source in sources.items():
        if source.get("status") in ["DISCOVERED", "PENDING_QUEUE"]:
            print(f"Processing: {source.get('title')}...")
            knowledge = process_single_source(source)
            if knowledge:
                processed_extractions.append(knowledge)
                extracted_count += 1
            else:
                if "BLOCKED" in source.get("status", ""):
                    blocked_count += 1
                else:
                    pending_count += 1
        elif source.get("status") == "EXTRACTED":
            # Load existing extraction for synthesis
            jf = BASE_DIR / "02_EXTRACTED_KNOWLEDGE" / f"{sid}_knowledge.json"
            if jf.exists():
                try:
                    with open(jf, "r", encoding="utf-8") as f:
                        processed_extractions.append(json.load(f))
                        extracted_count += 1
                except Exception:
                    pass

    save_manifest(manifest)
    
    # 3. Cross-Source Synthesis & Opportunity Ranking
    build_cross_analysis_and_rankings(processed_extractions)
    
    # 4. Postgres Sync
    sync_to_postgres(list(sources.values()), processed_extractions)
    
    # 5. Drive Mirror Sync
    drive_results = {}
    if sync_drive:
        print("Syncing knowledge files to Google Drive...")
        for sd in SUBDIRS:
            uploaded = sync_subfolder_to_drive(sd)
            drive_results[sd] = len(uploaded)
            
    # 6. Checkpoint Stats
    stats = {
        "pipeline_run_at": start_time,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "total_discovered": len(sources),
        "successfully_extracted": extracted_count,
        "verified_and_analyzed": len(processed_extractions),
        "pending_queue": pending_count,
        "blocked_or_unavailable": blocked_count,
        "drive_uploads_by_folder": drive_results,
        "status": "HEALTHY"
    }
    
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
        
    try:
        with psycopg.connect(DEFAULT_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research_checkpoints (job_name, last_run_at, status, stats_json)
                    VALUES ('continuous_research_engine', NOW(), %s, %s::jsonb)
                    ON CONFLICT (job_name) DO UPDATE SET
                        last_run_at = NOW(),
                        status = EXCLUDED.status,
                        stats_json = EXCLUDED.stats_json;
                """, (stats["status"], json.dumps(stats)))
            conn.commit()
    except Exception as e:
        print(f"Postgres checkpoint save note: {e}")
        
    print(f"=== [ONLINE_MONEY_RESEARCH] Pipeline Run Complete: {json.dumps(stats, indent=2)} ===")
    return stats

if __name__ == "__main__":
    run_pipeline(sync_drive=True)
