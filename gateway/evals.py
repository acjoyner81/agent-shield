"""Eval judge for quality scoring."""

import json
import logging
import time
from typing import Dict, Any

logger = logging.getLogger(__name__)


def evaluate_response(response_text: str, prompt: str, model: str) -> Dict[str, Any]:
    """Rule based evaluation of LLM responses."""
    score = 0.0
    reasons = []
    
    if not response_text:
        reasons.append("Empty response")
        return {"eval_passed": False, "score": 0.0, "reason": "; ".join(reasons)}
    
    # Length check
    if len(response_text.strip()) > 10:
        score += 0.3
    else:
        reasons.append("Response too short")
    
    # Not just echoing prompt
    if response_text.strip() != prompt.strip():
        score += 0.3
    else:
        reasons.append("Response is just echoing prompt")
    
    # Contains actual content
    if any(word in response_text.lower() for word in ["the", "a", "is", "to", "and", "for", "that"]):
        score += 0.2
    else:
        reasons.append("Lacks meaningful content")
    
    # Reasonable length
    if 20 < len(response_text.strip()) < 500:
        score += 0.2
    elif len(response_text.strip()) >= 500:
        score += 0.15
    else:
        reasons.append("Unusual length")
    
    eval_passed = score >= 0.6
    
    if not reasons:
        if eval_passed:
            reasons.append("Response is well formed and relevant")
        else:
            reasons.append("Response quality below threshold")
    
    return {
        "eval_passed": eval_passed,
        "score": round(score, 2),
        "reason": "; ".join(reasons) if reasons else "Evaluated"
    }


def store_eval_result(redis_client, tenant_id: str, run_id: str, eval_result: Dict[str, Any]):
    """Store evaluation result in Redis."""
    eval_data = {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "eval_passed": eval_result["eval_passed"],
        "score": eval_result["score"],
        "reason": eval_result["reason"],
        "timestamp": time.time(),
    }
    redis_client.lpush(f"evals:{tenant_id}", json.dumps(eval_data))
    redis_client.ltrim(f"evals:{tenant_id}", 0, 49)
    redis_client.setex(f"eval:{run_id}", 3600, json.dumps(eval_data))
    return eval_data
