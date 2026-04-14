# AI Video Detector — Full Project Spec

## Phase 1: Telegram Bot (COMPLETE — lives in `backend/`)

A private Telegram bot that detects AI-generated Instagram Reels and TikTok videos.

Pipeline: User sends link → yt-dlp downloads → ffmpeg extracts 8 JPEG frames (720px) + MP3 audio → caption from metadata → Gemini 2.5 Flash + audio analyses → Claude Sonnet 4.6 fallback if blocked/low confidence → bot replies with verdict + frame thumbnail.

Key backend files (READ-ONLY reference):
- `backend/services/detector.py` — production detection logic, prompts, caption keyword lists, parsing
- `backend/services/downloader.py` — yt-dlp wrapper with Instagram cookie fallback
- `backend/services/frame_extractor.py` — ffmpeg frame extraction (configurable `num_frames`)
- `backend/services/audio_extractor.py` — ffmpeg audio → MP3
- `backend/models.py` — `DetectionResult(verdict, confidence, reason, model_used, input_tokens, output_tokens, cost_usd)`

---

## Phase 2: Cost Optimization Loop (THIS DIRECTORY — `evaluation/`)

An autoresearch-style optimization loop inspired by github.com/karpathy/autoresearch.

**Goal:** Find the cheapest detection strategy that achieves 100% accuracy on a 5-video labeled test set.

### The Pattern

1. **One mutable file** (`test_detector.py`) — contains the detection strategy as `async def detect(...)`
2. **One immutable harness** (`eval_harness.py`) — downloads videos, extracts media, calls `detect()`, scores, logs
3. **A ratchet** — only keep strategies that are cheaper than current best while maintaining 100% accuracy
4. **An agent** — reads `program.md`, freely modifies `test_detector.py`, runs harness, iterates

### File Roles

```
evaluation/
├── CLAUDE.md           ← THIS FILE. Project spec. READ-ONLY.
├── program.md          ← Agent instructions. READ-ONLY.
├── eval_harness.py     ← IMMUTABLE. Runs strategies and scores them.
├── test_detector.py    ← MUTABLE. The ONLY file the agent modifies.
├── test_questions.md   ← IMMUTABLE. 5-video labeled test set.
├── pyproject.toml      ← IMMUTABLE. uv dependencies.
├── cache/              ← Auto-created. Downloaded videos + frames + audio cached here.
└── results.jsonl       ← APPEND-ONLY. Never delete or overwrite. Created on first run.
```

**Agent CAN modify:** `test_detector.py` only.
**Agent CANNOT modify:** everything else in `evaluation/`, anything in `backend/`.
**Agent MAY READ:** any file in `backend/` for reference.

---

## Test Set

Defined in `test_questions.md`:

| # | Reel ID | Ground Truth |
|---|---------|-------------|
| 1 | DWM0mTqDOIF | Real |
| 2 | DT0U_hcDSaq | Real |
| 3 | DVmPqQhETyD | AI — Ferrari Koi Pond; validated in Phase 1 research |
| 4 | DWj-0u6EgX4 | AI |
| 5 | DWmajXxjF7S | AI |

---

## Scoring Rules (IMMUTABLE — agent cannot change these)

A video **PASSES** if and only if:
1. Verdict is **correct** — `"AI GENERATED"` for AI videos, `"LIKELY REAL"` for real videos
2. Confidence is `"HIGH"` or `"MEDIUM"`

A video **FAILS** if:
- Verdict is wrong
- Verdict is `"UNCERTAIN"` (always fails)
- Confidence is `"LOW"` (always fails, even with correct verdict)

A strategy is **valid** only if it achieves **5/5 pass rate**.

---

## Optimization Objective

- **Minimize** `total_cost_usd` across all 5 videos
- **Hard constraint:** 100% pass rate (5/5)
- **Ratchet:** new strategy beats current best only if valid AND cheaper
- **Stop** when you have exhausted reasonable ideas

---

## How to Run

```bash
cd evaluation
uv run eval_harness.py
```

The harness:
1. Reads `test_questions.md` for URLs and labels
2. Downloads each video (cached — only downloaded once)
3. Extracts 8 frames + audio + caption (cached)
4. Imports `detect` fresh from `test_detector.py`
5. Calls `detect()` for each video
6. Scores against ground truth
7. Prints summary table
8. Appends one JSON line to `results.jsonl`

---

## The `detect()` Function Interface

`test_detector.py` must export exactly this async function:

```python
async def detect(
    frames: list[str],       # absolute paths to ALL extracted JPEG frames (up to ~30, at ~1fps)
    audio_path: str | None,  # absolute path to MP3, or None if no audio track
    caption: str | None,     # video caption/description from metadata
    video_path: str,         # absolute path to downloaded video file
) -> dict:
    """
    Returns:
        verdict:       "AI GENERATED" | "LIKELY REAL" | "UNCERTAIN"
        confidence:    "HIGH" | "MEDIUM" | "LOW"
        reason:        one sentence
        model_used:    e.g. "gemini-2.5-flash"
        input_tokens:  int
        output_tokens: int
        cost_usd:      float
    """
```

