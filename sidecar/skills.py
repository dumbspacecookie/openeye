"""
OpenEye Skills Manager
Procedural memory: create, retrieve, and inject skills into agent context.
Skills are markdown documents the agent writes after completing complex tasks.
At session start, relevant skills are cosine-ranked against the task description
and injected into the system prompt.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from state import get_db

logger = logging.getLogger(__name__)

def _skills_dir() -> Path:
    d = Path(os.getenv("OPENEYE_HOME", str(Path.home() / ".openeye"))) / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tokenize(text: str) -> set:
    normalized = re.sub(r"[-_]", " ", text.lower())
    tokens = set(re.findall(r"\b[a-z0-9]{3,}\b", normalized))
    extras = {t[:-1] for t in tokens if t.endswith("s") and len(t) > 4}
    return tokens | extras


def _score(query_tokens: set, skill_tokens: set) -> float:
    if not query_tokens or not skill_tokens:
        return 0.0
    intersection = query_tokens & skill_tokens
    return len(intersection) / (len(query_tokens | skill_tokens) or 1)


def write_skill(name, content, description=None, domain="general", source="generated") -> Dict:
    db = get_db()
    skill_id = db.upsert_skill(name=name, content=content, description=description,
                                domain=domain, source=source)
    safe_name = re.sub(r"[^\w\-]", "_", name.lower())
    skill_path = _skills_dir() / f"{safe_name}.md"
    with open(skill_path, "w", encoding="utf-8") as f:
        f.write(f"# {name}\n\n")
        if description:
            f.write(f"_{description}_\n\n")
        f.write(content)
    logger.info("Skill written: %s (%s)", name, domain)
    return {"id": skill_id, "name": name, "domain": domain, "path": str(skill_path)}


def get_skill(name) -> Optional[Dict]:
    return get_db().get_skill(name)


def list_skills(domain=None) -> List[Dict]:
    return get_db().list_skills(domain=domain)


def recall_relevant_skills(task_description, domain=None, top_k=5, min_score=0.05) -> List[Dict]:
    skills = get_db().list_skills(domain=domain, limit=200)
    if not skills:
        return []
    query_tokens = _tokenize(task_description)
    scored = []
    for skill in skills:
        skill_text = (skill.get("name") or "") + " " + (skill.get("description") or "") + " " + (skill.get("content") or "")
        score = _score(query_tokens, _tokenize(skill_text))
        if score >= min_score:
            scored.append((score, skill))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:top_k]]


def build_skills_context(task_description, domain=None) -> str:
    skills = recall_relevant_skills(task_description, domain=domain)
    if not skills:
        return ""
    parts = ["<openeye-skills>",
             "[System: The following skills are relevant procedural memory. "
             "Use them to inform your approach.]", ""]
    for skill in skills:
        parts.append(f"## {skill['name']}")
        if skill.get("description"):
            parts.append(f"_{skill['description']}_")
        parts.append(skill.get("content", ""))
        parts.append("")
    parts.append("</openeye-skills>")
    return "\n".join(parts)
