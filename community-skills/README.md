# Community Skills

A PR-driven directory of procedural skills the OpenEye agent can recall.
Anyone can contribute a skill — open a PR adding a markdown file and an
entry to `index.json`.

This is **not a marketplace.** There's no hosting, no ratings, no
download counts. It's a versioned git directory. The bar is "useful to
more than one person, written carefully enough that a stranger could
verify it."

## Install

```bash
# List what's available
python community-skills/install.py --list

# Install one
python community-skills/install.py bolt-assembly-m6

# Install everything in a domain
python community-skills/install.py --domain manufacturing

# Install everything
python community-skills/install.py --all
```

If your sidecar requires a token:
```bash
export OPENEYE_SIDECAR_TOKEN=...
python community-skills/install.py bolt-assembly-m6
```

Installation POSTs to the sidecar's `/skills/write` endpoint — the same
endpoint the agent uses when it writes a new skill itself. After
install, the skill is available in `recall_skill` and gets injected
into agent context when the task description matches.

## Contributing a skill

1. Pick a domain (existing: `manufacturing`, `field-service`, `medical`).
   If none fit, create a new top-level directory.
2. Write a markdown file using the template below.
3. Add an entry to `index.json` (alphabetized within domain).
4. Open a PR.

### Skill template

```markdown
---
name: your-skill-name
domain: manufacturing | field-service | medical | training | other
version: 1.0.0
maintainer: your-github-handle
tags: [tag1, tag2]
---

# Human-Readable Title

One-sentence summary of what this skill verifies.

## Steps

1. First procedural step — what the operator does, what the camera sees.
2. Second step.
3. ...

## Common failure modes

- What goes wrong in the field. Be specific.

## Verification rules

- When to mark `pass` / `fail` / `uncertain`. The agent reads this to
  calibrate its judgments.

## Reward weights (optional)

If your procedure benefits from non-default reward weights, recommend
them here so users can configure via /procedures/reward-config.
```

### What we accept

- Real procedures from real industries (cite the source if it's a public
  standard like OSHA, ISO, AAMI)
- Engineering or training examples explicitly labeled as such
- Updates to existing skills (bump the version)

### What we don't accept

- **Validated clinical / safety-critical protocols without independent
  review.** Anything labeled as a real medical protocol must come with a
  citation to a regulatory or institutional source. Engineering examples
  are fine if labeled — see the advisory field in `index.json`.
- **Proprietary procedures.** Don't submit your employer's internal IP.
- **One-off scripts.** This is for procedures that generalize.

## License

All skill markdown files are MIT-licensed under the repo's main LICENSE.
By submitting a PR you agree to that license.

## How the agent uses these

At session start, OpenEye queries `recall_skill(task, top_k=5)`. The
sidecar ranks installed skills against the task description (Jaccard by
default, sentence-transformers if `OPENEYE_SKILL_RANKER=embeddings`) and
injects the top matches into the system prompt as procedural memory.

A well-written skill changes agent behavior dramatically. A vague one is
noise. Aim for specific verification rules and concrete failure modes.
