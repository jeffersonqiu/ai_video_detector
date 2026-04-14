"""
Cost-optimization research script — Gemini model sweep.

Tests 5 strategies to find the cheapest Gemini model/config that correctly
identifies the Ferrari Koi Pond reel as AI GENERATED with MEDIUM+ confidence.
Claude Sonnet 4.6 is the baseline reference (known to work).

Strategies tested (all use 8 frames + caption + audio where supported):
  1. claude-sonnet-4-6         — baseline reference, no audio
  2. gemini-2.5-flash          thinking OFF  + audio
  3. gemini-2.5-flash          thinking ON (budget=8000) + audio
  4. gemini-2.5-pro            thinking OFF  + audio
  5. gemini-2.5-pro            thinking ON (budget=8000) + audio
"""

import asyncio
import base64
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import anthropic
from google import genai
from google.genai import types

from services.downloader import download_video
from services.frame_extractor import extract_frames
from services.audio_extractor import extract_audio_async
from services.cleanup import cleanup_job
from services.detector import (
    _build_prompt,
    _SIMPLE_PROMPT_SUFFIX,
    _parse_verdict,
    _SAFETY_SETTINGS,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("research")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_URL = "https://www.instagram.com/reel/DVmPqQhETyD/?igsh=aWZ5bHg1cTZmZWNq"

# Cost per 1M tokens: (input_price, output_price)
# Thinking tokens always billed at $3.50/1M for Gemini
COSTS = {
    "claude-sonnet-4-6":   (3.00, 15.00),
    "gemini-2.5-flash":    (0.30,  1.00),
    "gemini-2.5-pro":      (1.25, 10.00),
}
THINKING_COST_PER_1M = 3.50


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class StrategyResult:
    strategy_num: int
    name: str
    model: str
    thinking: bool
    audio: bool
    verdict: Optional[str] = None
    confidence: Optional[str] = None
    reason: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0   # Gemini only
    cost_usd: float = 0.0
    success: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_cost(model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float:
    ip, op = COSTS.get(model, (0.0, 0.0))
    return (input_tokens * ip + output_tokens * op + thinking_tokens * THINKING_COST_PER_1M) / 1_000_000


def is_success(verdict: Optional[str], confidence: Optional[str]) -> bool:
    return (
        verdict is not None
        and confidence is not None
        and verdict.upper() == "AI GENERATED"
        and confidence.upper() in ("MEDIUM", "HIGH")
    )


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_gemini_client: Optional[genai.Client] = None
_claude_client: Optional[anthropic.AsyncAnthropic] = None


def get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client()
    return _gemini_client


def get_claude() -> anthropic.AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.AsyncAnthropic()
    return _claude_client


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

async def call_gemini(
    model: str,
    thinking_budget: int,   # 0 = OFF, >0 = ON
    caption: Optional[str],
    frame_paths: list[str],
    audio_path: Optional[str] = None,
) -> tuple[Optional[object], int, int, int]:
    """
    Returns (result, input_tokens, output_tokens, thinking_tokens).
    """
    has_audio = audio_path is not None
    prompt = _build_prompt(caption, has_audio) + _SIMPLE_PROMPT_SUFFIX

    parts: list[types.Part] = [types.Part.from_text(text=prompt)]

    if audio_path:
        with open(audio_path, "rb") as f:
            parts.append(types.Part.from_bytes(data=f.read(), mime_type="audio/mpeg"))

    for fp in frame_paths:
        with open(fp, "rb") as f:
            parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/jpeg"))

    config = types.GenerateContentConfig(
        max_output_tokens=1024,
        temperature=0.1,
        safety_settings=_SAFETY_SETTINGS,
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
    )

    response = await get_gemini().aio.models.generate_content(
        model=model,
        contents=parts,
        config=config,
    )

    text = response.text
    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    output_tokens = (usage.candidates_token_count or 0) if usage else 0
    thinking_tokens = (usage.thoughts_token_count or 0) if usage else 0

    if text is None:
        logger.warning(f"Gemini {model} (thinking_budget={thinking_budget}) returned no text.")
        return None, input_tokens, output_tokens, thinking_tokens

    result = _parse_verdict(text)
    if result is None:
        # Retry with extraction prompt
        retry_parts = [types.Part.from_text(
            "From the analysis below, extract and output ONLY these three lines:\n"
            "VERDICT: [AI GENERATED / LIKELY REAL / UNCERTAIN]\n"
            "CONFIDENCE: [HIGH / MEDIUM / LOW]\n"
            "REASON: [one sentence]\n\n"
            f"Analysis:\n{text}"
        )]
        retry_config = types.GenerateContentConfig(
            max_output_tokens=300,
            temperature=0.1,
            safety_settings=_SAFETY_SETTINGS,
            thinking_config=types.ThinkingConfig(thinking_budget=0),  # no thinking on retry
        )
        retry_resp = await get_gemini().aio.models.generate_content(
            model=model,
            contents=retry_parts,
            config=retry_config,
        )
        retry_text = retry_resp.text or ""
        ru = retry_resp.usage_metadata
        input_tokens += (ru.prompt_token_count or 0) if ru else 0
        output_tokens += (ru.candidates_token_count or 0) if ru else 0
        thinking_tokens += (ru.thoughts_token_count or 0) if ru else 0
        result = _parse_verdict(retry_text)

    return result, input_tokens, output_tokens, thinking_tokens


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

async def call_claude(
    model: str,
    caption: Optional[str],
    frame_paths: list[str],
) -> tuple[Optional[object], int, int]:
    """
    Returns (result, input_tokens, output_tokens).
    Claude does not support audio inline — frames only.
    """
    prompt = _build_prompt(caption, has_audio=False) + _SIMPLE_PROMPT_SUFFIX
    content: list[dict] = [{"type": "text", "text": prompt}]

    for fp in frame_paths:
        with open(fp, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
        })

    response = await get_claude().messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text if response.content else None
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    if not text:
        return None, input_tokens, output_tokens

    result = _parse_verdict(text)
    if result is None:
        retry_response = await get_claude().messages.create(
            model=model,
            max_tokens=200,
            messages=[
                {"role": "user", "content": content},
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    "Output ONLY the three verdict lines from your analysis above:\n"
                    "VERDICT: [AI GENERATED / LIKELY REAL / UNCERTAIN]\n"
                    "CONFIDENCE: [HIGH / MEDIUM / LOW]\n"
                    "REASON: [one sentence]"
                )},
            ],
        )
        retry_text = retry_response.content[0].text if retry_response.content else ""
        input_tokens += retry_response.usage.input_tokens
        output_tokens += retry_response.usage.output_tokens
        result = _parse_verdict(retry_text)

    return result, input_tokens, output_tokens


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 75)
    print("AI VIDEO DETECTOR — GEMINI MODEL SWEEP")
    print("=" * 75)
    print(f"Video URL: {VIDEO_URL}")
    print()

    # ------------------------------------------------------------------
    # Step 1: Download video
    # ------------------------------------------------------------------
    print(">>> Step 1: Downloading video...")
    job_id = str(uuid.uuid4())
    job_dir = f"/tmp/research_{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    # Handle Instagram cookies
    cookies_path: Optional[str] = None
    ig_cookies_b64 = os.environ.get("INSTAGRAM_COOKIES_B64")
    if ig_cookies_b64:
        cookies_path = os.path.join(job_dir, "instagram_cookies.txt")
        with open(cookies_path, "wb") as f:
            f.write(base64.b64decode(ig_cookies_b64))
        print(f"    Decoded INSTAGRAM_COOKIES_B64 → {cookies_path}")
    else:
        local_cookies = os.path.join(os.path.dirname(__file__), "instagram_cookies.txt")
        if os.path.exists(local_cookies):
            cookies_path = local_cookies
            print(f"    Using local cookies file: {cookies_path}")
        else:
            print("    No Instagram cookies found — attempting unauthenticated download.")

    from config import settings
    if cookies_path:
        settings.instagram_cookies_file = cookies_path

    try:
        filepath, video_info = await download_video(VIDEO_URL, job_dir)
        caption = video_info.description or None
        print(f"    Downloaded: {filepath}")
        print(f"    Uploader:   {video_info.uploader}")
        print(f"    Duration:   {video_info.duration_seconds}s")
        print(f"    Caption:    {caption!r}")
    except Exception as e:
        print(f"    ERROR downloading video: {e}")
        cleanup_job(job_dir)
        return

    # ------------------------------------------------------------------
    # Step 2: Extract 8 frames
    # ------------------------------------------------------------------
    print()
    print(">>> Step 2: Extracting 8 frames...")
    try:
        frames = extract_frames(filepath, job_dir, num_frames=8)
        print(f"    Extracted: {[os.path.basename(f) for f in frames]}")
    except Exception as e:
        print(f"    ERROR extracting frames: {e}")
        cleanup_job(job_dir)
        return

    # ------------------------------------------------------------------
    # Step 3: Extract audio
    # ------------------------------------------------------------------
    print()
    print(">>> Step 3: Extracting audio...")
    audio_path: Optional[str] = None
    try:
        audio_path = await extract_audio_async(filepath, job_dir)
        if audio_path:
            size_kb = os.path.getsize(audio_path) / 1024
            print(f"    Audio: {audio_path} ({size_kb:.1f} KB)")
        else:
            print("    No audio track found.")
    except Exception as e:
        print(f"    WARNING: Audio extraction failed: {e}")
        audio_path = None

    # ------------------------------------------------------------------
    # Step 4: Run strategies
    # ------------------------------------------------------------------
    print()
    print("=" * 75)
    print("RUNNING STRATEGIES")
    print("=" * 75)

    strategies: list[StrategyResult] = []

    # --- Strategy 1: Claude Sonnet 4.6 (baseline reference) ---
    print()
    print(">>> Strategy 1: claude-sonnet-4-6 | thinking=N/A | audio=No  [BASELINE]")
    r1 = StrategyResult(
        strategy_num=1,
        name="Claude Sonnet 4.6 (baseline)",
        model="claude-sonnet-4-6",
        thinking=False,
        audio=False,
    )
    try:
        result1, r1.input_tokens, r1.output_tokens = await call_claude(
            "claude-sonnet-4-6", caption, frames
        )
        if result1:
            r1.verdict = result1.verdict
            r1.confidence = result1.confidence
            r1.reason = result1.reason
        else:
            r1.verdict = "UNCERTAIN"
            r1.confidence = "LOW"
            r1.reason = "Could not parse model response."
        r1.cost_usd = compute_cost("claude-sonnet-4-6", r1.input_tokens, r1.output_tokens)
        r1.success = is_success(r1.verdict, r1.confidence)
    except Exception as e:
        r1.error = str(e)
        print(f"    ERROR: {e}")
    print(f"    Verdict: {r1.verdict} | Confidence: {r1.confidence}")
    print(f"    Tokens: {r1.input_tokens} in / {r1.output_tokens} out")
    print(f"    Cost: ${r1.cost_usd:.6f} | SUCCESS: {r1.success}")
    if r1.reason:
        print(f"    Reason: {r1.reason}")
    strategies.append(r1)

    # --- Strategy 2: gemini-2.5-flash, thinking OFF, audio YES ---
    print()
    print(">>> Strategy 2: gemini-2.5-flash | thinking=OFF | audio=Yes")
    r2 = StrategyResult(
        strategy_num=2,
        name="Flash thinking=OFF + audio",
        model="gemini-2.5-flash",
        thinking=False,
        audio=True,
    )
    try:
        result2, r2.input_tokens, r2.output_tokens, r2.thinking_tokens = await call_gemini(
            "gemini-2.5-flash", 0, caption, frames, audio_path=audio_path
        )
        if result2:
            r2.verdict = result2.verdict
            r2.confidence = result2.confidence
            r2.reason = result2.reason
        else:
            r2.verdict = "UNCERTAIN"
            r2.confidence = "LOW"
            r2.reason = "Could not parse model response."
        r2.cost_usd = compute_cost("gemini-2.5-flash", r2.input_tokens, r2.output_tokens, r2.thinking_tokens)
        r2.success = is_success(r2.verdict, r2.confidence)
    except Exception as e:
        r2.error = str(e)
        print(f"    ERROR: {e}")
    print(f"    Verdict: {r2.verdict} | Confidence: {r2.confidence}")
    print(f"    Tokens: {r2.input_tokens} in / {r2.output_tokens} out / {r2.thinking_tokens} thinking")
    print(f"    Cost: ${r2.cost_usd:.6f} | SUCCESS: {r2.success}")
    if r2.reason:
        print(f"    Reason: {r2.reason}")
    strategies.append(r2)

    # --- Strategy 3: gemini-2.5-flash, thinking ON (budget=8000), audio YES ---
    print()
    print(">>> Strategy 3: gemini-2.5-flash | thinking=ON (budget=8000) | audio=Yes")
    r3 = StrategyResult(
        strategy_num=3,
        name="Flash thinking=ON(8k) + audio",
        model="gemini-2.5-flash",
        thinking=True,
        audio=True,
    )
    try:
        result3, r3.input_tokens, r3.output_tokens, r3.thinking_tokens = await call_gemini(
            "gemini-2.5-flash", 8000, caption, frames, audio_path=audio_path
        )
        if result3:
            r3.verdict = result3.verdict
            r3.confidence = result3.confidence
            r3.reason = result3.reason
        else:
            r3.verdict = "UNCERTAIN"
            r3.confidence = "LOW"
            r3.reason = "Could not parse model response."
        r3.cost_usd = compute_cost("gemini-2.5-flash", r3.input_tokens, r3.output_tokens, r3.thinking_tokens)
        r3.success = is_success(r3.verdict, r3.confidence)
    except Exception as e:
        r3.error = str(e)
        print(f"    ERROR: {e}")
    print(f"    Verdict: {r3.verdict} | Confidence: {r3.confidence}")
    print(f"    Tokens: {r3.input_tokens} in / {r3.output_tokens} out / {r3.thinking_tokens} thinking")
    print(f"    Cost: ${r3.cost_usd:.6f} | SUCCESS: {r3.success}")
    if r3.reason:
        print(f"    Reason: {r3.reason}")
    strategies.append(r3)

    # --- Strategy 4: gemini-2.5-pro, thinking OFF, audio YES ---
    print()
    print(">>> Strategy 4: gemini-2.5-pro | thinking=OFF | audio=Yes")
    r4 = StrategyResult(
        strategy_num=4,
        name="Pro thinking=OFF + audio",
        model="gemini-2.5-pro",
        thinking=False,
        audio=True,
    )
    try:
        result4, r4.input_tokens, r4.output_tokens, r4.thinking_tokens = await call_gemini(
            "gemini-2.5-pro", 0, caption, frames, audio_path=audio_path
        )
        if result4:
            r4.verdict = result4.verdict
            r4.confidence = result4.confidence
            r4.reason = result4.reason
        else:
            r4.verdict = "UNCERTAIN"
            r4.confidence = "LOW"
            r4.reason = "Could not parse model response."
        r4.cost_usd = compute_cost("gemini-2.5-pro", r4.input_tokens, r4.output_tokens, r4.thinking_tokens)
        r4.success = is_success(r4.verdict, r4.confidence)
    except Exception as e:
        r4.error = str(e)
        print(f"    ERROR: {e}")
    print(f"    Verdict: {r4.verdict} | Confidence: {r4.confidence}")
    print(f"    Tokens: {r4.input_tokens} in / {r4.output_tokens} out / {r4.thinking_tokens} thinking")
    print(f"    Cost: ${r4.cost_usd:.6f} | SUCCESS: {r4.success}")
    if r4.reason:
        print(f"    Reason: {r4.reason}")
    strategies.append(r4)

    # --- Strategy 5: gemini-2.5-pro, thinking ON (budget=8000), audio YES ---
    print()
    print(">>> Strategy 5: gemini-2.5-pro | thinking=ON (budget=8000) | audio=Yes")
    r5 = StrategyResult(
        strategy_num=5,
        name="Pro thinking=ON(8k) + audio",
        model="gemini-2.5-pro",
        thinking=True,
        audio=True,
    )
    try:
        result5, r5.input_tokens, r5.output_tokens, r5.thinking_tokens = await call_gemini(
            "gemini-2.5-pro", 8000, caption, frames, audio_path=audio_path
        )
        if result5:
            r5.verdict = result5.verdict
            r5.confidence = result5.confidence
            r5.reason = result5.reason
        else:
            r5.verdict = "UNCERTAIN"
            r5.confidence = "LOW"
            r5.reason = "Could not parse model response."
        r5.cost_usd = compute_cost("gemini-2.5-pro", r5.input_tokens, r5.output_tokens, r5.thinking_tokens)
        r5.success = is_success(r5.verdict, r5.confidence)
    except Exception as e:
        r5.error = str(e)
        print(f"    ERROR: {e}")
    print(f"    Verdict: {r5.verdict} | Confidence: {r5.confidence}")
    print(f"    Tokens: {r5.input_tokens} in / {r5.output_tokens} out / {r5.thinking_tokens} thinking")
    print(f"    Cost: ${r5.cost_usd:.6f} | SUCCESS: {r5.success}")
    if r5.reason:
        print(f"    Reason: {r5.reason}")
    strategies.append(r5)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    cleanup_job(job_dir)

    # ------------------------------------------------------------------
    # Final report
    # ------------------------------------------------------------------
    print()
    print("=" * 75)
    print("FULL RESULTS TABLE")
    print("=" * 75)
    header = (
        f"{'#':<3} {'Strategy':<34} {'Model':<20} {'Think':<6} {'Au':<3} "
        f"{'Verdict':<16} {'Conf':<7} {'In':>7} {'Out':>6} {'Think':>7} {'Cost USD':>11} {'OK':>4}"
    )
    print(header)
    print("-" * len(header))

    total_cost = 0.0
    sonnet_cost = 0.0
    cheapest_gemini_success: Optional[StrategyResult] = None

    for r in strategies:
        model_short = r.model[:20]
        verdict_short = (r.verdict or "—")[:16]
        conf_short = (r.confidence or "—")[:7]
        ok = "YES" if r.success else "no"
        err_note = f"  [ERR: {r.error[:50]}]" if r.error else ""
        thinking_label = "ON" if r.thinking else "OFF"
        print(
            f"{r.strategy_num:<3} {r.name:<34} {model_short:<20} {thinking_label:<6} "
            f"{'Y' if r.audio else 'N':<3} {verdict_short:<16} {conf_short:<7} "
            f"{r.input_tokens:>7} {r.output_tokens:>6} {r.thinking_tokens:>7} "
            f"${r.cost_usd:>10.6f} {ok:>4}"
            + err_note
        )
        total_cost += r.cost_usd
        if r.strategy_num == 1:
            sonnet_cost = r.cost_usd
        if r.success and r.model.startswith("gemini") and cheapest_gemini_success is None:
            cheapest_gemini_success = r

    print()
    print("=" * 75)
    print("SUMMARY")
    print("=" * 75)

    print(f"Baseline (Sonnet 4.6):  cost=${sonnet_cost:.6f}  success={strategies[0].success}")
    print()

    if cheapest_gemini_success:
        cs = cheapest_gemini_success
        savings_pct = (1 - cs.cost_usd / sonnet_cost) * 100 if sonnet_cost > 0 else 0
        print(f"Cheapest successful Gemini: #{cs.strategy_num} — {cs.name}")
        print(f"  Model:      {cs.model}")
        print(f"  Thinking:   {'ON' if cs.thinking else 'OFF'}")
        print(f"  Verdict:    {cs.verdict} | Confidence: {cs.confidence}")
        print(f"  Reason:     {cs.reason}")
        print(f"  Cost/video: ${cs.cost_usd:.6f}")
        print(f"  Tokens:     {cs.input_tokens} in / {cs.output_tokens} out / {cs.thinking_tokens} thinking")
        print()
        print(f"  Cost vs Sonnet:   ${cs.cost_usd:.6f} vs ${sonnet_cost:.6f}  ({savings_pct:.1f}% cheaper)")
        print()
        print(f"  Projected @ 50 analyses/month:  ${cs.cost_usd * 50:.4f}")
        print(f"  Projected @ 500 analyses/month: ${cs.cost_usd * 500:.4f}")
        print()
        print(f"  RECOMMENDATION: Use strategy #{cs.strategy_num} ({cs.name}) in production.")
    else:
        print("NO Gemini strategy successfully identified the video as AI GENERATED with MEDIUM+ confidence.")
        print("Continue using Claude Sonnet 4.6.")

    print()
    print(f"Total research spend this run (all {len(strategies)} strategies): ${total_cost:.6f}")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(main())
