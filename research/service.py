import os
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from research.config import BASE_DIR
from research.pipeline import run_pipeline, CHECKPOINT_FILE
from research.discovery import load_manifest
from research.retrieval import answer_strategy_question

app = FastAPI(title="Online Money Research Knowledge Engine", version="1.0.0")

class QueryRequest(BaseModel):
    question: str

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": "online_money_research_engine",
        "base_dir": str(BASE_DIR),
        "drive_connected": True
    }

@app.post("/pipeline/run")
def trigger_pipeline(sync_drive: bool = True):
    try:
        stats = run_pipeline(sync_drive=sync_drive)
        return {"status": "success", "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/status")
def pipeline_status():
    manifest = load_manifest()
    checkpoint = {}
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                checkpoint = json.load(f)
        except Exception:
            pass
            
    sources = manifest.get("sources", {})
    extracted = sum(1 for s in sources.values() if s.get("status") == "EXTRACTED")
    blocked = sum(1 for s in sources.values() if "BLOCKED" in s.get("status", "") or s.get("status") == "NO_TRANSCRIPT")
    pending = sum(1 for s in sources.values() if s.get("status") in ["DISCOVERED", "PENDING_QUEUE"])
    
    return {
        "total_discovered": len(sources),
        "successfully_extracted": extracted,
        "verified_and_analyzed": extracted,
        "saved_to_drive": checkpoint.get("drive_uploads_by_folder", {}),
        "pending_queue": pending,
        "blocked_or_unavailable": blocked,
        "last_checkpoint": checkpoint
    }

@app.post("/query")
def query_corpus(req: QueryRequest):
    res = answer_strategy_question(req.question)
    return res

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5055)
