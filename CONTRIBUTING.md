# contributing to OpenEye

thanks for being here. if you're reading this, you're probably looking at a project that's still early and moving fast. that's exactly when contributions matter most.

---

## what we need

**community skills** — procedure verification protocols for new domains. if you work in healthcare, manufacturing, field service, or any other domain where someone needs to verify that a physical task was done correctly, your domain knowledge is the most valuable thing you can contribute.

**model benchmarks** — run the eval set against different models and share results. the eval framework is built (`eval/scripts/run_eval.py`), we just need data points.

**bug reports** — if something doesn't work, open an issue. include the error, what you expected, and what actually happened.

**integrations** — new model providers, new deployment targets, new device types.

---

## skills contributions

skills are markdown files with YAML front matter that teach the agent how to verify procedures. see `skills/` for examples.

```bash
# validate your skill before submitting
python sidecar/validate_skill.py path/to/your-skill.md
```

requirements:
- YAML front matter with: name, description, domain, procedure_id, tags, version, author
- name must be kebab-case
- domain must be: medical, manufacturing, field-service, or general
- description must be under 120 characters
- version must be semantic (e.g. 1.0.0)
- body must include pass/fail/uncertain criteria for each step

---

## code contributions

1. fork the repo
2. create a branch (`git checkout -b my-feature`)
3. make your changes
4. run tests:
   ```bash
   python -m pytest tests/ -v
   npm test
   npm run typecheck
   ```
5. open a PR with a clear description

keep PRs focused. one feature or fix per PR. if you're touching both Python and TypeScript, that's fine — just make sure both sides have tests.

---

## code style

- TypeScript: ESM, strict mode, no `any` unless interfacing with pi framework types
- Python: PEP 8, type hints where practical, no external deps beyond FastAPI/uvicorn
- both: no comments explaining what code does — write clear code instead. comments for *why*, not *what*

---

## architecture notes

OpenEye has two halves:

**TypeScript** (`src/`) — the agent runtime. imports from `@mariozechner/pi-agent-core` and `@mariozechner/pi-ai`. this is what the developer interacts with.

**Python** (`sidecar/`) — the state engine. SQLite with FTS5, skills management, trajectory capture. the TypeScript client spawns this as a subprocess and talks to it via localhost HTTP.

the split is intentional. the agent runtime needs to be in the same process as the LLM streaming pipeline (TypeScript/pi). the state engine needs SQLite with FTS5 (Python). they talk over HTTP so either side can be replaced independently.

---

*— dumbspacecookie*
