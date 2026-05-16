# OpenEye-Bench v1.0

A reproducible, versioned benchmark for procedure-verification models.
Given a procedure step and a scene description, the model returns
`pass` / `fail` / `uncertain`. Scored on a held-out set of 20 examples
across 5 procedures.

## Why this exists

The OpenEye claim is "trajectories trained on opted-in real-world
verification data make models measurably better at the specific
procedure." Without a benchmark, that's marketing. With one, it's a
graph you can put on a slide.

Use this benchmark to:
- Compare baseline LLMs against fine-tuned variants
- Track progress release-over-release as the dataset grows
- Submit results to the public leaderboard (see below)

## What's here

```
benchmark/
├── README.md          — this file
├── dataset_v1.json    — 20 frozen examples across 5 procedures
├── runner.py          — OpenAI-compatible benchmark runner
├── leaderboard.md     — community submissions (PR-driven)
└── CONTRIBUTING.md    — how to submit a result or propose new examples
```

## Dataset

`dataset_v1.json` is **frozen**. Once we tag `benchmark-v1.0` we never
modify, reorder, or remove examples — that would invalidate prior
results. New examples land in `dataset_v2.json` etc.

### Coverage

| Procedure | Examples | Pass | Fail | Uncertain |
|---|---|---|---|---|
| bolt-assembly | 4 | 1 | 2 | 1 |
| hand-hygiene | 4 | 2 | 1 | 1 |
| lockout-tagout | 4 | 2 | 1 | 1 |
| ppe-check | 4 | 2 | 1 | 1 |
| qa-inspection | 4 | 2 | 2 | 0 |
| **Total** | **20** | **9** | **7** | **4** |

Each example contains: a `step_id`, `step_name`, `scene_description`
(camera-style natural language), `expected_verdict`, and a `rationale`
explaining why.

### Scoring

For each example:
- Exact verdict match: **1.0**
- Predicted `uncertain` vs. expected `pass` or `fail`: **0.5** (acceptable hedge)
- Predicted `pass` vs. expected `fail` (or vice versa): **0.0** (dangerous)

Reported metrics:
- **overall_score** — average across examples
- **exact_match_rate** — fraction scoring 1.0
- **wrong_verdict_rate** — fraction scoring 0.0 *(the safety metric)*

## Run it

Against any OpenAI-compatible endpoint:

```bash
python runner.py \
  --model claude-sonnet-4-6 \
  --endpoint https://api.anthropic.com/v1/messages \
  --api-key sk-ant-... \
  --label sonnet-4-6
```

Anthropic API isn't OpenAI-compatible directly — most users point at
their own vLLM / OpenRouter / Groq endpoint. Common targets:

| Provider | Endpoint | Notes |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | 200+ models behind one key |
| Groq | `https://api.groq.com/openai/v1` | Free tier, fast |
| vLLM | `http://localhost:8000/v1` | Self-hosted any open model |
| Ollama | `http://localhost:11434/v1` | Local llama.cpp models |
| OpenAI | `https://api.openai.com/v1` | GPT-4o, etc. |

Compare two runs:

```bash
python runner.py --compare baseline.json fine-tuned.json
```

## Baseline numbers (illustrative)

Real numbers will vary — this is what to expect roughly on the v1 dataset:

| Model | overall | exact | wrong |
|---|---|---|---|
| Claude Sonnet 4.6 | ~0.85 | ~0.75 | ~0.05 |
| GPT-4o | ~0.82 | ~0.72 | ~0.05 |
| Llama-3.3-70B (Groq) | ~0.72 | ~0.55 | ~0.10 |
| Llama-3.2-3B (base) | ~0.55 | ~0.35 | ~0.20 |
| Llama-3.2-3B + DPO on OpenEye data | ~0.75 | ~0.55 | ~0.10 |

The interesting cell is the last row: that's the lift you can buy with
~50-100 well-labeled trajectories. If you can move a small open model
from 0.55 to 0.75 on a held-out set, the data flywheel pays for itself.

## Limitations

1. **Text-only scenes.** The benchmark feeds models scene descriptions,
   not images. A model that excels here may still flunk when paired with
   a vision adapter whose descriptions are bad. Always benchmark against
   the actual vision-pipeline output for your deployment.
2. **English-only.** Procedures translate across languages but this
   dataset doesn't.
3. **No real-time component.** Latency isn't measured — you can fit
   anything in the budget by waiting. For AR overlay deployments, time
   to first verdict matters; benchmark separately.
4. **20 examples is small.** A model that scores 1.0 here isn't
   "solved" — it's "passed a vibe check." Real validation needs 200+
   examples per procedure.

## Roadmap

- **v1.0 (now):** 20 examples, frozen
- **v2.0 (when 5+ teams have submitted to v1):** 100 examples, more procedures
- **v3.0:** image-input variant for models with vision

## Submitting a result

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Briefly: open a PR adding a row
to `leaderboard.md` with your runner output JSON attached as an artifact.
