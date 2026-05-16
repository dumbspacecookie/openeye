# Contributing to OpenEye-Bench

Two kinds of contribution are valuable: **result submissions** and
**dataset proposals**.

## Submitting a result to the leaderboard

See [`leaderboard.md`](leaderboard.md) for the rules. Quick version:

1. Run `runner.py` unmodified against `dataset_v1.json`.
2. Open a PR adding one row to `leaderboard.md` and attaching the
   generated `<label>.json`.
3. A maintainer merges within a few days.

We do not gatekeep based on model size, license, or provider. We DO
verify that the runner is unmodified and the dataset hash matches.

## Proposing new examples

`dataset_v1.json` is frozen. Proposals become `dataset_v2.json` once
we accumulate enough community submissions on v1 to make a v2
meaningful (target: 5+ teams on the leaderboard).

When that happens, the rules for v2 examples will be:

1. **Real procedure.** Examples must come from procedures that exist
   somewhere (industry standard, safety regulation, public protocol).
   We won't accept made-up procedures.
2. **Specific step, not whole procedure.** "Wash hands properly" is too
   broad. "Apply soap to both palms for ≥3 seconds" is right.
3. **Camera-style scene description.** Like the v1 examples — what a
   camera would observe and what a vision adapter would write down.
   No internal monologue, no metadata.
4. **One unambiguous expected verdict.** With a written rationale.
   If reviewers can't agree on pass/fail/uncertain, the example is
   ambiguous and doesn't belong.
5. **Balanced contribution.** Don't submit 20 examples that are all
   `fail`. Distribution should roughly match v1 (~45% pass, ~35% fail,
   ~20% uncertain).

## Reporting a bad example

If you think a v1 example has the wrong expected verdict, open an issue
with:
- The example `id` (e.g. `bolt-assembly-03`)
- Your proposed verdict
- Why
- (Ideally) two other people who agree with you

We do not silently fix v1 — we'd be erasing prior results. If consensus
is the example is wrong, it gets marked `excluded_from_v2` in the v2
release and v1 stays untouched.

## Code of conduct

Be specific, be respectful, cite when you can. This benchmark is
small enough that one bad-faith submission can poison it — we'll
revert and ban if necessary.
