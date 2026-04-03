from typing import Optional

from pydantic import BaseModel


class DetectionResult(BaseModel):
    verdict: str            # "AI GENERATED" | "LIKELY REAL" | "UNCERTAIN"
    confidence: str         # "HIGH" | "MEDIUM" | "LOW"
    reason: str             # one-sentence explanation
    raw_response: Optional[str] = None
    model_used: str = "gemini-2.5-flash-lite"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class AnalysisRequest(BaseModel):
    url: str
    chat_id: int
