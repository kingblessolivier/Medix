# 23 — UI copy

Medix is a tool, not a document. It shows what to do. It does not explain itself.

A pharmacist at a counter reads a label and acts. Every extra sentence is a delay.

---

## Rules

1. **Label, don't narrate.** `Expiring batches` — not `Here are the batches that are expiring soon`.
2. **Verb first on actions.** `Create order`, `Post GRN`, `Void sale`.
3. **One line maximum.** If it needs two, the design is wrong.
4. **No apologies.** No "sorry", no "unfortunately", no "please".
5. **No reassurance.** Not "Don't worry, your draft is safe" — just `Draft saved`.
6. **No teaching in the interface.** Documentation teaches. The interface states.
7. **Sentence case.** Never Title Case. Never ALL CAPS except 10px group headers.
8. **No terminal punctuation on labels, headings, buttons.** Full stops only in helper text.
9. **Numbers, not adjectives.** `18 expiring` — not `several items need attention`.
10. **Name the object.** `Void SAL-00982` — not `Void this item`.

---

## Length limits

| Element | Max |
|---|---|
| Button | 3 words |
| Label | 3 words |
| Page title | 3 words |
| Page description | 8 words |
| Empty state heading | 5 words |
| Empty state body | 10 words |
| Error | 12 words |
| Tooltip | 8 words |
| Confirmation body | 15 words |

Over the limit means cut, not rephrase.

---

## Rewrites

| ❌ | ✅ |
|---|---|
| We couldn't submit this order. Your connection may have been interrupted. Your draft has been saved. | `Submission failed. Draft saved.` |
| No purchase orders yet. Your approved purchase orders will appear here. | `No purchase orders` |
| Please enter a valid batch number | `Batch number required` |
| Your changes have been saved successfully! | `Saved` |
| Are you sure you want to void this sale? This action cannot be undone. | `Void SAL-00982? Reverses 3 stock movements.` |
| This product requires a prescription before it can be dispensed to the patient | `Prescription required` |
| Monitor stock levels, batches, expiry dates and stock movements | `Stock, batches, expiry` |
| There are currently no results matching your search criteria | `No results for "amoxicilin"` |
| Loading your inventory data, please wait… | *(skeleton — no text)* |
| Click here to create your first purchase order | `Create purchase order` |
| Insurance coverage has been calculated based on the patient's scheme | `Covered 28,000 · Co-pay 7,000` |

---

## Patterns

**Empty state** — heading, action. Body only if the action is non-obvious.

```
No purchase orders
[ Create purchase order ]
```

**Error** — what failed, what to do.

```
Submission failed. Draft saved.
[ Retry ]
```

**Blocked action** — what is missing.

```
Prescription required
[ Attach prescription ]
```

**Confirmation** — object, consequence, action.

```
Void SAL-00982?
Reverses 3 stock movements. Issues a credit note.
[ Cancel ]  [ Void ]
```

**Status** — state only.

```
● Expiring    ● Awaiting approval    ● Quarantined
```

**Progress** — fact, no commentary.

```
Draft saved
Synced 2 min ago
425 of 400 MOQ
```

---

## Banned

| Word | Why |
|---|---|
| please | The tool is not asking a favour |
| sorry, unfortunately | Not an apology |
| successfully | The result is the confirmation |
| simply, just, easy | Presumes |
| click here | The label names the destination |
| oops, whoops | Not a toy |
| ! | Shouty |
| we, our | The system has no first person |
| Are you sure | Say what happens instead |

---

## Where explanation belongs

| Needs explaining | Goes in |
|---|---|
| Why FEFO picked this batch | Tooltip, 8 words |
| What a GRN is | Documentation |
| Why a sale is blocked | The blocked-action pattern |
| How consolidation works | Onboarding, once |
| Regulatory reasoning | Compliance docs |

Never in the working interface.

---

## Domain terms

Use the pharmacist's word, not the system's.

| ❌ | ✅ |
|---|---|
| Stock movement record | Movement |
| Goods received note | Receipt · GRN in the header |
| Unit of measure | Unit |
| Fiscal invoice submission exception | Fiscal exception |
| Consolidated import order participant | Participant |
| Prescription verification | Verify |

Abbreviations the trade already uses — GRN, PO, POM, RFQ, MOQ, EBM — are fine in headers and tables. Never invent one.

---

## Language

**English only.** No localization layer, no translation keys, no i18n library.

Strings live where they are used. Copy is reviewed against the length limits above, not extracted to a catalogue.
