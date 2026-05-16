"""
OpenEye RL Trajectory Capture
Converts completed pi agent sessions into ShareGPT-format trajectories
compatible with hermes batch_runner and tinker-atropos.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from state import get_db, OpenEyeDB

logger = logging.getLogger(__name__)


def _role_to_sharegpt(role: str) -> str:
    mapping = {"user": "human", "assistant": "gpt", "system": "system",
               "toolResult": "tool", "tool": "tool"}
    return mapping.get(role, role)


def messages_to_sharegpt(messages, system_prompt=None) -> List[Dict]:
    result = []
    if system_prompt:
        result.append({"from": "system", "value": system_prompt})
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        if role == "assistant":
            if tool_calls:
                tc_text = json.dumps(tool_calls, ensure_ascii=False)
                value = f"{content}\n<tool_calls>{tc_text}</tool_calls>".strip()
            else:
                value = content
            result.append({"from": "gpt", "value": value})
        elif role in ("user", "human"):
            result.append({"from": "human", "value": content})
        elif role in ("toolResult", "tool"):
            result.append({"from": "tool", "value": content, "tool_name": msg.get("tool_name")})
    return result


def compute_visual_reward(visual_session_id: str,
                          procedure_tag: Optional[str] = None) -> Optional[float]:
    """Compute the reward signal for a visual session.

    Default formula: (1.0 × passes + 0.5 × uncertain + 0.0 × fails) / total.
    Override per-procedure via OpenEyeDB.set_procedure_reward_weights().
    Custom weights matter when a procedure's risk profile makes "uncertain"
    closer to fail than pass (e.g. sterile-field checks)."""
    db = get_db()
    counts = db.count_step_results(visual_session_id)
    if not counts:
        return None
    weights = db.get_procedure_reward_weights(procedure_tag)
    total = sum(counts.values())
    weighted = (
        weights["pass_weight"] * counts.get("pass", 0)
        + weights["uncertain_weight"] * counts.get("uncertain", 0)
        + weights["fail_weight"] * counts.get("fail", 0)
    )
    return round(weighted / total, 4)


def capture_trajectory(session_id, completed, model, system_prompt=None,
                       visual_session_id=None, tenant_id=None, tags=None,
                       cloud_sync=False) -> Optional[str]:
    db = get_db()
    messages = db.get_messages(session_id)
    if not messages:
        return None
    conversations = messages_to_sharegpt(messages, system_prompt=system_prompt)
    if not conversations:
        return None
    reward = None
    if visual_session_id:
        # First non-meta tag is the procedure tag — same heuristic as DPO export
        procedure_tag = None
        for t in (tags or []):
            if t and t not in ("openeye", "completed", "abandoned", "error"):
                procedure_tag = t
                break
        reward = compute_visual_reward(visual_session_id, procedure_tag)
    trajectory_id = db.save_trajectory(
        conversations=conversations, model=model, completed=completed,
        session_id=session_id, visual_session_id=visual_session_id,
        tenant_id=tenant_id, reward_signal=reward, tags=tags or [],
        mark_sync=cloud_sync)
    logger.info("Trajectory captured: %s (session=%s, completed=%s, reward=%s)",
                trajectory_id, session_id, completed, reward)
    return trajectory_id


def export_for_training(output_path, completed_only=True) -> int:
    db = get_db()
    count = db.export_trajectories_jsonl(output_path, completed_only=completed_only)
    logger.info("Exported %d trajectories to %s", count, output_path)
    return count
