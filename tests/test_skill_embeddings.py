"""
Embedding-based skill recall tests. Verifies:
  - Default backend stays Jaccard (no behavior change for existing users)
  - Switching to embeddings without sentence-transformers falls back gracefully
  - When sentence-transformers IS installed, embeddings path is used (smoke test)

Run: python -m pytest tests/test_skill_embeddings.py -v
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import state as state_module  # noqa: E402
from state import OpenEyeDB  # noqa: E402


def _reload_skills_with_env(env: dict):
    """Set env vars, reload skills module so module-level constants update."""
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import skills
    importlib.reload(skills)
    return skills


class TestDefaultIsJaccard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db
        self.skills = _reload_skills_with_env({"OPENEYE_SKILL_RANKER": None})

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    def test_default_ranker_is_jaccard(self):
        self.assertEqual(self.skills.SKILL_RANKER, "jaccard")

    def test_jaccard_recall_works(self):
        self.db.upsert_skill("hand-hygiene-check", "verify hand washing")
        self.db.upsert_skill("equipment-check", "verify equipment startup")
        results = self.skills.recall_relevant_skills("verify hand hygiene")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "hand-hygiene-check")


class TestEmbeddingFallback(unittest.TestCase):
    """When OPENEYE_SKILL_RANKER=embeddings but sentence-transformers isn't
    importable, we silently fall back to Jaccard. No crash, useful warning."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db
        self.skills = _reload_skills_with_env({"OPENEYE_SKILL_RANKER": "embeddings"})

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    def test_falls_back_to_jaccard_when_no_sentence_transformers(self):
        # Simulate the import failing
        with mock.patch.dict(sys.modules, {"sentence_transformers": None}):
            # Reset the cached failure flag and model so the import is retried
            self.skills._embed_model = None
            self.skills._embed_failed = False

            # Force the import to fail by removing the module from sys.modules
            # and patching importlib so it raises
            with mock.patch.object(
                self.skills, "_get_embed_model", return_value=None
            ):
                self.db.upsert_skill("safety-check", "verify safety procedures")
                results = self.skills.recall_relevant_skills("verify safety procedure")
                # Jaccard would match — embedding path returned None — fall back kicks in
                self.assertGreater(len(results), 0)
                self.assertEqual(results[0]["name"], "safety-check")

    def test_get_embed_model_returns_none_after_failure(self):
        self.skills._embed_failed = True
        self.skills._embed_model = None
        self.assertIsNone(self.skills._get_embed_model())


class TestEmbeddingPathWhenAvailable(unittest.TestCase):
    """If sentence-transformers IS installed in this env, verify the
    embedding path actually engages and produces sensible results."""

    def setUp(self):
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            self.skipTest("sentence-transformers not installed — skipping")

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db
        self.skills = _reload_skills_with_env({"OPENEYE_SKILL_RANKER": "embeddings"})

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
            state_module._db_instance = None
            os.unlink(self.tmp.name)

    def test_semantic_match_beats_lexical(self):
        # "Aseptic protocol" should match "sterile field maintenance" via
        # embeddings even though they share no surface tokens.
        self.db.upsert_skill("sterile-field",
                             "maintain sterile field during surgical procedures")
        self.db.upsert_skill("equipment-startup",
                             "boot industrial machinery sequence")
        results = self.skills.recall_relevant_skills(
            "aseptic protocol for the OR", top_k=1)
        # Embeddings should surface the sterile-field skill first
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "sterile-field")


if __name__ == "__main__":
    unittest.main()
