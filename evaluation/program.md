# Program — AI Video Detection Cost Optimization Agent

You are an autonomous cost-optimization agent. Your single goal: find the **cheapest** detection strategy that achieves **100% accuracy** (5/5 pass rate with MEDIUM+ confidence) on the labeled test set.

## Ground Rules

1. You may ONLY modify `test_detector.py`. Every other file is immutable.
2. Read `CLAUDE.md` before starting — it contains the full project spec, scoring rules, model costs, and SDK patterns.
3. After EVERY change to `test_detector.py`, run: `uv run eval_harness.py`
4. Read `results.jsonl` after every run to interpret results.
5. NEVER modify `eval_harness.py`, `test_questions.md`, `CLAUDE.md`, `program.md`, or `pyproject.toml`.
6. NEVER delete or overwrite `results.jsonl`.
7. You may READ files in `backend/` for reference — especially `backend/services/detector.py` for production prompts and keyword lists.
8. NEVER modify anything in `backend/`.

---

## Step 0 — Orientation

Before your first experiment:

1. Read `CLAUDE.md` thoroughly.
2. Read `test_questions.md` for the 5 videos and their ground truth labels.
3. Read `results.jsonl` (if it exists) — note the current best valid strategy and its `total_cost_usd`. That is the number to beat.
4. Read `backend/services/detector.py` — it contains proven prompts, caption keyword lists (`_CAPTION_STRONG_AI`, `_CAPTION_WEAK_AI`), the multi-signal prompt template, and robust response parsing logic. These are your starting tools.
5. Read the current `test_detector.py` to understand the baseline strategy.

---

## Step 1 — Establish a Valid Baseline

Your first experiment: run the current `test_detector.py` unmodified to establish the baseline pass rate and cost.

```bash
uv run eval_harness.py
```

Check `results.jsonl`:
- If **5/5 pass rate** → record the cost. This is your baseline to beat.
- If **< 5/5** → the baseline itself needs fixing before you can optimize. Read which videos failed and why, then fix the strategy in `test_detector.py` first.

Do NOT attempt to reduce cost until you have a valid 5/5 strategy.

---

## Step 2 — The Experiment Loop

Repeat this cycle indefinitely:

```
1. HYPOTHESISE  Why might this be cheaper? What's the risk to accuracy?
2. EDIT         Modify test_detector.py to implement the hypothesis.
3. RUN          uv run eval_harness.py
4. READ         Check results.jsonl for the latest entry.
5. DECIDE
   - 5/5 AND cost < current best  →  KEEP. Record as new best.
   - 5/5 AND cost >= current best →  REVERT to last valid best. Not an improvement.
   - < 5/5                        →  REVERT to last valid best. Study which videos failed.
6. LEARN        Even failed experiments teach you something. Note it.
7. REPEAT
```

When you REVERT, restore `test_detector.py` to the last known valid best version before starting the next experiment. Check `results.jsonl` to find the `strategy_name` of the last `is_new_best: true` entry — that tells you what to restore.

---

## Creative Directions to Explore

These are ordered roughly from cheapest potential to most expensive. But you are not limited to this list — be creative.

### Tier 0: Zero-cost ($0.00)
**Caption-only keyword matching** — if caption contains STRONG AI keywords (see `_CAPTION_STRONG_AI` in `backend/services/detector.py`), return `AI GENERATED / HIGH` immediately without calling any model. Cost: $0 per video with strong caption. Risk: AI videos with no obvious caption will fail. Real videos with AI-adjacent captions may get false positives.

### Tier 1: Cheapest model (~$0.0001–0.0004/video)
**Flash-Lite + audio** — `gemini-2.5-flash-lite` is 3× cheaper than Flash. It failed in Phase 1 WITHOUT audio; with audio it may work. Try it.

**Flash-Lite + no audio** — even cheaper, but higher risk for subtle AI.

**Flash-Lite + fewer frames** — the harness provides up to 30 frames at ~1fps. Slice them: `frames[:2]`, `[frames[len(frames)//2]]` (single middle frame), or `frames[::5]`. Fewer image tokens = lower cost. Try 1, 2, 4 frames.

**Flash-Lite + caption + no frames** — pure text analysis with just the caption passed as context. Essentially free.

### Tier 2: Hybrid routing (~$0.0000–0.0009/video)
**Caption fast-path + Flash-Lite fallback** — if STRONG caption signal, return immediately (free). Otherwise call Flash-Lite. Expected cost: near-zero for videos with clear captions, Flash-Lite cost for ambiguous ones.

**Caption fast-path + Flash fallback** — same pattern but use full Flash for ambiguous videos. Balances accuracy and cost.

