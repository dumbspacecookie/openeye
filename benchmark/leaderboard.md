# OpenEye-Bench Leaderboard

Community-submitted results on **OpenEye-Bench v1.0** (20 examples).
Sorted by `overall_score` descending.

| Model | Overall | Exact | Wrong | Submitter | Notes |
|---|---|---|---|---|---|
| _(your result here — open a PR)_ | | | | | |

## How to submit

1. Run `python runner.py --model <your-model> --endpoint <your-endpoint> --label myresult`
2. Open a PR adding a row to the table above. Include:
   - **Model**: a recognizable name (`claude-sonnet-4-6`, `llama-3.2-3b-finetuned-on-bolt-assembly`, etc.)
   - **Overall / Exact / Wrong**: scores from your run, rounded to 3 decimals
   - **Submitter**: GitHub handle or org
   - **Notes**: anything notable (`fine-tuned on 80 in-house trajectories`, `quantized to 4-bit`, etc.)
3. Attach `myresult.json` to the PR for reproducibility.

We accept results from any model and any endpoint, but they must come from
an unmodified `runner.py` against the frozen `dataset_v1.json`. Modified
runners (different prompts, retry-until-pass loops, etc.) belong in their
own forks.

## Rules

- **No training on the benchmark dataset.** If your training data includes
  any of the 20 scenes from `dataset_v1.json`, your submission is invalid.
  The dataset's intentionally small size makes this temptation real — don't.
- **Same prompt for everyone.** The runner's `SYSTEM_PROMPT` and
  `USER_PROMPT_TEMPLATE` are fixed. Don't change them in submissions.
- **Single shot, temperature 0.** Deterministic outputs. If your endpoint
  doesn't support temperature 0, note it in the submission.
- **Three retries on transient errors.** Network failures don't count; we
  re-run. But a model that refuses to answer counts as a wrong verdict.

## Open questions for the community

These would all be useful additions — open an issue if you want to take one:

- Multilingual variant (es / de / fr / zh translations)
- Long-form scene descriptions (3-4 sentences, more realistic)
- Adversarial examples (operator partially out of frame, lighting issues)
- A "difficulty" tier (basic vs. expert procedures)
