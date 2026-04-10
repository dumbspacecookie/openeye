"""
run_eval.py �� evaluates a model on the OpenEye procedure verification eval set.

Run:
    python eval/scripts/run_eval.py \
      --model meta-llama/Llama-3.3-70B-Instruct \
      --base-url https://api.groq.com/openai/v1 \
      --api-key $GROQ_API_KEY \
      --eval-data eval/eval-data/procedure_verification_eval.json \
      --output results/llama33-base.json
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

SYSTEM_PROMPT = (
    "You are a procedure verification assistant. "
    "Given a scene description and step context, respond with exactly one word: pass, fail, or uncertain."
)

VALID_RESULTS = {"pass", "fail", "uncertain"}


def score(prediction: str, ground_truth: str) -> float:
    pred = prediction.strip().lower()
    truth = ground_truth.strip().lower()
    if pred == truth:
        return 1.0
    if pred == "uncertain" or truth == "uncertain":
        return 0.5
    return 0.0


def call_model(base_url: str, api_key: str, model: str, user_prompt: str) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 10,
        "temperature": 0.0,
    }).encode()

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip().lower()


def evaluate(eval_data: list, base_url: str, api_key: str, model: str) -> dict:
    results_by_difficulty = {"easy": [], "medium": [], "hard": []}
    results_by_procedure = {}
    scores = []

    for i, ex in enumerate(eval_data):
        user_prompt = (
            f"Procedure: {ex['procedure']}\n"
            f"Step: {ex['step_name']} (id: {ex['step_id']})\n"
            f"Scene: {ex['scene_description']}\n"
            f"Objects detected: {', '.join(ex.get('objects_detected', []))}\n\n"
            f"Is this step complete?"
        )
        try:
            prediction = call_model(base_url, api_key, model, user_prompt)
            if prediction not in VALID_RESULTS:
                prediction = "uncertain"
        except Exception as e:
            sys.stderr.write(f"  [warn] eval-{i+1}: {e}\n")
            prediction = "uncertain"

        s = score(prediction, ex["ground_truth"])
        scores.append(s)
        results_by_difficulty[ex["difficulty"]].append(s)
        results_by_procedure.setdefault(ex["procedure"], []).append(s)

        if (i + 1) % 10 == 0:
            running_acc = sum(scores) / len(scores)
            sys.stdout.write(f"  {i+1}/{len(eval_data)} — running accuracy: {running_acc:.2%}\n")
            sys.stdout.flush()

    total = len(scores)
    return {
        "model": model,
        "total": total,
        "score": round(sum(scores), 2),
        "accuracy": round(sum(scores) / total, 4) if total else 0,
        "by_difficulty": {
            k: round(sum(v) / len(v), 4) if v else 0
            for k, v in results_by_difficulty.items()
        },
        "by_procedure": {
            k: round(sum(v) / len(v), 4) if v else 0
            for k, v in results_by_procedure.items()
        },
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--eval-data", default=os.path.join(os.path.dirname(__file__), "..", "eval-data", "procedure_verification_eval.json"))
    p.add_argument("--output", required=True)
    args = p.parse_args()

    with open(args.eval_data) as f:
        eval_data = json.load(f)

    print(f"evaluating {args.model} on {len(eval_data)} examples...")
    results = evaluate(eval_data, args.base_url, args.api_key, args.model)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\naccuracy: {results['accuracy']:.2%}")
    print(f"by difficulty: {results['by_difficulty']}")
    print(f"results -> {args.output}")


if __name__ == "__main__":
    main()