**Frame selection is a free parameter.** The harness extracts up to 30 frames at ~1fps and passes all of them. The `detect()` function chooses which to send to the model:

```python
# Use first 8 evenly spread (baseline)
step = len(frames) / 8
selected = [frames[int(i * step)] for i in range(8)]

# Use only 4 frames
selected = frames[:4]

# Use every other frame
selected = frames[::2]

# Use just the middle frame (cheapest single-frame call)
selected = [frames[len(frames) // 2]]

# Use all available frames
selected = frames
```

The function may also ignore any input entirely. A caption-only strategy ignores `frames`, `audio_path`, and `video_path`. A visual-only strategy ignores `audio_path`.

The harness provides all four inputs every time — the strategy decides what to use.

---

## Model Cost Table (April 2026)

| Model | Input $/1M | Output $/1M | Approx. cost/video (8 frames + audio) |
|-------|-----------|------------|---------------------------------------|
| `gemini-2.5-flash-lite` | $0.10 | $0.40 | ~$0.0003 |
| `gemini-2.5-flash` | $0.30 | $1.00 | ~$0.0016 |
| `claude-haiku-4-5-20251001` | $0.80 | $4.00 | ~$0.005 |
| `claude-sonnet-4-6` | $3.00 | $15.00 | ~$0.039 |

Cost formula: `(input_tokens * input_price_per_M + output_tokens * output_price_per_M) / 1_000_000`

Gemini thinking tokens (`thinking_budget > 0`) are billed at $3.50/1M. Disable thinking for cost optimization unless accuracy specifically requires it.

---

## Known Research Findings (from Phase 1 `backend/research.py`)

1. **Flash-Lite alone fails on subtle AI** — missed DVmPqQhETyD without audio
2. **Audio is decisive** — Flash + audio caught DVmPqQhETyD at $0.0016 vs Flash alone missing it
3. **Caption keywords are a strong signal** — production detector pre-screens with keyword lists
4. **Flash with thinking=OFF is cheaper** — thinking tokens expensive and no accuracy benefit observed
5. **Two-pass retry helps** — if response parsing fails, a second extraction call usually works
6. **Sonnet is the safety net** — always correct, but at $0.039/video vs $0.0016 for Flash+audio

---

## SDK Usage Patterns

### Gemini (google-genai)
```python
from google import genai
from google.genai import types

client = genai.Client()  # reads GEMINI_API_KEY from env

response = await client.aio.models.generate_content(
    model="gemini-2.5-flash",
    contents=[
        types.Part.from_text(text="prompt here"),
        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
        types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg"),
    ],
    config=types.GenerateContentConfig(
        max_output_tokens=512,
        temperature=0.1,
        thinking_config=types.ThinkingConfig(thinking_budget=0),  # disable thinking
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
        ],
    ),
)
text = response.text  # None if blocked
input_tok = response.usage_metadata.prompt_token_count
output_tok = response.usage_metadata.candidates_token_count
```

### Claude (anthropic)
```python
import anthropic, base64

client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env

with open(frame_path, "rb") as f:
    b64 = base64.standard_b64encode(f.read()).decode()

response = await client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=512,
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "prompt here"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
    ]}],
)
text = response.content[0].text
input_tok = response.usage.input_tokens
output_tok = response.usage.output_tokens
```

**Note:** Claude does not support inline audio — frames and caption only.

---

## Environment Variables

The harness loads from `evaluation/.env` first, then falls back to `backend/.env`.

| Variable | Required | Notes |
|----------|----------|-------|
| `GEMINI_API_KEY` | Yes | For any Gemini strategy |
| `ANTHROPIC_API_KEY` | Yes if using Claude | For Haiku or Sonnet strategies |
| `INSTAGRAM_COOKIES_FILE` | Recommended | Path to Netscape cookies.txt |
| `INSTAGRAM_COOKIES_B64` | Alternative | Base64-encoded cookies.txt |

---

## results.jsonl Schema

Each line is one experiment run:

```json
{
  "timestamp": "2026-04-05T10:30:00Z",
  "strategy_name": "flash-lite-audio-4frames",
  "strategy_description": "Gemini 2.5 Flash-Lite with audio, 4 frames only",
  "pass_rate": "5/5",
  "total_cost_usd": 0.00126,
  "is_valid": true,
  "is_new_best": true,
  "per_video": [
    {
      "url": "https://www.instagram.com/reel/DVmPqQhETyD/",
      "ground_truth": "AI",
      "verdict": "AI GENERATED",
      "confidence": "HIGH",
      "reason": "Caption explicitly mentions AI challenge and visual artifacts detected.",
      "passed": true,
      "cost_usd": 0.00031,
      "model_used": "gemini-2.5-flash-lite",
      "input_tokens": 2100,
      "output_tokens": 45
    }
  ]
}
```
