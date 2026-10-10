import os
import json
import re
from pathlib import Path
from research.config import BASE_DIR

EXTRACTED_DIR = BASE_DIR / "02_EXTRACTED_KNOWLEDGE"
RAW_DIR = BASE_DIR / "01_RAW_AND_TRANSCRIPTS"

def search_corpus(query: str, top_k: int = 3) -> list[dict]:
    query_tokens = [t.lower() for t in re.findall(r'\w+', query) if len(t) > 2]
    results = []
    
    if not EXTRACTED_DIR.exists():
        return results

    for jf in EXTRACTED_DIR.glob("*_knowledge.json"):
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Score match
            searchable_text = f"{data.get('title', '')} {data.get('business_model', '')} {data.get('creator_workflow', '')} {data.get('practical_reproduction_steps', '')} {data.get('revenue_mechanism', '')}".lower()
            
            score = 0
            for token in query_tokens:
                score += searchable_text.count(token)
                
            if score > 0:
                results.append({"score": score, "data": data})
        except Exception:
            pass
            
    results.sort(key=lambda x: x["score"], reverse=True)
    return [r["data"] for r in results[:top_k]]

def answer_strategy_question(question: str) -> dict:
    matches = search_corpus(question, top_k=2)
    if not matches:
        return {
            "question": question,
            "status": "NO_EVIDENCE_FOUND",
            "answer": "No direct evidence found in the processed knowledge corpus for this query.",
            "citations": []
        }
        
    primary = matches[0]
    
    citations = [
        {
            "source_id": m.get("source_id"),
            "title": m.get("title"),
            "url": m.get("url"),
            "creator": m.get("creator"),
            "business_model": m.get("business_model"),
            "claimed_earnings": m.get("claimed_earnings"),
            "evidence": m.get("earnings_evidence")
        } for m in matches
    ]
    
    answer_text = f"""Based on source evidence from **{primary.get('title')}** (by {primary.get('creator')}):

### 1. Strategy & Business Model
**Model:** {primary.get('business_model')}  
**Revenue Generation:** {primary.get('revenue_mechanism')}  
**Capital Required:** {primary.get('startup_cost_est')}  

### 2. Operational Workflow
{primary.get('creator_workflow')}

### 3. Customer Acquisition (Minimal Phone Calls)
{primary.get('customer_acquisition_strategy')}

### 4. Step-by-Step Practical Reproduction
{primary.get('practical_reproduction_steps')}

### 5. Grounding & Claims Verification
- Claimed Earnings: {primary.get('claimed_earnings')}
- Supporting Evidence: {primary.get('earnings_evidence')}
- Scrutiny & Risks: {primary.get('missing_evidence_and_scrutiny')}
"""

    return {
        "question": question,
        "status": "ANSWERED_FROM_EVIDENCE",
        "answer": answer_text,
        "citations": citations
    }
