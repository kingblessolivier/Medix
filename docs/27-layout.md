# 27 — Layout and pagination

Grid, spacing, responsive behaviour, and how large result sets are navigated.

---

## 1 — Grid

12 columns, 20px gutter, fluid within the content area.

```
sidebar 248px │ content: max 1440px, padding 24px
```

| Region | Width |
|---|---|
| Sidebar expanded | 248px fixed |
| Sidebar collapsed | 68px fixed |
| Content max | 1440px |
| Content padding | 24px |
| Column gutter | 20px |
| Section gap | 24px |

Content is centred within the viewport once past 1440px. Unbounded stretch produces 40-character-wide table columns on a 27-inch monitor with an ocean of grey either side.

### Column allocation

| Layout | Split |
|---|---|
| Full-width table | 12 |
| Detail + sidebar | 8 / 4 |
| Split product view | 3 / 9 |
| Form, one column | 6 |
| Form, two column | 6 / 6 within an 8-wide section |
| Metric row | 12, flex, not grid |

Metrics are flex, not grid — four numbers should sit at their natural width with a fixed gap, not stretch to fill twelve columns.

---

## 2 — Spacing

```
4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64
```

Never 13, 17, 19, 27, 31. Consistency is what makes an interface feel considered rather than assembled.

| Relationship | Gap |
|---|---|
| Label → field | 6px |
| Field → helper | 4px |
| Between fields | 16px |
| Between field groups | 24px |
| Between sections | 32px |
| Section heading → content | 12px |
| Icon → label | 8px |
| Between buttons | 8px |
| Page header → content | 20px |

**Use `gap`, not margins.** Sibling groups lay out with flex or grid and `gap`. Per-element margins collapse unpredictably and double at boundaries.

### Whitespace over borders

```
❌  border → border → border → border
✅  information → space → information → space
```

A section separates from the next by 32px of space, or a single hairline — not by both, and not by a card around each.

---

## 3 — Page structure

Every screen, in order:

```
Page header      title 20/600 · description 13 · actions right
  ↓ 20px
Primary workspace    the thing being worked on
  ↓ 32px
Secondary information
```

The page header never scrolls away on a table screen — the table header sticks beneath it.

### Density of the page itself

One primary action per page header. One primary purpose per screen. If a screen has two equally weighted jobs, it is two screens.

---

## 4 — Responsive

Desktop-first. This is an operational system used at a counter and a desk.

| Breakpoint | Width | Behaviour |
|---|---|---|
| `sm` | < 640 | Bottom navigation, single column, cards replace tables |
| `md` | 640–1023 | Sidebar collapsed to 68px, tables scroll horizontally |
| `lg` | 1024–1439 | Sidebar expanded, content fluid |
| `xl` | ≥ 1440 | Content capped at 1440, centred |

### Tables on small screens

Two strategies, chosen per table:

**Horizontal scroll** — default. Container `overflow-x: auto`, first column sticky (usually the product name). The page body never scrolls sideways.

**Card transformation** — for tables under 6 columns where scanning matters more than comparison. Each row becomes a stacked block with labels.

Never force twelve columns into 375px.

### POS is the exception

Touch targets 44px minimum, buttons 40px, inputs 40px. Tablet-first ergonomics inside the same visual language, because it runs on a counter tablet.

---

## 5 — Pagination

Three mechanisms. Choosing the wrong one is a performance bug, not a preference.

| Mechanism | Use for | Why |
|---|---|---|
| **Numbered pages** | Products, orders, batches, documents | User needs position and total |
| **Cursor / infinite** | Ledger, audit, sales, notifications | Constant-cost at depth; rows insert constantly |
| **Load more** | Activity feeds, search suggestions | Short lists, no position needed |

### Numbered pages — the default

```
Showing 1–50 of 1,284          ‹  1  2  3  …  26  ›     50 ▾
```

| Element | Spec |
|---|---|
| Position | Bottom of the table, inside the surface, 12px padding |
| Range text | 13px `--text-2`, left |
| Controls | Right |
| Page button | 32×32px, radius `--r-sm` |
| Current page | `--selected` background, `--brand` text, 600 |
| Page size | Select: 25 · 50 · 100 · 200. Default **50** |
| Window | First, last, current ±2, ellipsis between |
| Disabled arrows | `--text-3`, not hidden — layout must not shift |

Page size persists per user per table.

**Keyboard:** `←` `→` for previous and next when the table has focus and no cell is being edited.

### Cursor pagination

For the ledger, audit and sales, `OFFSET 50000` reads and discards 50,000 rows, and rows insert constantly so page 3 shifts under the reader between loads.

```
Showing 1–50                          [ Load older ]
```

No total, no page numbers, no jump-to-page — they cannot be provided honestly. Do not fake them.

Infinite scroll only where the user is browsing, never where they are working. A pharmacist reconciling a ledger needs a stable position; an infinite list takes that away.

### Rules

- **Never load an unbounded list.** Every list endpoint paginates. No exceptions.
- **Never paginate client-side over a full fetch.** Fetching 5,000 rows to show 50 is the most common performance mistake in enterprise UI.
- **Server-side sort and filter always.** Sorting a page of 50 sorts 50 rows, which is wrong.
- Page state lives in the URL — `?page=3&sort=expiry&status=AVAILABLE` — so a view is shareable and survives a refresh.
- Selection is cleared on page change, with the count shown before it clears if anything was selected.
- Loading a new page shows skeleton rows at the current row height. The table does not collapse and re-expand.

---

## 6 — Virtualization

Above 100 rendered rows, virtualize. Below, do not — the complexity is not repaid.

| Rows | Approach |
|---|---|
| < 100 | Render all |
| 100–500 | Virtualized, paginated |
| > 500 | Virtualized, cursor |

Virtualized rows must be fixed height, which is why density is a fixed set of three values rather than content-driven.

---

## 7 — Scroll

| Region | Behaviour |
|---|---|
| Page | Single vertical scroll |
| Table header | Sticky beneath the page header |
| Drawer body | Scrolls; header and footer fixed |
| Modal body | Scrolls above 60vh |
| Sidebar | Scrolls independently when items overflow |
| Wide content | `overflow-x: auto` on its own container |

**Never nested vertical scroll.** Two scrollbars on one screen means the user cannot predict what moves.

The page body never scrolls horizontally. Wide content scrolls inside its own container.

---

## 8 — Z-index

```css
--z-base:     0;
--z-sticky:  10;   /* table header, page header */
--z-nav:     20;   /* sidebar, top bar */
--z-dropdown:30;   /* select, popover, tooltip */
--z-drawer:  40;
--z-modal:   50;
--z-toast:   60;
--z-palette: 70;   /* command palette above everything */
```

Never an arbitrary value. Never `9999`.

---

## 9 — Checklist

- [ ] Content capped at 1440px, centred beyond
- [ ] Spacing from the scale — no 13, 17, 19, 27
- [ ] `gap`, not per-element margins
- [ ] One primary action per page header
- [ ] Every list paginated server-side
- [ ] Cursor pagination on ledger, audit, sales
- [ ] Page state in the URL
- [ ] No nested vertical scroll
- [ ] Page body never scrolls horizontally
- [ ] Tables above 100 rows virtualized
- [ ] Z-index from the token set
- [ ] POS targets 44px minimum
