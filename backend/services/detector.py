import base64
import logging
import os

import anthropic
from google import genai
from google.genai import types

from models import DetectionResult

logger = logging.getLogger(__name__)

# Cost per 1M tokens: (input, output)
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite":       (0.10, 0.40),
    "gemini-2.5-flash":            (0.30, 1.00),
    "claude-haiku-4-5-20251001":   (0.80, 4.00),
}

# Disable configurable safety filters — the forensic detection prompt combined
# with human faces trips Gemini's filters on normal lifestyle/beauty videos.
# Non-configurable model-level refusals still apply; Claude handles those cases.
_SAFETY_SETTINGS = [
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",        threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
]

# Lazy singletons
_gemini_client: genai.Client | None = None
_claude_client: anthropic.AsyncAnthropic | None = None


def _get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client()
    return _gemini_client


def _get_claude() -> anthropic.AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.AsyncAnthropic()
    return _claude_client


_PROMPT_BASE = """\
You are an expert at detecting AI-generated video content.

{caption_section}Analyse the provided video frames{audio_section} carefully.

Look for these visual AI generation indicators:
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

{audio_indicators}Respond using EXACTLY this format and nothing else:
VERDICT: [AI GENERATED / LIKELY REAL / UNCERTAIN]
CONFIDENCE: [HIGH / MEDIUM / LOW]
REASON: [One sentence explaining the strongest signal you detected]\
"""

_AUDIO_INDICATORS = """\
Also look for these audio AI generation indicators:
- Robotic, overly smooth, or unnaturally paced speech (TTS voice)
- Voice that doesn't match the speaker's lip movements in the frames
- Unnatural pauses, breathing patterns, or transitions in speech
- Background music that sounds synthetic or AI-generated
- Audio quality inconsistent with the video quality
- Speaker mentions AI tools, editing software, or AI generation

"""


def _build_prompt(caption: str | None, has_audio: bool = False) -> str:
    if caption:
        caption_section = (
            f'VIDEO CAPTION: "{caption}"\n'
            f"Use this as additional context — if the caption explicitly mentions AI, "
            f"editing tools, or deepfakes, weight that heavily in your verdict.\n\n"
        )
    else:
        caption_section = ""

    audio_section = " and audio" if has_audio else ""
    audio_indicators = _AUDIO_INDICATORS if has_audio else ""

    return _PROMPT_BASE.format(
        caption_section=caption_section,
        audio_section=audio_section,
        audio_indicators=audio_indicators,
    )


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
        logger.warning(f"Could not fully parse response: {text!r}")
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


def _build_gemini_config(model: str) -> types.GenerateContentConfig:
    extra = {}
    if model == "gemini-2.5-flash":
        extra["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(
        max_output_tokens=300,
        temperature=0.1,
        safety_settings=_SAFETY_SETTINGS,
        **extra,
    )


