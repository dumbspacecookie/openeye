---
name: hand-hygiene-protocol
description: verification criteria for the five-step hand hygiene procedure in clinical settings
domain: medical
procedure_id: hand-hygiene
tags: [hand-washing, gloves, sterile, aseptic, soap, infection-control]
version: 1.0.0
author: dumbspacecookie
device_types: [hololens, webxr, ios]
reward_threshold: 0.75
---

## hand hygiene verification protocol

use this skill when verifying hand hygiene steps in surgical or clinical procedures. the agent should apply these criteria when deciding pass, fail, or uncertain for each step.

---

### step 1 — hand washing

**pass**: both hands visible under running water, antiseptic soap applied and producing visible foam, scrubbing motion present, duration at minimum 15 seconds estimated from frame sequence.

**fail**: water not running, soap absent, hands not in contact with water, or only one hand visible with the other clearly dry.

**uncertain**: one hand off-frame but water and soap present; duration cannot be estimated from available frames; significant occlusion from equipment or personnel.

agent note: soap suds on both palms and between fingers is stronger evidence than soap dispenser visibility alone.

---

### step 2 — soap application

**pass**: foam clearly coating both palms and dorsal surfaces, visible between fingers.

**fail**: no foam visible on hands, or foam only on one hand.

**uncertain**: foam partially visible but extent unclear due to glare, angle, or occlusion.

---

### step 3 — rinse

**pass**: both hands under running water with visible soap residue being rinsed away.

**fail**: hands removed from water with soap still visibly present.

**uncertain**: one hand rinsed, status of second hand unknown.

---

### step 4 — dry with sterile towel

**pass**: sterile paper towel or clean linen towel used to pat-dry both hands. towel is fresh from dispenser or sealed package.

**fail**: hands dried on clothing, reusable cloth of unknown sterility, or not dried at all before gloving.

**uncertain**: towel visible but hands not in frame during drying.

---

### step 5 — sterile glove donning

**pass**: sterile gloves applied to both hands using aseptic technique. packaging from sealed sterile pouch, no contact between outer glove surface and non-sterile objects.

**fail**: non-sterile gloves used, torn glove detected, or contamination event observed.

**uncertain**: glove packaging not visible (cannot confirm sterility), one glove on but second not yet applied.

---

### reward signal calibration

sessions where all five steps are confirmed pass: reward ~0.95
sessions where one step is uncertain: reward ~0.75
sessions where one step is fail: reward ~0.40
sessions where two or more steps fail: reward ~0.10

---

*— dumbspacecookie*
