"""
Pull opted-in trajectories from a Context receiver and write them as a
TRL-compatible JSONL.

Schema produced (matches what train_dpo.py expects):

    {
      "prompt":   "<scene description / verification request>",
      "chosen":   "<assistant response from a HIGH-reward trajectory>",
      "rejected": "<assistant response from a LOW-reward trajectory>",
      "procedure_tag": "bolt-assembly",
      "chosen_reward": 0.95,
      "rejected_reward": 0.18,
    }

Run:
    python fetch_trajectories.py \\
        --endpoint https://context-receiver-yourname.fly.dev \\
        --token ctx-... \\
        --output trajectories_dpo.jsonl \\
        --chosen-threshold 0.8 \\
        --rejected-threshold 0.4
"""

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [fetch] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def fetch_all(endpoint: str, token: str, page_size: int = 500) -> List[Dict]:
    """Pull every trajectory the token has access to. The receiver
    paginates by `limit`; we walk procedure_tag-by-procedure_tag to keep
    page sizes manageable for big tenants."""
    base = endpoint.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    # First, list batches to discover which procedures exist
    req = urllib.request.Request(f"{base}/v1/openeye/batches?limit=1000", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            batches = json.loads(resp.read()).get("batches", [])
    except urllib.error.HTTPError as e:
        logger.error("Failed to list batches: HTTP %d %s", e.code, e.reason)
        sys.exit(1)

    if not batches:
        logger.warning("No batches found at %s", base)
        return []

    # Now pull trajectories
    req = urllib.request.Request(f"{base}/v1/openeye/trajectories?limit={page_size}", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        listing = json.loads(resp.read()).get("trajectories", [])

    # The list endpoint returns metadata only; we need conversations.
    # Fetch one by one. (A production receiver would paginate the full body.)
    full = []
    for meta in listing:
        tid = meta["trajectory_id"]
        try:
            req = urllib.request.Request(
                f"{base}/v1/openeye/trajectories/{tid}", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                full.append(json.loads(resp.read()))
        except urllib.error.HTTPError as e:
            logger.warning("Skipping %s: HTTP %d", tid, e.code)
    return full


def extract_dpo_pairs(trajectories: List[Dict],
                     chosen_threshold: float,
                     rejected_threshold: float) -> List[Dict]:
    """Group by procedure_tag, then pair highest-reward chosen against
    lowest-reward rejected. Same algorithm as sidecar/dpo_export.py."""
    by_proc: Dict[str, List[Dict]] = defaultdict(list)
    for t in trajectories:
        proc = t.get("procedure_tag")
        reward = t.get("reward_signal")
        if proc and reward is not None:
            by_proc[proc].append(t)

    pairs = []
    for proc, ts in by_proc.items():
        chosen = [t for t in ts if t["reward_signal"] >= chosen_threshold]
        rejected = [t for t in ts if t["reward_signal"] <= rejected_threshold]
        if not chosen or not rejected:
            logger.info("Skipping %s: chosen=%d rejected=%d (need both)",
                        proc, len(chosen), len(rejected))
            continue

        # Take the best chosen × worst rejected as the canonical pair.
        # Real production: generate multiple pairs per procedure for more data.
        best = max(chosen, key=lambda t: t["reward_signal"])
        worst = min(rejected, key=lambda t: t["reward_signal"])

        prompt, chosen_resp = _to_prompt_completion(best.get("conversations", []))
        _, rejected_resp = _to_prompt_completion(worst.get("conversations", []))

        if not prompt or not chosen_resp or not rejected_resp:
            continue

        pairs.append({
            "prompt": prompt,
            "chosen": chosen_resp,
            "rejected": rejected_resp,
            "procedure_tag": proc,
            "chosen_reward": best["reward_signal"],
            "rejected_reward": worst["reward_signal"],
        })
    return pairs


def _to_prompt_completion(conversations: List[Dict]) -> tuple:
    """Take a ShareGPT conversation, fold all `human` turns into a prompt,
    and the last `gpt` turn into the completion."""
    prompt_parts = []
    last_gpt = None
    for msg in conversations:
        role = msg.get("from") or msg.get("role")
        value = msg.get("value", "")
        if not value:
            continue
        if role in ("human", "user"):
            prompt_parts.append(value)
        elif role in ("gpt", "assistant"):
            last_gpt = value
    return ("\n".join(prompt_parts), last_gpt or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True,
                    help="Base URL of the Context receiver (e.g. https://context-receiver-yourname.fly.dev)")
    ap.add_argument("--token", required=True,
                    help="Bearer token for the receiver")
    ap.add_argument("--output", default="trajectories_dpo.jsonl")
    ap.add_argument("--chosen-threshold", type=float, default=0.8)
    ap.add_argument("--rejected-threshold", type=float, default=0.4)
    args = ap.parse_args()

    logger.info("Fetching trajectories from %s", args.endpoint)
    trajectories = fetch_all(args.endpoint, args.token)
    logger.info("Got %d trajectories", len(trajectories))

    pairs = extract_dpo_pairs(trajectories,
                              args.chosen_threshold,
                              args.rejected_threshold)
    logger.info("Built %d DPO pairs", len(pairs))

    with open(args.output, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    logger.info("Wrote %s", args.output)


if __name__ == "__main__":
    main()
