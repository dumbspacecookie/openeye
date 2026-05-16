# Fine-tuning on OpenEye trajectories

The whole pitch of OpenEye is: **sessions generate training data, that data
fine-tunes a model, the fine-tuned model is measurably better at the
specific procedure**. This directory turns that claim into a runnable
proof: pull opted-in trajectories from a Context receiver, train a small
Llama on them with DPO, and benchmark before vs. after.

## What's here

| file | role |
|---|---|
| `fetch_trajectories.py` | pull trajectories from a Context receiver, write a TRL-shaped JSONL |
| `train_dpo.py` | DPO fine-tune via TRL (`DPOTrainer`) — supports QLoRA for 16GB GPUs |
| `benchmark.py` | OpenAI-compatible bench against a 20-example test set |
| `benchmark_data.json` | the 20 examples across 5 procedures (manufacturing, hand-hygiene, LOTO, PPE, QA) |
| `requirements.txt` | only needed for training; benchmark works without |

## End-to-end pipeline

### 0. Prereqs
- A Context receiver running (the reference receiver from
  `examples/context-receiver/` works) with at least 20-30 trajectories
  across 2+ procedures. Mixed-quality trajectories needed — DPO requires
  both "good" and "bad" examples to learn from.
- A GPU box for training (Colab T4 free tier works for the 3B model).
- An OpenAI-compatible inference endpoint for benchmarking. Easiest:
  [vLLM](https://docs.vllm.ai/) to serve the local checkpoint.

### 1. Fetch the trajectories

```bash
python fetch_trajectories.py \
  --endpoint https://context-receiver-yourname.fly.dev \
  --token ctx-your-token \
  --output trajectories_dpo.jsonl \
  --chosen-threshold 0.8 \
  --rejected-threshold 0.4
```

Output: one DPO pair per procedure with `(prompt, chosen, rejected)`.
Expect ~5-20 pairs depending on volume. More is better but DPO can work
with as few as 50 across procedures.

### 2. Sanity check (no GPU needed)

```bash
python train_dpo.py --data trajectories_dpo.jsonl --dry-run
```

Prints the config and a sample pair. Confirms your JSONL is valid before
you spin up an expensive GPU.

### 3. Baseline benchmark (run before training)

Serve the base model via vLLM in another terminal:

```bash
pip install vllm
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.2-3B-Instruct \
  --port 8000
```

Then run the benchmark:

```bash
python benchmark.py \
  --model meta-llama/Llama-3.2-3B-Instruct \
  --endpoint http://localhost:8000/v1 \
  --label baseline
```

Output: `baseline.json` with the full scoring breakdown.

### 4. Train

On a GPU box (Colab cell or local):

```bash
pip install -r requirements.txt

python train_dpo.py \
  --data trajectories_dpo.jsonl \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --output ./openeye-llama-3.2-3b-dpo \
  --epochs 3 \
  --batch-size 4
```

T4 16GB: add `--use-qlora`. Training takes ~25 minutes on T4, ~5 minutes
on A100.

### 5. Post-train benchmark

Stop the baseline vLLM server, restart pointing at the trained checkpoint:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model ./openeye-llama-3.2-3b-dpo \
  --port 8001
```

Then benchmark:

```bash
python benchmark.py \
  --model ./openeye-llama-3.2-3b-dpo \
  --endpoint http://localhost:8001/v1 \
  --label dpo
```

### 6. Compare

```bash
python benchmark.py --compare baseline.json dpo.json
```

Sample output (real numbers will vary):

```
OpenEye Procedure Verification Benchmark — Comparison
======================================================================
metric                  baseline      dpo
----------------------------------------------------------------------
overall score           0.625         0.775
exact match rate        0.500         0.700
wrong verdict rate      0.250         0.100

By procedure:
procedure               baseline      dpo
bolt-assembly           0.625         0.875
hand-hygiene            0.625         0.750
lockout-tagout          0.625         0.750
ppe-check               0.625         0.750
qa-inspection           0.625         0.750

Δ overall: +0.150 (+24.0%)
```

## What the benchmark actually tests

The 20 examples in `benchmark_data.json` are **held out** — they were
not used during training. Each one is a scene description + a step +
the correct verdict (pass/fail/uncertain). The model scores 1.0 for an
exact match, 0.5 for `uncertain` vs. a definite verdict (acceptable
hedge), 0.0 for getting it backwards (pass ↔ fail). Wrong-verdict rate
is the key safety metric — a procedure verifier that mistakes a fail
for a pass is dangerous.

Worth noting: the benchmark uses model-written scene descriptions as
inputs. If your real deployment uses a different vision adapter (Claude
vs. Ollama), descriptions will look different and benchmark scores will
shift. Run the benchmark with descriptions from your actual vision
pipeline for the truest measure.

## Cherry-picking honesty

The first time you run this, you'll be tempted to:
- Tune the chosen/rejected thresholds until the bench looks great
- Cherry-pick procedures where DPO helps most

Don't. The whole point of this exercise is to show that the data
flywheel works on a held-out test set. If you have to tune the
thresholds, the data isn't strong enough yet — collect more.

## When DPO doesn't help

You'll see no lift (or regression) when:

1. **Too few pairs per procedure.** DPO needs maybe 30+ pairs per
   procedure to start moving the model meaningfully. Below that, the
   loss is too noisy.
2. **Chosen ≈ rejected.** If the high-reward and low-reward responses
   say similar things, there's no signal. Inspect 5 random pairs by
   hand before training — if you can't easily tell which is which,
   neither can the model.
3. **The base model already aces this task.** A frontier model
   (Claude Opus, GPT-4o) may saturate the benchmark at 0.95+ without
   training. DPO helps small open models catch up — it's not magic on
   already-strong models.
4. **Beta too low/high.** The default `--beta 0.1` is a starting
   point. If you see no learning, try 0.3. If the model collapses
   (produces garbage), try 0.05.

## What gets shared with Context

When you run `fetch_trajectories.py` against your own Context receiver,
you're pulling **your own** opted-in data back to fine-tune **your**
model. Nothing about this loop shares data with anyone else.

If Context (the platform) someday trains a community model on aggregated
opted-in data and releases it, that's the value-back side of the
flywheel — but it's optional and a separate pipeline. The local loop in
this directory is fully self-contained.