def _finish_reason(response) -> str:
    try:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            return f"PROMPT_BLOCKED({response.prompt_feedback.block_reason})"
        return str(response.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return "UNKNOWN"


async def _call_gemini(
    image_parts: list[types.Part],
    model: str,
    caption: str | None = None,
    audio_path: str | None = None,
) -> tuple[DetectionResult | None, int, int]:
    """
    Call a Gemini model. Returns (None, 0, 0) when the response is blocked
    so the caller can escalate to the next model.

    Part order: [text prompt] [audio?] [images...]
    Gemini supports audio as inline bytes (MP3 → audio/mpeg).
    """
    has_audio = audio_path is not None
    parts: list[types.Part] = [types.Part.from_text(text=_build_prompt(caption, has_audio))]

    if audio_path:
        with open(audio_path, "rb") as f:
            parts.append(types.Part.from_bytes(data=f.read(), mime_type="audio/mpeg"))

    parts.extend(image_parts)

    response = await _get_gemini().aio.models.generate_content(
        model=model,
        contents=parts,
        config=_build_gemini_config(model),
    )

    text = response.text
    if text is None:
        reason = _finish_reason(response)
        logger.warning(f"Gemini {model} blocked. reason={reason}")
        return None, 0, 0

    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    output_tokens = (usage.candidates_token_count or 0) if usage else 0
    return _parse_verdict(text), input_tokens, output_tokens


async def _call_claude(
    frame_paths: list[str],
    caption: str | None = None,
    has_audio: bool = False,
) -> tuple[DetectionResult, int, int]:
    """
    Call Claude claude-haiku as a fallback when Gemini blocks.
    Claude does not support audio input — analysis is frames + caption only.
    """
    model = "claude-haiku-4-5-20251001"
    # Claude doesn't support audio; note this in the prompt so it doesn't hallucinate
    prompt = _build_prompt(caption, has_audio=False)
    if has_audio:
        prompt += "\nNote: Audio analysis is not available for this response — base your verdict on visual frames and caption only."
    content: list[dict] = [{"type": "text", "text": prompt}]

    for frame_path in frame_paths:
        with open(frame_path, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
        })

    response = await _get_claude().messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text if response.content else None
    if not text:
        return DetectionResult(
            verdict="UNCERTAIN",
            confidence="LOW",
            reason="Claude returned no response.",
            raw_response=None,
        ), 0, 0

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    return _parse_verdict(text), input_tokens, output_tokens


async def detect_ai_video(
    frame_paths: list[str],
    caption: str | None = None,
    video_path: str | None = None,
) -> DetectionResult:
    """
    Analysis pipeline with three-tier model escalation:
      1. gemini-2.5-flash-lite  — fast and cheap
      2. gemini-2.5-flash       — if confidence is LOW
      3. claude-haiku-4-5       — if Gemini blocks (model-level refusal)

    caption is injected into the prompt.
    video_path is used to extract audio; Gemini receives audio inline.
    Claude does not support audio — it uses frames + caption only.
    """
    from services.audio_extractor import extract_audio_async

    if caption:
        logger.info(f"Caption provided: {caption[:80]!r}")

    # Extract audio if video path provided
    audio_path: str | None = None
    if video_path:
        output_dir = os.path.dirname(frame_paths[0])
        try:
            audio_path = await extract_audio_async(video_path, output_dir)
        except Exception:
            logger.warning("Audio extraction failed — continuing without audio.", exc_info=True)

    has_audio = audio_path is not None

    # Build Gemini image-only parts (prompt + audio injected per call)
    image_parts: list[types.Part] = []
    for frame_path in frame_paths:
        with open(frame_path, "rb") as f:
            image_parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/jpeg"))

    # Tier 1: Flash-Lite
    model = "gemini-2.5-flash-lite"
    result, input_tokens, output_tokens = await _call_gemini(image_parts, model, caption, audio_path)
    if result:
        logger.info(f"{model}: verdict={result.verdict} confidence={result.confidence} tokens={input_tokens}+{output_tokens}")

    # Tier 2: Flash (if low confidence)
    if result and result.confidence == "LOW":
        logger.info("Low confidence — escalating to gemini-2.5-flash.")
        model = "gemini-2.5-flash"
        result, input_tokens, output_tokens = await _call_gemini(image_parts, model, caption, audio_path)
        if result:
            logger.info(f"{model}: verdict={result.verdict} confidence={result.confidence} tokens={input_tokens}+{output_tokens}")

    # Tier 3: Claude (if Gemini blocked at either tier)
    if result is None:
        logger.info("Gemini blocked — falling back to Claude claude-haiku.")
        model = "claude-haiku-4-5-20251001"
        result, input_tokens, output_tokens = await _call_claude(frame_paths, caption, has_audio)
        logger.info(f"{model}: verdict={result.verdict} confidence={result.confidence} tokens={input_tokens}+{output_tokens}")

    input_cost, output_cost = _MODEL_COSTS.get(model, (0.30, 1.00))
    cost_usd = (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000

    return result.model_copy(update={
        "model_used": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    })
