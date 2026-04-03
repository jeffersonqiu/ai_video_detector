import logging

from google import genai
from google.genai import types

from models import DetectionResult

logger = logging.getLogger(__name__)

# Cost per 1M tokens (input, output) for each model
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash":      (0.30, 1.00),
}

# Lazy singleton — created on first API call so import never crashes
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


DETECTION_PROMPT = """\
You are an expert at detecting AI-generated video content. Analyse these video frames carefully.

Look for these AI generation indicators:
- Unnatural skin texture, waxy or overly smooth appearance
- Face morphing, blending, or flickering between frames
- Inconsistent lighting direction or shadows that don't match the scene
- Background elements that are blurred, distorted, or geometrically wrong
- Hair, teeth, or fine details that look artificial or inconsistent
- Eye reflections that don't match light sources
- Unnatural motion or movement that looks interpolated
- Text in the video that is garbled, misspelled, or morphing
- Hands with wrong number of fingers or distorted joints
- Any general uncanny valley quality

Respond using EXACTLY this format and nothing else:
VERDICT: [AI GENERATED / LIKELY REAL / UNCERTAIN]
CONFIDENCE: [HIGH / MEDIUM / LOW]
REASON: [One sentence explaining the strongest signal you detected]\
"""


def _parse_verdict(text: str) -> DetectionResult:
    verdict = confidence = reason = None
    for line in text.strip().splitlines():
        if line.startswith("VERDICT:"):
            verdict = line.replace("VERDICT:", "").strip()
        elif line.startswith("CONFIDENCE:"):
            confidence = line.replace("CONFIDENCE:", "").strip()
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    if not all([verdict, confidence, reason]):
        logger.warning(f"Could not fully parse Gemini response: {text!r}")
        return DetectionResult(
            verdict="UNCERTAIN",
            confidence="LOW",
            reason="Could not parse model response clearly.",
            raw_response=text,
        )

    return DetectionResult(
        verdict=verdict,
        confidence=confidence,
        reason=reason,
        raw_response=text,
    )


async def _call_model(
    parts: list[types.Part], model: str
) -> tuple[DetectionResult, int, int]:
    """
    Call a Gemini model with the given parts.
    Returns (DetectionResult, input_tokens, output_tokens).
    """
    response = await _get_client().aio.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            max_output_tokens=300,
            temperature=0.1,
        ),
    )

    text = response.text  # None if safety-filtered or no text candidate
    if text is None:
        logger.warning(f"Model {model} returned no text (safety filter or empty response).")
        result = DetectionResult(
            verdict="UNCERTAIN",
            confidence="LOW",
            reason="Model returned no response — video may have triggered a content filter.",
            raw_response=None,
        )
    else:
        result = _parse_verdict(text)

    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    output_tokens = (usage.candidates_token_count or 0) if usage else 0

    return result, input_tokens, output_tokens


async def detect_ai_video(frame_paths: list[str]) -> DetectionResult:
    """
    Sends frames to Gemini for AI detection.

    Uses gemini-2.5-flash-lite first. If confidence is LOW, automatically
    escalates to gemini-2.5-flash for a second opinion.

    Returns DetectionResult with verdict, confidence, reason, model used, and cost.
    """
    parts: list[types.Part] = [types.Part.from_text(text=DETECTION_PROMPT)]
    for frame_path in frame_paths:
        with open(frame_path, "rb") as f:
            image_bytes = f.read()
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    # First pass: cheap fast model
    model = "gemini-2.5-flash-lite"
    result, input_tokens, output_tokens = await _call_model(parts, model)
    logger.info(f"{model}: verdict={result.verdict} confidence={result.confidence} tokens={input_tokens}+{output_tokens}")

    # Escalate to full Flash if confidence is low
    if result.confidence == "LOW":
        logger.info("Low confidence — escalating to gemini-2.5-flash.")
        model = "gemini-2.5-flash"
        result, input_tokens, output_tokens = await _call_model(parts, model)
        logger.info(f"{model}: verdict={result.verdict} confidence={result.confidence} tokens={input_tokens}+{output_tokens}")

    input_cost, output_cost = _MODEL_COSTS[model]
    cost_usd = (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000

    return result.model_copy(update={
        "model_used": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    })
