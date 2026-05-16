"""
OpenEye Skills Manager
Procedural memory: create, retrieve, and inject skills into agent context.
Skills are markdown documents the agent writes after completing complex tasks.
At session start, relevant skills are cosine-ranked against the task description
and injected into the system prompt.

Ranking strategy is configurable via OPENEYE_SKILL_RANKER:
  - "jaccard" (default): token-set overlap, zero deps, mediocre quality
  - "embeddings": sentence-transformers cosine, much better, needs install:
      pip install sentence-transformers numpy

If you set "embeddings" but the deps aren't installed, we fall back to
Jaccard with a one-time warning rather than crashing.
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

SKILL_RANKER = os.getenv("OPENEYE_SKILL_RANKER", "jaccard").strip().lower()
EMBED_MODEL = os.getenv("OPENEYE_SKILL_EMBED_MODEL", "all-MiniLM-L6-v2")

# Lazy-initialized embedding model + per-skill embedding cache. The cache
# is keyed by skill text so updates invalidate automatically.
_embed_model = None
_embed_cache: Dict[str, "list"] = {}
_embed_failed = False  # set true after first install failure


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


def _get_embed_model():
    """Lazy-load the sentence-transformers model. Returns None if the
    package isn't installed — caller should fall back to Jaccard."""
    global _embed_model, _embed_failed
    if _embed_model is not None:
        return _embed_model
    if _embed_failed:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
        logger.info("Loaded sentence-transformers model %s for skill recall", EMBED_MODEL)
        return _embed_model
    except ImportError:
        _embed_failed = True
        logger.warning(
            "OPENEYE_SKILL_RANKER=embeddings but sentence-transformers is not installed. "
            "Falling back to Jaccard. Install with: pip install sentence-transformers")
        return None
    except Exception as e:
        _embed_failed = True
        logger.warning("Failed to load embedding model %s: %s. Falling back to Jaccard.",
                       EMBED_MODEL, e)
        return None


def _embed(text: str):
    """Embed a string, using the cache. Returns a numpy array or None."""
    if text in _embed_cache:
        return _embed_cache[text]
    model = _get_embed_model()
    if model is None:
        return None
    vec = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    _embed_cache[text] = vec
    return vec


def _cosine(a, b) -> float:
    """Dot product of two normalized vectors = cosine similarity."""
    try:
        import numpy as np
        return float(np.dot(a, b))
    except ImportError:
        # numpy not available — sum products manually
        return sum(x * y for x, y in zip(a, b))


def _embedding_score(query: str, skill_text: str) -> Optional[float]:
    qv = _embed(query)
    sv = _embed(skill_text)
    if qv is None or sv is None:
        return None
    return _cosine(qv, sv)


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


def _skill_text(skill: Dict) -> str:
    return (
        (skill.get("name") or "") + " "
        + (skill.get("description") or "") + " "
        + (skill.get("content") or "")
    )


def recall_relevant_skills(task_description, domain=None, top_k=5,
                           min_score=0.05) -> List[Dict]:
    """Rank skills by relevance. Strategy switched via OPENEYE_SKILL_RANKER:

      jaccard    — token-set overlap (default; min_score=0.05 = 5% overlap)
      embeddings — sentence-transformers cosine (min_score=0.3 ≈ moderate match)

    Embeddings fall back to Jaccard if sentence-transformers isn't installed.
    """
    skills = get_db().list_skills(domain=domain, limit=200)
    if not skills:
        return []

    use_embeddings = SKILL_RANKER == "embeddings" and _get_embed_model() is not None

    scored: List[Tuple[float, Dict]] = []
    if use_embeddings:
        # Embeddings give a different score scale — 0.3+ is a moderate match,
        # 0.6+ strong. Bump the default minimum unless caller overrode it.
        threshold = min_score if min_score != 0.05 else 0.3
        for skill in skills:
            score = _embedding_score(task_description, _skill_text(skill))
            if score is None:
                # Defensive: model went missing mid-call. Skip rather than crash.
                continue
            if score >= threshold:
                scored.append((score, skill))
    else:
        query_tokens = _tokenize(task_description)
        for skill in skills:
            score = _score(query_tokens, _tokenize(_skill_text(skill)))
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
