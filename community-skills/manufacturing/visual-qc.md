---
name: visual-qc-protocol
description: verification criteria for visual quality control inspection of manufactured parts
domain: manufacturing
procedure_id: visual-quality-control
tags: [qc, inspection, surface-finish, dimensional, defect, reject, pass]
version: 1.0.0
author: dumbspacecookie
device_types: [hololens, webxr, snap_spectacles]
reward_threshold: 0.85
---

## visual quality control protocol

use this skill when inspecting manufactured parts for visual defects. covers surface finish, dimensional conformance, and defect classification.

---

### surface finish inspection

**pass**: surface uniformly finished with no visible scratches, pitting, or discoloration. tool marks within specification. finish matches reference sample.

**fail**: visible scratches deeper than surface finish tolerance, pitting, burrs, or discoloration outside acceptable range.

**uncertain**: surface partially in shadow or at glare angle preventing full assessment.

---

### dimensional check

**pass**: part dimensions confirmed within tolerance using go/no-go gauge or caliper visible in frame. measurement tool reading is within spec range.

**fail**: measurement tool shows out-of-tolerance reading, or no-go gauge fits when it shouldn't.

**uncertain**: measurement tool reading partially obscured, cannot confirm exact value.

---

### defect classification

when a defect is detected, classify as:
- **critical**: affects function or safety — reject immediately
- **major**: affects fit or appearance beyond customer tolerance — reject
- **minor**: cosmetic only, within customer tolerance — pass with note

---

*— dumbspacecookie*
