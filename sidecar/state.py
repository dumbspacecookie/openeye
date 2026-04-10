"""
OpenEye State Engine
Extends the Hermes SQLite state engine with visual session and frame metadata.
Thread-safe, WAL mode, FTS5 full-text search on text + frame descriptions.

Tables added beyond Hermes core:
  - visual_sessions: AR/XR session metadata (device, user, procedure)
  - frames: individual captured frames with scene descriptions
  - step_verifications: per-frame step check results
  - trajectories: RL trajectory records ready for batch_runner

Opt-in cloud sync: any row with sync_pending=1 is picked up by the sync worker.
"""

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

OPENEYE_HOME = Path(os.getenv("OPENEYE_HOME", Path.home() / ".openeye"))
DB_PATH = OPENEYE_HOME / "openeye.db"
SCHEMA_VERSION = 1


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- ── Agent sessions (ported from Hermes) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'cli',
    user_id         TEXT,
    tenant_id       TEXT,
    model           TEXT,
    system_prompt   TEXT,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    end_reason      TEXT,
    message_count   INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    estimated_cost  REAL,
    title           TEXT,
    sync_pending    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    role         TEXT NOT NULL,
    content      TEXT,
    tool_calls   TEXT,
    tool_name    TEXT,
    timestamp    REAL NOT NULL,
    token_count  INTEGER,
    finish_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_source  ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);

-- ── Visual sessions ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS visual_sessions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES sessions(id),
    tenant_id       TEXT,
    device_type     TEXT,
    device_id       TEXT,
    procedure_id    TEXT,
    procedure_name  TEXT,
    user_id         TEXT,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    frame_count     INTEGER DEFAULT 0,
    step_count      INTEGER DEFAULT 0,
    steps_verified  INTEGER DEFAULT 0,
    outcome         TEXT,
    metadata        TEXT,
    sync_pending    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_vsessions_tenant    ON visual_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vsessions_procedure ON visual_sessions(procedure_id);
CREATE INDEX IF NOT EXISTS idx_vsessions_started   ON visual_sessions(started_at DESC);

