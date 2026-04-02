import logging

from google import genai
from google.genai import types

from models import DetectionResult

logger = logging.getLogger(__name__)

# Initialised once at module load; reads GEMINI_API_KEY from environment automatically
client = genai.Client()

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


async def detect_ai_video(frame_paths: list[str]) -> DetectionResult:
    """
    Sends extracted frames to Gemini 2.5 Flash-Lite for AI generation detection.

    Returns a DetectionResult with verdict, confidence, and reason.
    Raises DetectionError if the Gemini API call fails.
    """
    parts: list[types.Part] = [types.Part.from_text(text=DETECTION_PROMPT)]

    for frame_path in frame_paths:
        with open(frame_path, "rb") as f:
            image_bytes = f.read()
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=parts,
        config=types.GenerateContentConfig(
            max_output_tokens=300,
            temperature=0.1,
        ),
    )

    logger.info(f"Gemini response received, parsing verdict.")
    return _parse_verdict(response.text)
