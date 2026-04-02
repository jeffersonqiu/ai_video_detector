from pydantic import BaseModel
from typing import Optional


class DetectionResult(BaseModel):
    verdict: str        # "AI GENERATED" | "LIKELY REAL" | "UNCERTAIN"
    confidence: str     # "HIGH" | "MEDIUM" | "LOW"
    reason: str         # one-sentence explanation
    raw_response: Optional[str] = None  # full model output for debugging


class AnalysisRequest(BaseModel):
    url: str
    chat_id: int
