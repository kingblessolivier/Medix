# 26 — Typography

Two families. Nothing else ships.

---

## Families

| Role | Family | Fallback |
|---|---|---|
| **UI** | Inter | `"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif` |
| **Mono** | JetBrains Mono | `ui-monospace, "Cascadia Mono", Consolas, monospace` |

### Why Inter

Designed for screen UI at small sizes. Tall x-height keeps 12–13px legible at density. Open apertures — `a`, `e`, `s`, `c` do not close up at 11px, which matters when a pharmacist reads a batch number under fluorescent light at the end of a shift.

Ships **tabular figures** and **slashed zero** as OpenType features. Both are required here: every stock table, price column and batch number depends on them.

Rejected: Roboto (closed apertures, poor at 11px), Segoe UI (Windows-only), Geist and IBM Plex Sans (viable, no advantage), any serif or geometric sans.

### Why JetBrains Mono

Wide character differentiation — `0/O`, `1/l/I`, `5/S`, `8/B` are unmistakable. Batch numbers and document numbers are read aloud, typed from a screen, and matched against a printed box. A mono that confuses `0` and `O` produces a real dispensing error.

Used for: batch numbers, document numbers, GTIN, registration numbers, codes, API examples. **Never for body text.**

---

## Loading

Self-hosted, variable, subset to Latin, `woff2`.

```css
@font-face {
  font-family: "Inter";
  src: url("/fonts/inter-var.woff2") format("woff2-variations");
  font-weight: 400 700;
  font-display: swap;
  unicode-range: U+0000-00FF, U+0131, U+2000-206F, U+2074, U+20AC, U+2122, U+2212;
}
```

Rules:

- **Never a font CDN.** Third-party request, privacy exposure, and a failure mode that leaves the app in a fallback face.
- `font-display: swap` — text renders immediately in the fallback rather than blanking.
- Preload the UI variable font in `<head>`; do not preload mono.
- Subset to Latin only. English-only means no extended ranges.
- Two files total. Variable axes cover every weight.

Budget: UI font ≤ 45 KB, mono ≤ 35 KB.

---

## OpenType features

```css
:root {
  font-feature-settings: "cv05" 1, "cv11" 1, "ss03" 1;
  font-variant-numeric: proportional-nums;
}

.tabular, td, th, .metric, input[type="number"] {
  font-variant-numeric: tabular-nums slashed-zero;
}
```

| Feature | Effect | Why |
|---|---|---|
| `tabular-nums` | Fixed-width digits | Columns align; a changing value does not shift the row |
| `slashed-zero` | `0` with a slash | `0` vs `O` in batch numbers |
| `cv05` | Lowercase `l` with a tail | `l` vs `1` vs `I` |
| `cv11` | Single-storey `a`… | Not used — disabled deliberately, double-storey reads better at 12px |
| `ss03` | Round quotes | Typographic quotes |

**`tabular-nums` is mandatory** on every table cell, metric, money value and numeric input. Not a preference — proportional digits in a stock column make scanning impossible.

---

## Scale

Fixed. Adding a size is a design-system change, not a screen decision.

| Token | Size | Weight | Line height | Use |
|---|---|---|---|---|
| `--t-page` | 20px | 600 | 28px | Page title |
| `--t-section` | 14px | 600 | 20px | Section heading |
| `--t-body` | 13px | 400 | 20px | Body, table cell |
| `--t-th` | 12px | 600 | 16px | Table header |
| `--t-label` | 12px | 500 | 16px | Field label |
| `--t-help` | 11px | 400 | 16px | Helper, caption |
| `--t-group` | 10px | 600 | 14px | Group header, uppercase, `0.07em` |

Display sizes for metrics only:

| Token | Size | Weight | Use |
|---|---|---|---|
| `--t-metric` | 19px | 600 | KPI value |
| `--t-metric-lg` | 28px | 600 | POS total, hero number |

**No size above 28px anywhere.** A 40px heading belongs on a marketing page.

### Line height

Body 1.5. Headings 1.3. Table cells 1.4 — tighter, because row height already provides the breathing room.

Never `line-height: normal`. It varies by family and breaks vertical rhythm the moment the fallback loads.

---

## Weight

Only three values.

| Weight | Use |
|---|---|
| 400 | Body, table cells, everything by default |
| 500 | Labels, buttons, status text |
| 600 | Headings, table headers, metrics, emphasis |

**Never 700.** Against these cool neutrals it reads as shouting. **Never 300** — it fails contrast at 13px.

Emphasis comes from weight *or* colour, never both, never plus size.

---

## Letter spacing

| Context | Tracking |
|---|---|
| Page title 20px | `-0.02em` |
| Section 14px | `-0.01em` |
| Body 13px | `0` |
| Helper 11px | `0.01em` |
| Group header 10px uppercase | `0.07em` |

Large text tightens, small text opens, uppercase always tracks out. This is the single detail that most separates considered typography from default typography.

---

## Colour

| Token | Use |
|---|---|
| `--text` | Primary content, values, headings |
| `--text-2` | Labels, secondary information, table headers |
| `--text-3` | Placeholders, captions, disabled, group headers |

**Never opacity on text.** Opacity multiplies against whatever is behind it and drifts across the five surface levels. Use the token.

**Never a series colour on text.** In a chart the mark carries identity; the label stays in text tokens.

---

## Measure

| Content | Max width |
|---|---|
| Body prose | 65ch |
| Helper text | 55ch |
| Table cell | Column width, truncate with tooltip |
| Empty state body | 40ch |

The interface has almost no prose. Where it appears — a document, a compliance note — it is measured.

---

## Truncation

Single line, ellipsis, full value in a `title` tooltip. Never wrap inside a table cell — a wrapping cell breaks the row rhythm and the density argument with it.

Never truncate: money, quantity, dates, status, batch numbers. If they do not fit, the column is too narrow.

---

## Numbers

| Type | Format | Alignment |
|---|---|---|
| Money | `28,000` — no decimals, RWF has no practical minor unit in retail | Right |
| Money, precise | `28,000.00` in financial reports only | Right |
| Quantity | `240` with the unit in the header, not the cell | Right |
| Percentage | `12.4%` — one decimal | Right |
| Date | `11 Aug 2026` | Left |
| Date, compact | `Aug 2026` in expiry columns | Left |
| Time | `14:22` — 24-hour | Left |
| Document number | `SAL-2026-00982` — mono | Left |
| Large value | `18.4M` in metrics, full value in tooltip | Right |

Currency appears in the column header (`Price (RWF)`), never repeated on every row.

---

## Print

Documents use the same families at print scale. Full specification in [18-document-design.md](18-document-design.md).

| Role | Print size |
|---|---|
| Document type | 20pt / 600 |
| Body, line item | 9.5pt / 400 |
| Table header | 8.5pt / 600 |
| Section label | 8pt / 600, uppercase, `0.08em` |
| Footer | 7.5pt / 400 |

Fonts embedded in the PDF. A document that renders in a fallback face on the recipient's machine is a broken document.

---

## Checklist

- [ ] Only Inter and JetBrains Mono
- [ ] Self-hosted, no CDN
- [ ] Size from the fixed scale
- [ ] Weight is 400, 500 or 600 — never 700 or 300
- [ ] `tabular-nums` on every table cell, metric and numeric input
- [ ] `slashed-zero` wherever a code or batch number appears
- [ ] Mono for codes only, never body
- [ ] Text colour from a token, never opacity
- [ ] Letter spacing applied per the table
- [ ] Nothing below 11px, nothing above 28px
