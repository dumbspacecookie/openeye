---
name: bolt-assembly-m6
domain: manufacturing
version: 1.0.0
maintainer: dumbspacecookie
tags: [assembly, torque, fastening]
---

# M6 Bolt Assembly — Bracket to Frame

Verify operator correctly installs M6 bolts joining a bracket to a frame.

## Steps

1. **Position bracket on frame mounting points.** Bracket holes align with
   frame holes; bracket sits flush; no shims.
2. **Insert M6 bolt through bracket hole into frame.** Bolt enters
   mounting hole (not secondary alignment hole). Threads engage.
3. **Hand-tighten bolt clockwise until snug.** Bolt head approaches but
   does not yet seat against bracket. No tools yet.
4. **Torque to 12 Nm with calibrated wrench.** Calibrated torque wrench
   (not adjustable, not breaker bar). Audible/tactile click at spec.

## Common failure modes

- Wrong hole: bolt in secondary alignment hole rather than structural
  mounting hole. The structural hole is the larger, deeper one.
- Wrong tool: uncalibrated wrench used in step 4. The torque-spec step
  cannot be satisfied without calibration.
- Cross-thread: visible thread damage on bolt after insertion. Often
  caused by trying to power through misalignment.
- Under-torque: bolt visibly loose after wrench engagement, or wrench
  rotated more than ~90° at "click."

## Verification rules

- `pass` only when: correct hole + correct tool + bolt seated + torque
  achieved.
- `fail` when: wrong hole / wrong tool / visible cross-thread / bolt
  not seated after torque step.
- `uncertain` when: hole and tool correct, but camera angle obscures
  whether torque was actually achieved (e.g. operator's body blocks
  the wrench at the click moment).

## Reward weights

Default `(1.0, 0.5, 0.0)` is fine for this procedure. Tighten to
`(1.0, 0.3, -0.5)` if you're using the data for training and want
the model to lean toward calling out fail conditions clearly.
