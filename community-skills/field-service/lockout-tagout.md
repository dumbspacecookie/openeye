---
name: lockout-tagout-protocol
description: verification criteria for OSHA-compliant lockout/tagout energy isolation procedures
domain: field-service
procedure_id: lockout-tagout
tags: [loto, energy-isolation, osha, safety, lock, tag, verify]
version: 1.0.0
author: dumbspacecookie
device_types: [hololens, webxr, android, ios]
reward_threshold: 0.90
---

## lockout/tagout verification protocol

use this skill when verifying OSHA-compliant lockout/tagout (LOTO) procedures. high reward threshold because safety-critical — uncertain should be used liberally.

---

### step 1 — energy source identification

**pass**: all energy sources for the equipment identified and listed. includes electrical, pneumatic, hydraulic, mechanical, thermal, chemical, and gravitational as applicable.

**fail**: energy source list incomplete or not present.

**uncertain**: list partially visible, cannot confirm all sources are included.

---

### step 2 — notification

**pass**: affected employees notified. notification board updated or verbal confirmation observed.

**fail**: no evidence of notification.

**uncertain**: cannot determine from visual evidence whether notification occurred.

---

### step 3 — lockout application

**pass**: personal lock applied to each energy isolation point. lock is the correct type (keyed, not combination). tag attached to lock with worker name and date visible.

**fail**: lock missing from any isolation point, wrong lock type, or tag missing/illegible.

**uncertain**: lock visible but tag details cannot be read from camera angle.

---

### step 4 — energy verification

**pass**: attempt to start equipment after lockout — equipment does not start. stored energy dissipated (pressure gauges at zero, capacitors discharged, springs released).

**fail**: equipment starts or moves after lockout attempt. stored energy still present.

**uncertain**: verification attempt not visible in frame sequence.

---

*— dumbspacecookie*
