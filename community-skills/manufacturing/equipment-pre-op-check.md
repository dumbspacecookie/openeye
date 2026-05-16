---
name: equipment-pre-op-check-protocol
description: verification criteria for the four-step pre-operation equipment check on CNC and industrial machinery
domain: manufacturing
procedure_id: equipment-check
tags: [power, guards, fluid-levels, calibration, cnc, industrial, safety]
version: 1.0.0
author: dumbspacecookie
device_types: [hololens, webxr, android]
reward_threshold: 0.80
---

## equipment pre-operation check protocol

use this skill when verifying equipment readiness before operation. covers power systems, safety guards, fluid levels, and calibration.

---

### step 1 — power system check

**pass**: main power indicator illuminated, emergency stop button in released (ready) position, no warning lights active on control panel.

**fail**: power indicator off, emergency stop engaged, or warning lights active.

**uncertain**: control panel partially obscured, cannot confirm all indicator states.

---

### step 2 — safety guard verification

**pass**: all safety guards in closed position, interlock indicators green, no visible damage to guard surfaces.

**fail**: guard open or missing, interlock bypass detected, visible damage compromising guard integrity.

**uncertain**: one or more guards not visible from current camera angle.

---

### step 3 — fluid level check

**pass**: coolant, hydraulic, and lubrication levels within marked operating range. no visible leaks on floor or machine surfaces.

**fail**: any fluid below minimum mark, visible leak detected, or fluid discoloration suggesting contamination.

**uncertain**: sight glass obscured by condensation or glare, level cannot be read clearly.

---

### step 4 — calibration verification

**pass**: calibration sticker current (not expired), test cut or reference measurement within tolerance, zero-point confirmed.

**fail**: calibration expired, test measurement out of tolerance, or zero-point drift detected.

**uncertain**: calibration sticker not visible, cannot determine expiry date from frame.

---

*— dumbspacecookie*
