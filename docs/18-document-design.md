# 18 — Document design

Medix produces roughly thirty-five document types. They are the artifacts that leave the system — a supplier receives the purchase order, a customer receives the receipt, an inspector reads the disposal certificate, an insurer reads the claim. **They are the product's public face more often than the UI is.**

> **The standard: these are modern designed documents, not digitized paperwork.**

No analog aesthetic. No boxed-form look with heavy rules and cramped grids. No default HTML-to-PDF output with Times New Roman and blue underlined links. A Medix invoice should sit comfortably next to a Stripe receipt, not next to a carbon-copy pad.

---

## What "not analog" means concretely

| Reject | Adopt |
|---|---|
| Heavy boxed grids, every field in a ruled cell | Whitespace and typographic hierarchy; hairlines only where they separate meaning |
| Full borders around the whole page | No page border. The margin is the frame |
| ALL-CAPS field labels in bold | Small caps-scale labels at 8pt, letter-spaced, in muted grey |
| Centred everything | Left-aligned text, right-aligned numbers, deliberate asymmetry |
| Dot leaders `......` | Alignment does the work |
| Times New Roman / Arial defaults | Inter throughout, matching the application |
| Coloured table row banding | Hairline separators; no zebra striping |
| A logo the size of a fist | Wordmark at a restrained scale in the header |
| "Page 1 of 3" alone in the corner | A structured footer: document number, issuer, page, verification reference |
| Signature lines drawn as long underscores | A named signature block with role, date and — where applicable — a system attestation |
| Stamped-looking status graphics | A quiet status pill in the header, same semantics as the UI |

---

## Anatomy

Every document uses the same five-region structure. Only the body varies by type.

```
┌─────────────────────────────────────────────────────────────┐
│  MASTHEAD                                                   │
│  Medix wordmark        Document type · number · status pill │
│  Issuer block                              Meta block       │
├─────────────────────────────────────────────────────────────┤
│  PARTIES                                                    │
│  From (issuer)                    To (counterparty)         │
├─────────────────────────────────────────────────────────────┤
│  BODY                                                       │
│  line items · sections · totals — varies by type            │
├─────────────────────────────────────────────────────────────┤
│  ATTESTATION                                                │
│  terms · approvals · signature blocks · regulatory notes    │
├─────────────────────────────────────────────────────────────┤
│  FOOTER   number · issuer · verification · page n of m      │
└─────────────────────────────────────────────────────────────┘
```

### Masthead

Document type in the page-title role, the number immediately beneath in mono, and a status pill on the same baseline. The meta block on the right carries date, reference documents, and any dates that matter to this type (due date, expiry, delivery date) as a two-column key/value list — never a boxed table.

### Parties

Two columns, `FROM` and `TO` labels in the small label style. Each block: legal name, premises licence number where the document requires it, TIN, address, contact. For a fiscal invoice this block is regulated content — treat it as data, not decoration.

### Body

Line items in a table with **no vertical rules and no row banding**. A hairline under the header row, a hairline above the totals. Numeric columns right-aligned with tabular figures. Batch numbers, document references and codes in mono.

Totals sit in a right-aligned block, no box around them, with the grand total separated by a slightly stronger rule and set one step up in size and weight.

### Attestation

Terms, approvals, and signature blocks. A signature block is a named region — role, name, date — not a drawn line. Where a step was performed in-system, print the attestation instead of an empty line:

```
Approved in Medix by Marie Uwase · Responsible Pharmacist · 11 Aug 2026 14:22
```

### Footer

Repeats on every page: document number, issuing organization, verification reference, page *n* of *m*.

---

## Verification block

Every externally-facing document carries a QR code resolving to a verification URL, plus the reference in text so it survives photocopying. This is what replaces the analog rubber stamp — it is the modern equivalent of "this document is genuine".

Fiscal invoices additionally carry whatever the RRA response mandates (fiscal signature, device identifiers, and the RRA-specified QR content). **RRA-mandated content is authoritative and overrides our layout preferences** wherever the two conflict.

---

## Typography

Inter throughout, matching the application. Print scale is smaller than screen scale.

| Role | Size | Weight | Notes |
|---|---|---|---|
| Document type | 20pt | 600 | Masthead |
| Document number | 11pt | 500 | Mono |
| Section label | 8pt | 600 | Letter-spacing 0.08em, muted, uppercase — the only uppercase in the document |
| Body / line item | 9.5pt | 400 | |
| Table header | 8.5pt | 600 | Muted |
| Grand total | 13pt | 600 | |
| Footer | 7.5pt | 400 | Muted |