**Caption fast-path → Flash-Lite → Flash cascade** — three tiers. Easy = free. Medium = Flash-Lite. Hard = Flash. Only escalate when confidence is LOW.

### Tier 3: Full Flash (~$0.0009–0.0016/video)
**Flash no-audio** — remove audio to save audio token costs. Flash alone may be sufficient for some videos.

**Flash + audio + shorter prompt** — the production prompt is ~600 tokens. A stripped-down prompt saves ~$0.00006/video (small but adds up across many videos). Try: `"Is this video AI-generated? VERDICT: / CONFIDENCE: / REASON:"`.

**Flash + fewer frames** — halve or quarter the image token count by slicing `frames`. Try 4, 2, or 1 frame. The harness gives you up to 30 at 1fps; use `frames[:4]` or a spread like `[frames[int(i*len(frames)/4)] for i in range(4)]`.

### Tier 4: Higher accuracy but more expensive (~$0.005–0.039/video)
**Haiku** — cheaper than Flash + audio in some configurations (no audio support but very capable vision model).

**Sonnet** — most accurate, most expensive. Only reach for this if nothing cheaper works.

### Wild cards — try these too
- **Audio-only** — skip all frames, just send audio + caption. If AI videos have TTS audio, this might work at minimal cost.
- **Reduce frame resolution** — resize frames to 360px before sending. Fewer image tokens.
- **Different frame selection** — instead of evenly spaced, try first + last + middle (temporal spread with fewer frames).
- **Batched prompt** — send all 5 videos in one Gemini call with a structured response. May confuse the model but is worth testing.
- **Flash with thinking enabled** — thinking tokens are expensive ($3.50/1M) but may improve accuracy on subtle AI. Only try this if cheaper approaches fail.

---

## The Ratchet

The harness automatically tracks the best valid strategy in `results.jsonl` (`is_new_best: true`).

Your job: chase that `total_cost_usd` number down. If current best is $0.008, your next experiment should aim for < $0.008. If you find a strategy at $0.004, your next aim is < $0.004.

The ratchet only moves in one direction: cheaper.

---

## Simplicity Criterion

When two strategies have equal cost and accuracy, prefer the simpler one:
- Fewer lines of code
- Fewer model calls per video
- Fewer conditional branches
- A strategy you can describe in one sentence is better than one requiring three paragraphs

The ideal strategy is the cheapest AND the simplest that achieves the goal.

---

## Interpreting Per-Video Failures

When a strategy fails (< 5/5), read `per_video` in `results.jsonl` to understand which video failed and why. This is your most valuable signal.

Common failure patterns:
- **Real video classified as AI GENERATED** → the strategy is too aggressive, likely triggered by a false caption signal or visual false positive
- **AI video classified as LIKELY REAL** → the strategy missed a subtle signal; try adding audio, more frames, or a stronger model
- **AI video classified as UNCERTAIN** → the model was unsure; try a more explicit prompt or a stronger model
- **LOW confidence on correct verdict** → the model got the right answer but wasn't sure; try a more directive prompt or stronger model

Use the `reason` field in the failed video's entry — it tells you what the model saw (or didn't see).

---

## NEVER STOP

Keep exploring until you are genuinely convinced no cheaper valid strategy exists.

Ask yourself before stopping:
- Have I tried Flash-Lite with audio?
- Have I tried a caption fast-path that skips model calls for obvious AI videos?
- Have I tried reducing frame count to 4 or 2?
- Have I tried a cascade (cheap first, escalate only on low confidence)?
- Have I tried audio-only?
- Have I tried shorter prompts?

If the answer to any of these is "no", keep going.

Only stop when you have tried the main directions AND verified that the current best cannot be beaten. At that point, add a final entry to `results.jsonl` with `strategy_name: "FINAL"` and a note about your conclusion.

---

## Common Pitfalls

1. **Optimising cost before fixing accuracy.** Always get to 5/5 first.
2. **Forgetting to revert.** After a failed experiment, restore `test_detector.py` to last known best before the next attempt.
3. **Ignoring per-video failures.** One failed video tells you exactly what the strategy can't handle.
4. **Breaking the `detect()` interface.** The function signature must remain: `async def detect(frames, audio_path, caption, video_path) -> dict` with the exact keys.
5. **Changing the response format.** Models must output `VERDICT: / CONFIDENCE: / REASON:` lines. If you change the prompt, keep this requirement.
6. **Not reading `results.jsonl`.** It contains the full history. Don't repeat failed experiments.
7. **Adding complexity for marginal gains.** A 10% cost reduction that doubles code complexity is not worth it.
