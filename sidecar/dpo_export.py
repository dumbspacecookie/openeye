"""
DPO preference pair export for OpenEye.

Pairs high-reward agent sessions (chosen) against low-reward ones (rejected)
on the same procedure, outputting TRL-compatible JSONL for fine-tuning with
TRL, LLaMA-Factory, Axolotl, or any DPO-capable trainer.

Output format (TRL-compatible, role/content not from/value):
  {
    "chosen":  [{"role": "system"|"user"|"assistant", "content": "..."}],
    "rejected": [...],
    "openeye_meta": {
      "procedure": "hand-hygiene",
      "chosen_reward": 0.95,
      "rejected_reward": 0.28
    }
  }
"""

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

try:
    from state import get_db
except ImportError:
    get_db = None


def sharegpt_to_trl(messages: List[Dict]) -> List[Dict]:
    """Convert ShareGPT format (from/value) to TRL format (role/content)."""
    role_map = {"system": "system", "human": "user", "gpt": "assistant", "tool": "assistant"}
    result = []
    for msg in messages:
        role = role_map.get(msg.get("from", ""), "user")
        content = msg.get("value", "")
        if not content:
            continue
        result.append({"role": role, "content": content})
    return result


def _procedure_from_tags(tags_json: Optional[str]) -> Optional[str]:
    """Extract the primary procedure identifier from a trajectory's tags."""
    if not tags_json:
        return None
    try:
        tags = json.loads(tags_json) if isinstance(tags_json, str) else tags_json
        skip = {"openeye", "completed", "abandoned", "error"}
        for tag in tags:
            if tag not in skip:
                return tag
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _load_trajectories(completed_only: bool) -> List[Dict]:
    db = get_db()
    with db._lock:
        q = "SELECT * FROM trajectories"
        if completed_only:
            q += " WHERE completed=1"
        q += " ORDER BY created_at"
        cursor = db._conn.execute(q)
        rows = cursor.fetchall()
    result = []
    for row in rows:
        t = dict(row)
        try:
            t["conversations"] = json.loads(t["conversations"])
        except (json.JSONDecodeError, TypeError):
            t["conversations"] = []
        try:
            t["tags_list"] = json.loads(t["tags"]) if t.get("tags") else []
        except (json.JSONDecodeError, TypeError):
            t["tags_list"] = []
        result.append(t)
    return result


def build_dpo_pairs(
    trajectories: List[Dict],
    chosen_threshold: float = 0.8,
    rejected_threshold: float = 0.4,
) -> List[Dict]:
    """
    Pair chosen (high-reward) and rejected (low-reward) trajectories by procedure.

    Algorithm:
      1. Group trajectories by procedure tag
      2. For each procedure with both chosen and rejected candidates:
         - chosen  = highest reward_signal above chosen_threshold
         - rejected = lowest reward_signal below rejected_threshold
      3. Convert ShareGPT -> TRL format for both
      4. Return list of DPO pair dicts
    """
    # Group by procedure
    by_procedure: Dict[str, List[Dict]] = {}
    for t in trajectories:
        procedure = _procedure_from_tags(t.get("tags") or t.get("tags_list"))
        if not procedure:
            continue
        reward = t.get("reward_signal")
        if reward is None:
            continue
        by_procedure.setdefault(procedure, []).append(t)

    pairs = []
    for procedure, trajs in by_procedure.items():
        # Find best chosen (highest reward above threshold)
        chosen_candidates = [t for t in trajs if (t.get("reward_signal") or 0) >= chosen_threshold]
        rejected_candidates = [t for t in trajs if (t.get("reward_signal") or 1) <= rejected_threshold]

        if not chosen_candidates or not rejected_candidates:
            continue

        best_chosen = max(chosen_candidates, key=lambda t: t.get("reward_signal") or 0)
        worst_rejected = min(rejected_candidates, key=lambda t: t.get("reward_signal") or 0)

        chosen_trl = sharegpt_to_trl(best_chosen.get("conversations", []))
        rejected_trl = sharegpt_to_trl(worst_rejected.get("conversations", []))

        if not chosen_trl or not rejected_trl:
            continue

        pairs.append({
            "chosen": chosen_trl,
            "rejected": rejected_trl,
            "openeye_meta": {
                "procedure": procedure,
                "chosen_reward": best_chosen.get("reward_signal"),
                "rejected_reward": worst_rejected.get("reward_signal"),
            },
        })

    return pairs


def export_dpo_pairs(
    output_path: str,
    chosen_threshold: float = 0.8,
    rejected_threshold: float = 0.4,
    completed_only: bool = True,
) -> int:
    """Export DPO preference pairs to a JSONL file. Returns pair count."""
    trajectories = _load_trajectories(completed_only)
    pairs = build_dpo_pairs(trajectories, chosen_threshold, rejected_threshold)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    return len(pairs)
