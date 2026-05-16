"""
Benchmark a procedure-verification model against the OpenEye 20-example
test set. Works with any OpenAI-compatible API (Anthropic, OpenAI, Groq,
local Llama via vLLM/Ollama, your DPO-trained checkpoint via vLLM).

Scoring:
  - exact verdict match:        1.0
  - "uncertain" ↔ pass/fail:    0.5
  - opposite verdict (pass↔fail): 0.0

Compares two models side by side so you can quantify the fine-tuning lift.

Usage:
    # baseline
    python benchmark.py \\
        --model meta-llama/Llama-3.2-3B-Instruct \\
        --endpoint http://localhost:8000/v1 \\
        --api-key dummy \\
        --label baseline

    # after DPO
    python benchmark.py \\
        --model ./openeye-llama-3.2-3b-dpo \\
        --endpoint http://localhost:8001/v1 \\
        --api-key dummy \\
        --label dpo

    # combine into a report
    python benchmark.py --compare baseline.json dpo.json
"""

import argparse
import json
import logging
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [bench] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are a procedure verification assistant. You will receive a scene "
    "description from a camera and a procedure step. Your job is to verify "
    "whether the step was performed correctly. Respond with EXACTLY one word: "
    "'pass', 'fail', or 'uncertain'."
)

USER_PROMPT_TEMPLATE = (
    "Procedure: {procedure_tag}\n"
    "Step: {step_name}\n"
    "Scene description: {scene_description}\n\n"
    "Verdict (pass/fail/uncertain):"
)


def load_benchmark(path: str = "benchmark_data.json") -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["examples"]


def query_model(endpoint: str, api_key: str, model: str,
                user_prompt: str, timeout: int = 30) -> Optional[str]:
    """OpenAI-compatible chat completion. Returns the text or None on error."""
    url = f"{endpoint.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 16,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        logger.warning("HTTP %d: %s", e.code, e.read()[:200])
        return None
    except Exception as e:
        logger.warning("Request failed: %s", e)
        return None


def parse_verdict(text: str) -> Optional[str]:
    """Extract pass/fail/uncertain from arbitrary model output."""
    if not text:
        return None
    t = text.strip().lower()
    # Try first word
    first = re.match(r"^[^\w]*([a-z]+)", t)
    if first and first.group(1) in ("pass", "fail", "uncertain"):
        return first.group(1)
    # Look anywhere in first 50 chars
    snippet = t[:50]
    for verdict in ("uncertain", "pass", "fail"):
        if re.search(rf"\b{verdict}\b", snippet):
            return verdict
    return None


def score_example(predicted: Optional[str], expected: str) -> float:
    if predicted is None:
        return 0.0
    if predicted == expected:
        return 1.0
    if predicted == "uncertain" or expected == "uncertain":
        return 0.5
    return 0.0  # opposite (pass↔fail)


def run_benchmark(args) -> Dict:
    examples = load_benchmark(args.data)
    logger.info("Loaded %d benchmark examples", len(examples))

    results = []
    start = time.time()
    for i, ex in enumerate(examples, 1):
        user_prompt = USER_PROMPT_TEMPLATE.format(**ex)
        raw = query_model(args.endpoint, args.api_key, args.model, user_prompt)
        verdict = parse_verdict(raw) if raw else None
        score = score_example(verdict, ex["expected_verdict"])
        results.append({
            "id": ex["id"],
            "procedure_tag": ex["procedure_tag"],
            "expected": ex["expected_verdict"],
            "predicted": verdict,
            "raw": raw,
            "score": score,
        })
        logger.info("[%2d/%d] %s: expected=%s predicted=%s score=%.1f",
                    i, len(examples), ex["id"],
                    ex["expected_verdict"], verdict, score)

    duration = time.time() - start
    scores = [r["score"] for r in results]
    by_proc: Dict[str, List[float]] = {}
    for r in results:
        by_proc.setdefault(r["procedure_tag"], []).append(r["score"])

    summary = {
        "label": args.label,
        "model": args.model,
        "endpoint": args.endpoint,
        "n": len(results),
        "duration_seconds": round(duration, 1),
        "overall_score": round(sum(scores) / len(scores), 3),
        "exact_match_rate": round(sum(1 for s in scores if s == 1.0) / len(scores), 3),
        "wrong_verdict_rate": round(sum(1 for s in scores if s == 0.0) / len(scores), 3),
        "by_procedure": {
            proc: {"n": len(s), "score": round(sum(s) / len(s), 3)}
            for proc, s in by_proc.items()
        },
        "results": results,
    }

    out_path = Path(f"{args.label}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", out_path)
    logger.info("Overall: %.3f  exact: %.3f  wrong: %.3f  (%.1fs)",
                summary["overall_score"], summary["exact_match_rate"],
                summary["wrong_verdict_rate"], summary["duration_seconds"])
    return summary


def compare(paths: List[str]) -> None:
    summaries = [json.load(open(p, "r", encoding="utf-8")) for p in paths]

    print()
    print("OpenEye Procedure Verification Benchmark — Comparison")
    print("=" * 70)
    print(f"{'metric':<24}" + "".join(f"{s['label']:<14}" for s in summaries))
    print("-" * 70)
    print(f"{'overall score':<24}" + "".join(f"{s['overall_score']:<14}" for s in summaries))
    print(f"{'exact match rate':<24}" + "".join(f"{s['exact_match_rate']:<14}" for s in summaries))
    print(f"{'wrong verdict rate':<24}" + "".join(f"{s['wrong_verdict_rate']:<14}" for s in summaries))
    print()

    # Per-procedure breakdown
    procs = sorted(set().union(*[s["by_procedure"].keys() for s in summaries]))
    print("By procedure:")
    print(f"{'procedure':<24}" + "".join(f"{s['label']:<14}" for s in summaries))
    for proc in procs:
        row = f"{proc:<24}"
        for s in summaries:
            v = s["by_procedure"].get(proc, {}).get("score", "—")
            row += f"{v!s:<14}"
        print(row)
    print()

    # Lift if comparing exactly 2 results
    if len(summaries) == 2:
        delta = summaries[1]["overall_score"] - summaries[0]["overall_score"]
        rel = (delta / summaries[0]["overall_score"]) * 100 if summaries[0]["overall_score"] else 0
        sign = "+" if delta >= 0 else ""
        print(f"Δ overall: {sign}{delta:.3f} ({sign}{rel:.1f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="benchmark_data.json")
    ap.add_argument("--model", help="Model name as the API expects it")
    ap.add_argument("--endpoint", help="OpenAI-compatible chat-completions endpoint")
    ap.add_argument("--api-key", default="dummy")
    ap.add_argument("--label", default="run",
                    help="Output file label (writes <label>.json)")
    ap.add_argument("--compare", nargs="+",
                    help="Compare two or more result JSONs; do not run new benchmark")
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return

    if not args.model or not args.endpoint:
        ap.error("--model and --endpoint are required (or use --compare)")
    run_benchmark(args)


if __name__ == "__main__":
    main()
