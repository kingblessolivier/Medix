# 29 — Alerts and warnings

Three severities, one rule about clinical content, and a hard limit on how
many alerts a screen is allowed to raise.

---

## 1 — The three levels

| Level | Token | Behaviour | Example |
|---|---|---|---|
| **Critical** | `--bad` | Hard stop. The transaction cannot complete. | Expired batch · prescription missing for a POM · lapsed licence |
| **Warning** | `--warn` | Soft stop. Proceeding requires an explicit acknowledgement, and the acknowledgement is logged. | Short-dated batch · credit near limit · low stock |
| **Info** | `--info` | Passive. Never interrupts. | Generic alternative held · volume discount available |

Colour is never the only signal. Each level carries its own icon —
`Banner` already enforces this — so the level survives a monochrome
screen and colour-vision deficiency.

**Critical is already implemented as refusal, not decoration.** Medix
raises `DomainError` for these, the API returns 422, and nothing is
written. A critical alert is not a dialog you dismiss; it is a
transaction that did not happen. What this document adds is the two
softer tiers, which have no home today.

---

## 2 — Alert fatigue is the design constraint

Staff who meet six warnings per sale stop reading warnings. At that point
the system is worse than one with no warnings at all, because everyone —
including the pharmacist — believes the checks are working.

Rules:

- **A screen raises at most three warnings.** Beyond that, collapse to
  one summary with a count and a way to expand.
- **A warning that fires on nearly every transaction is a bug**, not a
  safety feature. If more than roughly one sale in five raises a given
  warning, the threshold is wrong and it gets retuned or demoted to info.
- **Never warn about something the user cannot act on** at that moment.
- **Acknowledgement is recorded** to `AuditEvent` — who, when, which
  alert, on which record. An override nobody can trace is not a control.
- Critical never has a "proceed anyway". If a case genuinely needs an
  override it is a Warning with an authorised approver, not a Critical.

---

## 3 — Clinical alerts: what we will and will not build

This is the section that needs a decision rather than an implementation.

`CLAUDE.md` states: **no clinical advice; reference product information
and official leaflet links only.** The clinical alerts requested divide
cleanly against that line, and the division is not the same for all five.

### 3.1 Safe to build — these are data matches

| Alert | Why it is a match, not advice |
|---|---|
| **Allergy contraindication** | Compares a dispensed product's active ingredient against an allergy a pharmacist recorded on that patient. We assert an equality, not a judgement. |
| **Duplicate therapy** | Two products in one therapeutic category, on one prescription. `Product.category` already holds it. |
| **Demographic restriction** | Only if the restriction is a *recorded attribute of the product*, sourced and versioned like any other regulatory configuration — never inferred. |
| **Maximum daily dose** | Same condition: the limit is a stored, dated attribute per product. The system compares; it does not calculate a dose. |

All four surface as **Warning**, not Critical, and all four are addressed
to the pharmacist — who authorises, exactly as they do for a scanned
prescription today.

### 3.2 Not to be hand-built — drug–drug interaction

**We must not author an interactions table.**

Interaction checking is the one item here that is genuinely clinical
decision support. Doing it properly requires a maintained, licensed
clinical database — First Databank, Lexicomp, Medi-Span or a national
equivalent — with severity grading and continuous revision.

A hand-rolled table is not a smaller version of that. It is worse than
nothing, because a pharmacist who sees no warning reasonably concludes
the pair was checked and found safe. Every interaction we failed to
encode becomes a silent assurance we never earned. The liability for
that sits with whoever shipped it.

**The decision needed:** either license an interaction dataset and
integrate it as versioned reference data, or ship no interaction alert at
all and say so plainly in the interface. There is no defensible middle
option, and "a basic list of the common ones" is the indefensible middle.

Until that is decided, the product does not claim interaction checking
anywhere in its copy.

### 3.3 Where the data comes from

Every clinical threshold — maximum dose, pregnancy category, paediatric
restriction — is **effective-dated reference configuration**, on the same
footing as tax rules. A dispensing decision from eight months ago must
remain explainable under the reference data that applied then. It is
never a constant in Python or a literal in React.

---

## 4 — Compliance alerts

| Alert | Level | State |
|---|---|---|
| Prescription required for a POM | Critical | **built** — `PrescriptionRequired` |
| Lapsed or suspended premises licence | Critical | **built** — `LicenceInvalid`, capability derived from held licences |
| Expired product registration blocks listing and dispensing | Critical | partial — `RegistrationInvalid` exists; not yet checked on publish or dispatch |
| Controlled substance quota approaching the legal monthly limit | Warning | **open** |
| Controlled substance register entry on dispensing | Critical | **built** — `_write_controlled_entry` |
| Prescriber registration number recorded before finalising | Critical | **built** |

---

## 5 — Operational alerts

| Alert | Level | State |
|---|---|---|
| Dispensing or shipping an expired batch | Critical | **built** — FEFO excludes expired; `ExpiredBatch` |
| Cold-chain product into an ambient location | Critical | **built** — `ColdChainViolation` |
| Cold-chain temperature excursion from a sensor | Critical | **open** — needs the local agent and `Product.storage_min_c` / `storage_max_c` |
| Batch inside 90 days of expiry | Warning | **open** |
| Stock below reorder point | Warning | **open** — `Product.reorder_point_base` exists; nothing reads it |
| Depot offer exhausted while orders are open | Warning | **open** |

Note: short-dated stock rotates by **FEFO, not FIFO** — see
`docs/28-distribution-spec.md` §2.2.

---

## 6 — Financial alerts

| Alert | Level | State |
|---|---|---|
| Credit limit exceeded — blocks a new shipment | Critical | **open** — `TradingRelationship.credit_limit` exists; nothing reads it |
| Credit at 80% of limit | Warning | **open** |
| Invoice past Net-30 / Net-60 | Warning | **open** — needs receivables ageing |
| Sale price below batch cost | Warning | **open** — `Batch.unit_cost_base` makes this exact per batch |

The margin warning is worth stating precisely: it fires when the price a
counter is about to charge is below what *that batch* cost, not below an
average. Medix knows which batch is leaving, so the check is exact.

---

## 7 — Delivery

An alert is **inline and attached to the thing it is about**. A floating
toast for anything requiring action is forbidden — it disappears, it
cannot be re-read, and it does not survive a page change.

- Blocking and acknowledged alerts: `Banner`, above the affected control.
- Passive info in a table: a `StatusPill` in the row.
- Nothing that must be acted on is ever announced by colour alone.

Alerts needing attention outside the screen — a temperature excursion at
02:00, a receivable crossing 60 days — go out of band and are the only
case for email or SMS. Everything else stays in the interface.

---

## 8 — Queue

1. `Alert` value object and the three severities, with acknowledgement logged
2. Short-dated batch and reorder-point warnings
3. Credit limit — hard block at limit, warning at 80%
4. Product registration expiry blocking publish and dispatch
5. Below-cost price warning
6. Controlled substance quota
7. Clinical checks in §3.1, once the reference data is sourced
8. Interaction checking — **blocked on the §3.2 decision**