-- ── Frames ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS frames (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    visual_session_id TEXT NOT NULL REFERENCES visual_sessions(id),
    tenant_id        TEXT,
    sequence_num     INTEGER NOT NULL,
    captured_at      REAL NOT NULL,
    width            INTEGER,
    height           INTEGER,
    scene_description TEXT,
    objects_detected  TEXT,
    step_context     TEXT,
    embedding_ref    TEXT,
    confidence       REAL,
    sync_pending     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_frames_vsession ON frames(visual_session_id, sequence_num);
CREATE INDEX IF NOT EXISTS idx_frames_tenant   ON frames(tenant_id);

-- ── Step verifications ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS step_verifications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id          INTEGER REFERENCES frames(id),
    visual_session_id TEXT NOT NULL REFERENCES visual_sessions(id),
    tenant_id         TEXT,
    step_id           TEXT NOT NULL,
    step_name         TEXT,
    result            TEXT NOT NULL,
    confidence        REAL,
    reasoning         TEXT,
    model_used        TEXT,
    latency_ms        INTEGER,
    verified_at       REAL NOT NULL,
    sync_pending      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_stepverif_vsession ON step_verifications(visual_session_id);
CREATE INDEX IF NOT EXISTS idx_stepverif_step     ON step_verifications(step_id);

-- ── Skills (procedural memory) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT,
    content      TEXT NOT NULL,
    domain       TEXT DEFAULT 'general',
    use_count    INTEGER DEFAULT 0,
    last_used    REAL,
    created_at   REAL NOT NULL,
    source       TEXT DEFAULT 'generated',
    sync_pending INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_skills_domain ON skills(domain);
CREATE INDEX IF NOT EXISTS idx_skills_use    ON skills(use_count DESC);

-- ── RL Trajectories ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trajectories (
    id              TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES sessions(id),
    visual_session_id TEXT REFERENCES visual_sessions(id),
    tenant_id       TEXT,
    model           TEXT,
    completed       INTEGER NOT NULL DEFAULT 0,
    conversations   TEXT NOT NULL,
    reward_signal   REAL,
    tags            TEXT,
    created_at      REAL NOT NULL,
    exported_at     REAL,
    sync_pending    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_traj_tenant    ON trajectories(tenant_id);
CREATE INDEX IF NOT EXISTS idx_traj_completed ON trajectories(completed);
CREATE INDEX IF NOT EXISTS idx_traj_created   ON trajectories(created_at DESC);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(
    scene_description,
    content=frames,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS frames_fts_insert AFTER INSERT ON frames BEGIN
    INSERT INTO frames_fts(rowid, scene_description)
    VALUES (new.id, new.scene_description);
END;
CREATE TRIGGER IF NOT EXISTS frames_fts_delete AFTER DELETE ON frames BEGIN
    INSERT INTO frames_fts(frames_fts, rowid, scene_description)
    VALUES('delete', old.id, old.scene_description);
END;
CREATE TRIGGER IF NOT EXISTS frames_fts_update AFTER UPDATE ON frames BEGIN
    INSERT INTO frames_fts(frames_fts, rowid, scene_description)
    VALUES('delete', old.id, old.scene_description);
    INSERT INTO frames_fts(rowid, scene_description) VALUES (new.id, new.scene_description);
END;
"""


class OpenEyeDB:
    """Thread-safe SQLite state store for OpenEye. Singleton via get_db()."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        cursor = self._conn.cursor()
        cursor.executescript(CORE_SCHEMA)
        cursor.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        for table in ("messages_fts", "frames_fts"):
            try:
                cursor.execute(f"SELECT * FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                cursor.executescript(FTS_SCHEMA)
                break
        self._conn.commit()

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ── Sessions ─────────────────────────────────────────────────────────────

    def create_session(self, source="cli", user_id=None, tenant_id=None,
                       model=None, system_prompt=None, title=None) -> str:
        sid = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                """INSERT INTO sessions
                   (id, source, user_id, tenant_id, model, system_prompt, started_at, title)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (sid, source, user_id, tenant_id, model, system_prompt, time.time(), title))
            self._conn.commit()
        return sid

    def end_session(self, session_id: str, reason: str = "normal"):
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
                (time.time(), reason, session_id))
            self._conn.commit()

    def list_sessions(self, user_id=None, tenant_id=None, source=None,
                      limit=100, exclude_reason=None) -> list:
        clauses, params = [], []
        if user_id:        clauses.append("user_id = ?");      params.append(user_id)
        if tenant_id:      clauses.append("tenant_id = ?");    params.append(tenant_id)
        if source:         clauses.append("source = ?");       params.append(source)
        if exclude_reason: clauses.append("end_reason != ?");  params.append(exclude_reason)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            cursor = self._conn.execute(
                f"SELECT * FROM sessions {where} ORDER BY started_at DESC LIMIT ?", params)
            return [dict(r) for r in cursor.fetchall()]

    def append_message(self, session_id, role, content=None, tool_calls=None,
                       tool_name=None, token_count=None, finish_reason=None) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, tool_calls, tool_name, timestamp, token_count, finish_reason)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (session_id, role, content,
                 json.dumps(tool_calls) if tool_calls else None,
                 tool_name, time.time(), token_count, finish_reason))
            msg_id = cursor.lastrowid
            n_tools = len(tool_calls) if isinstance(tool_calls, list) else (1 if tool_calls else 0)
            self._conn.execute(
                """UPDATE sessions SET message_count = message_count + 1,
                   tool_call_count = tool_call_count + ? WHERE id=?""",
                (n_tools, session_id))
            self._conn.commit()
        return msg_id

    def get_messages(self, session_id: str) -> List[Dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY timestamp, id",
                (session_id,))
            rows = cursor.fetchall()
        result = []
        for r in rows:
            m = dict(r)
            if m.get("tool_calls"):
                try: m["tool_calls"] = json.loads(m["tool_calls"])
                except Exception: pass
            result.append(m)
        return result

    # ── FTS search ────────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_fts(query: str) -> str:
        query = re.sub(r'[+{}()"^]', " ", query)
        query = re.sub(r"\*+", "*", query)
        query = re.sub(r"(^|\s)\*", r"\1", query)
        query = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", query.strip())
        query = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", query.strip())
        return query.strip()

    def search_messages(self, query, tenant_id=None, limit=20) -> List[Dict]:
        q = self._sanitize_fts(query)
        if not q: return []
        sql = """
            SELECT m.id, m.session_id, m.role,
                   snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
                   m.timestamp, s.model, s.source
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE messages_fts MATCH ?
            {} ORDER BY rank LIMIT ?
        """.format("AND s.tenant_id = ?" if tenant_id else "")
        params = [q] + ([tenant_id] if tenant_id else []) + [limit]
        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
                return [dict(r) for r in cursor.fetchall()]
            except sqlite3.OperationalError:
                return []

    def search_frames(self, query, tenant_id=None, procedure_id=None, limit=20) -> List[Dict]:
        q = self._sanitize_fts(query)
        if not q: return []
        clauses, params = [], [q]
        if tenant_id:    clauses.append("f.tenant_id = ?");    params.append(tenant_id)
        if procedure_id: clauses.append("vs.procedure_id = ?"); params.append(procedure_id)
        where = ("AND " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT f.id, f.visual_session_id, f.sequence_num, f.captured_at,
                   snippet(frames_fts, 0, '>>>', '<<<', '...', 60) AS snippet,
                   f.step_context, f.confidence, f.objects_detected,
                   vs.procedure_name, vs.device_type
            FROM frames_fts
            JOIN frames f ON f.id = frames_fts.rowid
            JOIN visual_sessions vs ON vs.id = f.visual_session_id
            WHERE frames_fts MATCH ? {where} ORDER BY rank LIMIT ?
        """
        params.append(limit)
        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
                return [dict(r) for r in cursor.fetchall()]
            except sqlite3.OperationalError:
                return []

    # ── Visual sessions ───────────────────────────────────────────────────────

    def create_visual_session(self, device_type, device_id=None, procedure_id=None,
                              procedure_name=None, user_id=None, tenant_id=None,
                              session_id=None, metadata=None) -> str:
        vsid = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                """INSERT INTO visual_sessions
                   (id, session_id, tenant_id, device_type, device_id,
                    procedure_id, procedure_name, user_id, started_at, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (vsid, session_id, tenant_id, device_type, device_id,
                 procedure_id, procedure_name, user_id, time.time(),
                 json.dumps(metadata) if metadata else None))
            self._conn.commit()
        return vsid

    def end_visual_session(self, vsid, outcome="completed"):
        with self._lock:
            self._conn.execute(
                "UPDATE visual_sessions SET ended_at=?, outcome=? WHERE id=?",
                (time.time(), outcome, vsid))
            self._conn.commit()

    def get_visual_session(self, vsid) -> Optional[Dict]:
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM visual_sessions WHERE id=?", (vsid,))
            row = cursor.fetchone()
        return dict(row) if row else None

    # ── Frames ────────────────────────────────────────────────────────────────

    def log_frame(self, visual_session_id, sequence_num, scene_description,
                  tenant_id=None, width=None, height=None, objects_detected=None,
                  step_context=None, embedding_ref=None, confidence=None, mark_sync=False) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO frames
                   (visual_session_id, tenant_id, sequence_num, captured_at,
                    width, height, scene_description, objects_detected,
                    step_context, embedding_ref, confidence, sync_pending)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (visual_session_id, tenant_id, sequence_num, time.time(),
                 width, height, scene_description,
                 json.dumps(objects_detected) if objects_detected else None,
                 step_context, embedding_ref, confidence, 1 if mark_sync else 0))
            frame_id = cursor.lastrowid
            self._conn.execute(
                "UPDATE visual_sessions SET frame_count = frame_count + 1 WHERE id=?",
                (visual_session_id,))
            self._conn.commit()
        return frame_id

    # ── Step verifications ────────────────────────────────────────────────────

    def log_step_verification(self, visual_session_id, step_id, result,
                              frame_id=None, step_name=None, confidence=None,
                              reasoning=None, model_used=None, latency_ms=None,
                              tenant_id=None, mark_sync=False) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO step_verifications
                   (frame_id, visual_session_id, tenant_id, step_id, step_name,
                    result, confidence, reasoning, model_used, latency_ms,
                    verified_at, sync_pending)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (frame_id, visual_session_id, tenant_id, step_id, step_name,
                 result, confidence, reasoning, model_used, latency_ms,
                 time.time(), 1 if mark_sync else 0))
            vid = cursor.lastrowid
            self._conn.execute(
                """UPDATE visual_sessions SET step_count = step_count + 1,
                   steps_verified = steps_verified + ? WHERE id=?""",
                (1 if result == "pass" else 0, visual_session_id))
            self._conn.commit()
        return vid

    # ── Skills ────────────────────────────────────────────────────────────────

    def upsert_skill(self, name, content, description=None, domain="general", source="generated") -> str:
        skill_id = str(uuid.uuid4())
        with self._lock:
            existing = self._conn.execute("SELECT id FROM skills WHERE name=?", (name,)).fetchone()
            if existing:
                skill_id = existing["id"]
                self._conn.execute(
                    """UPDATE skills SET content=?, description=?, domain=?,
                       source=?, sync_pending=1 WHERE id=?""",
                    (content, description, domain, source, skill_id))
            else:
                self._conn.execute(
                    """INSERT INTO skills
                       (id, name, description, content, domain, source, created_at, sync_pending)
                       VALUES (?,?,?,?,?,?,?,1)""",
                    (skill_id, name, description, content, domain, source, time.time()))
            self._conn.commit()
        return skill_id

    def get_skill(self, name) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE skills SET use_count=use_count+1, last_used=? WHERE name=?",
                    (time.time(), name))
                self._conn.commit()
                row = self._conn.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
                return dict(row)
        return None

    def list_skills(self, domain=None, limit=50) -> List[Dict]:
        with self._lock:
            if domain:
                cursor = self._conn.execute(
                    "SELECT * FROM skills WHERE domain=? ORDER BY use_count DESC LIMIT ?",
                    (domain, limit))
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM skills ORDER BY use_count DESC LIMIT ?", (limit,))
            return [dict(r) for r in cursor.fetchall()]

    # ── Trajectories ─────────────────────────────────────────────────────────

    def save_trajectory(self, conversations, model, completed, session_id=None,
                        visual_session_id=None, tenant_id=None, reward_signal=None,
                        tags=None, mark_sync=False) -> str:
        tid = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                """INSERT INTO trajectories
                   (id, session_id, visual_session_id, tenant_id, model,
                    completed, conversations, reward_signal, tags, created_at, sync_pending)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, session_id, visual_session_id, tenant_id, model,
                 1 if completed else 0,
                 json.dumps(conversations, ensure_ascii=False),
                 reward_signal,
                 json.dumps(tags) if tags else None,
                 time.time(), 1 if mark_sync else 0))
            self._conn.commit()
        return tid

    def get_unsynced_trajectories(self, limit=100) -> List[Dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM trajectories WHERE sync_pending=1 ORDER BY created_at LIMIT ?",
                (limit,))
            rows = cursor.fetchall()
        result = []
        for r in rows:
            t = dict(r)
            try: t["conversations"] = json.loads(t["conversations"])
            except Exception: pass
            result.append(t)
        return result

    def mark_trajectory_synced(self, trajectory_id):
        with self._lock:
            self._conn.execute(
                "UPDATE trajectories SET sync_pending=0, exported_at=? WHERE id=?",
                (time.time(), trajectory_id))
            self._conn.commit()

    def export_trajectories_jsonl(self, output_path, completed_only=True) -> int:
        with self._lock:
            q = "SELECT * FROM trajectories"
            if completed_only: q += " WHERE completed=1"
            q += " ORDER BY created_at"
            cursor = self._conn.execute(q)
            rows = cursor.fetchall()
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for row in rows:
                t = dict(row)
                try: t["conversations"] = json.loads(t["conversations"])
                except Exception: pass
                entry = {
                    "conversations": t["conversations"],
                    "timestamp": t["created_at"],
                    "model": t["model"],
                    "completed": bool(t["completed"]),
                    "openeye_meta": {
                        "visual_session_id": t["visual_session_id"],
                        "tenant_id": t["tenant_id"],
                        "reward_signal": t["reward_signal"],
                        "tags": json.loads(t["tags"]) if t["tags"] else [],
                    },
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                count += 1
        return count

    # ── Cloud sync helpers ────────────────────────────────────────────────────

    def get_pending_sync(self, table, limit=50) -> List[Dict]:
        allowed = {"visual_sessions", "frames", "step_verifications", "trajectories", "skills"}
        if table not in allowed:
            raise ValueError(f"Unknown table: {table}")
        with self._lock:
            cursor = self._conn.execute(
                f"SELECT * FROM {table} WHERE sync_pending=1 LIMIT ?", (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def mark_synced(self, table, row_ids):
        allowed = {"visual_sessions", "frames", "step_verifications", "trajectories", "skills"}
        if table not in allowed:
            raise ValueError(f"Unknown table: {table}")
        placeholders = ",".join("?" for _ in row_ids)
        with self._lock:
            self._conn.execute(
                f"UPDATE {table} SET sync_pending=0 WHERE id IN ({placeholders})", row_ids)
            self._conn.commit()


# ─────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────

_db_instance: Optional[OpenEyeDB] = None
_db_lock = threading.Lock()


def get_db() -> OpenEyeDB:
    global _db_instance
    with _db_lock:
        if _db_instance is None:
            db_path = Path(os.getenv("OPENEYE_HOME", str(Path.home() / ".openeye"))) / "openeye.db"
            _db_instance = OpenEyeDB(db_path)
    return _db_instance
