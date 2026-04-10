---
name: surgical-site-prep-protocol
description: verification criteria for patient draping and antiseptic preparation before incision
domain: medical
procedure_id: surgical-site-prep
tags: [draping, antiseptic, betadine, chlorhexidine, sterile-field, incision-site]
version: 1.0.0
author: dumbspacecookie
device_types: [hololens, webxr]
reward_threshold: 0.80
---

## surgical site preparation verification protocol

use this skill when verifying surgical site prep steps. covers antiseptic application, draping, and sterile field establishment.

---

### step 1 — antiseptic application

**pass**: antiseptic (betadine or chlorhexidine) applied in concentric circles from incision site outward. application covers the required surface area. correct dwell time observed.

**fail**: antiseptic not applied, wrong agent used, or application pattern moves from dirty to clean area.

**uncertain**: antiseptic visible but application pattern cannot be determined from camera angle.

---

### step 2 — draping

**pass**: sterile drapes placed around the surgical site with adequate margins. no breaks in sterile field. drapes secured and not shifting.

**fail**: drapes not placed, insufficient coverage, or visible contamination of sterile field.

**uncertain**: drapes partially visible, margins cannot be fully assessed from available frames.

---

### step 3 — sterile field verification

**pass**: no non-sterile objects visible within the draped field. all team members maintaining sterile technique within the field boundaries.

**fail**: non-sterile object placed on or passed over the sterile field. break in technique observed.

**uncertain**: partial view of field, cannot confirm all boundaries are maintained.

---

*— dumbspacecookie*