Numbers always `font-variant-numeric: tabular-nums`. Money right-aligned with the currency code in the column header, not repeated on every row.

---

## Colour in print

Documents are **near-monochrome**. They must remain fully legible when printed on a monochrome thermal or laser printer, which most pharmacies use.

- Text `#17212B`, muted `#5F6B76`
- Hairlines `#DCE2E8`
- Brand blue appears **once** — the wordmark. Not on rules, not on headers, not on totals
- Semantic colour only on a status pill, and every pill also carries its label as text so it survives monochrome printing
- No background fills except an optional very light tint on a totals block

---

## Page setup

A4 (210 × 297 mm), portrait, margins 18 mm sides, 16 mm top, 20 mm bottom to leave room for the repeating footer.

Thermal receipts are a separate template family at 58 mm and 80 mm widths with their own scale — same hierarchy principles, no A4 assumptions.

---

## Web preview parity

**The web preview and the PDF are the same document.** Not a similar-looking HTML approximation.

Implementation: one template renders both. The web view is the same HTML with a screen stylesheet; the PDF is that HTML rendered headlessly with the print stylesheet. Differences are limited to viewport chrome — never content, layout, or type.

A user who previews then downloads must receive exactly what they saw. If those two ever diverge, it is a bug, not a variation.

---

## Numbering

```
{PREFIX}-{YEAR}-{SEQUENCE}
SAL-2026-00982 · GRN-2026-00412 · PO-2026-00124 · IR-2026-00082
```

Gap-free per organization, per type, per year, allocated by `core.sequences` under a database lock so concurrency cannot produce duplicates or gaps. A gap in a fiscal or controlled-substance sequence is an audit finding, so the sequence service is written and tested as if it were.

---

## Document register

| Category | Documents |
|---|---|
| **Procurement** | RFQ · Quotation · Purchase order · Order confirmation |
| **Import** | Import request · Consolidated import order · Shipment manifest · Commercial invoice · Packing list · Regulatory documentation · Inspection record |
| **Receiving** | Delivery note · Goods received note · Discrepancy report · Stock receipt |
| **Sales** | Sales invoice · Fiscal (EBM) invoice · Receipt · Credit note · Return note |
| **Insurance** | Prescription record · Claim · Claim submission · Claim response · Rejection notice · Supporting document bundle |
| **Stock** | Stock transfer note · Stock adjustment · Stock count sheet · Expiry report · Recall notice · Disposal certificate |
| **Compliance** | Premises licence record · Pharmacist registration record · Certificate · Inspection report · Verification record · Audit extract |

---

## Templates that need particular care

**Fiscal invoice.** RRA-mandated content is authoritative. Design around it; never omit or restyle a required element to suit the layout.

**Disposal certificate.** A regulated artifact with a witness. It needs a genuine signature block — witness name, role, authority reference, date — and reads as a certificate, not a delivery note. This is the one document where a more formal register is appropriate.

**Goods received note.** Must show ordered / received / accepted / rejected as four distinct columns, because the discrepancy is the point of the document.

**Claim.** The insurer's requirements govern content and often ordering. Layout is ours; required fields are not.

**Thermal receipt.** Ruthlessly compressed. Only what the customer and RRA need. No wordmark art, no decoration — but still typographically considered rather than a monospace dump.

---

## Implementation

- Templates: Django templates under `documents/templates/docs/`, one per type, all extending `base_document.html` which owns the five regions.
- Styling: a single print stylesheet built from the same design tokens as the application. **No document-local colour values.**
- Rendering: headless Chromium via Playwright. Deterministic, and it renders exactly what the browser preview shows.
- Storage: rendered PDFs in object storage, immutable once issued, addressed by document number and version.
- Reissue: never overwrite. A corrected document is a new version with a visible supersession reference.
- Testing: visual regression per template, plus assertions that mandated fields are present for regulated types.

---

## Acceptance checklist per template

- [ ] Renders identically in web preview and PDF
- [ ] Legible printed monochrome
- [ ] Footer repeats correctly across page breaks
- [ ] Line-item table breaks across pages with the header repeating
- [ ] Totals never orphan onto a page alone
- [ ] Verification QR resolves and the reference is also printed as text
- [ ] Mandated fields present for the regulated types
- [ ] No literal colour values — tokens only
- [ ] Number allocated from the sequence service, gap-free under concurrent load
- [ ] Renders correctly with the longest realistic product and organization names
