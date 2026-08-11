# 22 — Component specification

Every component, specified. Dimensions, states, behaviour, and the reasoning behind each decision.

Grounded in published enterprise design systems — [Fluent 2](https://fluent2.microsoft.design/), [IBM Carbon](https://carbondesignsystem.com/components/data-table/usage/), [Atlassian](https://atlassian.design/components), [SAP Fiori](https://www.sap.com/design-system/fiori-design-web/) — adapted to Medix's density and domain. Where these systems disagree, the Medix choice and its reason are stated.

**Tokens only.** No component contains a literal colour, size, spacing or radius value.

---

## 1 — Background and surface

The single most important decision in the visual system, and the one most often skipped.

### The five levels

| Level | Token | Light | Dark | Where |
|---|---|---|---|---|
| 0 Workspace | `--app` | `#F4F6F8` | `#14181B` | The page canvas |
| 1 Navigation | `--nav` | `#F0F3F6` | `#101418` | Sidebar |
| 1 Shell | `--topbar` | `#F7F8FA` | `#171B1F` | Top bar |
| 2 Secondary | `--content` | `#F8FAFC` | `#1A1F24` | Table headers, quiet panels |
| 3 Surface | `--surface` | `#FFFFFF` | `#1E242A` | Content that matters |
| 4 Floating | `--surface` + shadow | | | Modal, dropdown, palette |

**Why not pure white everywhere.** Fluent's guidance is that lighter neutrals on surfaces create hierarchy so the interface does not need borders and shadows everywhere; Fiori describes its Quartz Light background as subtle, calm and reduced so content remains the focus. Both arrive at the same conclusion: **depth comes from small differences between surfaces, not from floating everything in a card.**

The differences are deliberately near-imperceptible. The user never thinks *there are four backgrounds*. They feel the interface has structure.

### Rules

- **The sidebar is never pure white.** It is part of the application shell, not another floating card. This was an explicit reversal of an earlier direction.
- **The top bar disappears into the environment** — same neutral family as the workspace, separated by a 1px divider. Not a big white header.
- **Never more than five levels.** A sixth is always a sign that something should be a border or a gap instead.
- **No gradient as a page background.** Ever. Institutional, not trendy SaaS.
- **No Mica, Acrylic, or glassmorphism.** Fluent's materials are tied to Windows surfaces; in a browser we use solid surfaces plus subtle elevation. Transparency only on the modal scrim.

### Elevation

```css
--elev-0: none;                             /* normal content */
--elev-1: 0 1px 2px rgba(15,23,42,.04);     /* raised surface */
--elev-2: 0 4px 12px rgba(15,23,42,.08);    /* dropdown, popover */
--elev-3: 0 12px 32px rgba(15,23,42,.14);   /* modal, command palette */
```

In dark mode shadows are less effective, so floating surfaces also lift one background step and gain a `--border` outline.

---

## 2 — Borders

Two weights, deliberately. Collapsing them is why tables turn into grids of boxes.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--border` | `#DCE2E8` | `#2B333A` | Structural — panel edges, inputs, table outline, shell dividers |
| `--border-hair` | `#E4E8EC` | `#242B31` | Hairline — table rows, list separators |

At 1440p the hairline should almost disappear. Never a 2px border except the active-navigation left marker and a focus ring.

**Prefer a border to a shadow** for anything that is not floating. Borders are crisper at density and do not accumulate visual weight the way stacked shadows do.

---

## 3 — Data table

The most important component in Medix. In this product the table *is* the interface, not filler between cards.

### Anatomy

```
┌─────────────────────────────────────────────────────────────┐
│ TOOLBAR   search · filters · columns · density · export      │
├─────────────────────────────────────────────────────────────┤
│ ☐ │ Product      │ Batch    │ Expiry   │  Qty │ Status  │ ⋯ │  ← sticky
├───┼──────────────┼──────────┼──────────┼──────┼─────────┼───┤
│ ☐ │ Amoxicillin  │ AMX-0021 │ Apr 2027 │  240 │ ● Avail │ ⋯ │
├───┼──────────────┼──────────┼──────────┼──────┼─────────┼───┤
│ ☐ │ Cetirizine   │ CTZ-4421 │ Sep 2026 │   28 │ ● Crit  │ ⋯ │
├─────────────────────────────────────────────────────────────┤
│ 3 of 1,284                                    ‹ 1 2 3 … ›   │
└─────────────────────────────────────────────────────────────┘
```

### Metrics

| Element | Value |
|---|---|
| Header height | 36px |
| Row — compact | 40px *(default)* |
| Row — comfortable | 48px |
| Row — spacious | 56px |
| Cell padding | 12px horizontal, 0 vertical (line-height centres) |
| Header type | 12px / 600, `--text-2` |
| Cell type | 13px / 400, `--text` |
| Header background | `--content` |
| Row separator | 1px `--border-hair` |
| Outer border | 1px `--border`, radius `--r-lg` |

**Compact is the default**, unlike most systems. A pharmacist scanning 300 batches sees roughly twice as many rows at 40px as at comfortable — the difference between 9 screens of scrolling and 20. Carbon and Fiori both offer density as a user preference; Medix goes further and defaults to the dense end because the primary user is a professional at a counter, not an occasional visitor.

**Never 70–80px rows.** That is a consumer list, not an enterprise table.

### Sticky header

On by default. The header stays fixed for any row count, with `--content` background and a `--border` bottom edge so there is no confusion between labels and values. It must not jitter, overlap, or detach mid-scroll — this is verified on real content, not on a ten-row fixture.

### Column behaviour

| Type | Alignment | Notes |
|---|---|---|
| Text | Left | Truncate with ellipsis, full value in a tooltip |
| Number, money | **Right** | `tabular-nums` always |
| Date | Left | Consistent format, never locale-dependent inside a table |
| Code, batch, document number | Left, **mono** | |
| Status | Left | Dot + label, never colour alone |
| Actions | Right | `⋯` menu, appears on row hover, always present for keyboard |

**Sorting.** The affordance lives in the header and stays quiet until interacted with — a chevron appears on hover, becomes solid and directional when active. Only one sort at a time unless multi-sort is explicitly needed.

**Selection.** Checkbox column first, 40px wide. Selecting rows swaps the toolbar for a bulk-action bar showing the count and the available actions, with a clear escape.

**Row click opens a drawer.** It never navigates away — see §8.

### States

- **Hover** — row background `--hover`. No border change, no lift.
- **Selected** — `--selected` background, and the checkbox is checked.
- **Loading** — skeleton rows matching the real row height, so the layout does not jump.
- **Empty** — see §12.
- **Error** — inline message with a retry, table chrome preserved.

### What the table must never do

- Zebra striping. Hairlines are enough and striping fights the status dots.
- Vertical rules between columns. Alignment separates columns.
- A coloured row to indicate status. Use the status cell.
- Horizontal scroll on desktop. If columns do not fit, hide some by default and expose them through column visibility.

---

## 4 — Toolbar

Sits directly above the table, one row, on `--surface`.

```
[ 🔍 Search products…        ]  [ Filters 2 ]  [ Columns ]  [ ⇅ ]  [ Export ]
```

Left: search. Right: filters, column visibility, density, export. Filters showing an active count as a badge. Never more than six controls — beyond that the overflow goes into a menu.

Filters open in a popover, apply on confirm, and appear as removable chips beneath the toolbar when active. A user must always be able to see what is filtering their view without opening anything.

---

## 5 — Search

Two distinct components. Confusing them is a common error.

### Global search (top bar)

**Quiet by default.** This is a deliberate departure from the common pattern of a prominent white search box.

| State | Spec |
|---|---|
| Rest | `--hover` background, no border, `--text-2` placeholder, `⌘K` hint right-aligned |
| Hover | `--border` outline appears |
| Focus | `--surface` background, 1px `--brand` border, `--elev-1` |

Height 32px, radius `--r-md`, max-width 420px. A search field that shouts is a search field competing with the content.

Placeholder: `Search products, orders, batches…` — names what is searchable, never just `Search`.

### Scoped search (toolbar)

Standard input, height 32px, `--surface` background, `--border`, with a leading search icon at 16px. Debounced 250ms. Clear button appears once there is a value.

### Command palette

`⌘K` / `Ctrl+K`. A modal at `--elev-3`, 640px wide, positioned at 20vh from the top — not centred, because results grow downward.

Searches **everything** and runs **actions**:

```
┌────────────────────────────────────────────────┐
│ 🔍 Search or run a command…                    │
├────────────────────────────────────────────────┤
│ ACTIONS                                        │
│   ⊕ Create purchase order                      │
│   ⊕ Open point of sale                         │
│ PRODUCTS                                       │
│   ▣ Amoxicillin 500mg          240 in stock    │
│ DOCUMENTS                                      │
│   ▤ GRN-2026-00412             MedSupply       │
└────────────────────────────────────────────────┘
```

Results grouped by type, keyboard-navigable, `↵` to select, `Esc` to dismiss. Recent items when the query is empty.

---

## 6 — Text input

| Property | Value |
|---|---|
| Height | 36px *(40px in POS)* |
| Padding | 12px horizontal |
| Radius | `--r-md` (8px) |
| Border | 1px `--border` |
| Background | `--surface` |
| Type | 13px / 400 |
| Label | 12px / 500, `--text-2`, 6px above |
| Helper | 11px / 400, `--text-3`, 4px below |

### States

| State | Treatment |
|---|---|
| Rest | `--border` |
| Hover | `--border` darkened one step |
| Focus | 1px `--brand` border + 2px `--brand` at 20% outer ring |
| Filled | Same as rest — a value never changes the chrome |
| Disabled | `--content` background, `--text-3`, `cursor: not-allowed` |
| Read-only | No border, no background, `--text` — reads as data, not a field |
| Error | 1px `--bad` border, message below in `--bad` at 11px |
| Success | Not styled. Absence of error is success; a green border is noise |

**Focus is always visible.** Never `outline: none` without a replacement. This is an accessibility requirement, not a preference.

### Placeholders — the rules people get wrong

- **A placeholder is not a label.** It disappears on typing and cannot be relied on. Every input has a persistent visible label.
- **A placeholder is an example, not an instruction.** `AMX-0021` not `Enter batch number`.
- **Never `e.g.`** — the example is self-evidently an example.
- **Never repeat the label.** Label `Batch number`, placeholder `AMX-0021`.
- Placeholder colour is `--text-3`, never `--text-2`, so it cannot be mistaken for a value.

| Field | Label | Placeholder |
|---|---|---|
| Batch | `Batch number` | `AMX-0021` |
| Expiry | `Expiry date` | `MM / YYYY` |
| Quantity | `Quantity` | `100` |
| Search | — | `Search products, orders, batches…` |
| Email | `Email` | `name@pharmacy.rw` |

### Specialised inputs

**Number** — right-aligned, `tabular-nums`, no spinner arrows (they are imprecise and steal scroll). Step buttons only in POS, at 40px.

**Money** — currency prefix inside the field as static `--text-2` text, value right-aligned.

**Quantity** — a compound control: number input plus a unit-of-measure select, because a bare quantity is meaningless in this system.

```
Quantity  [    6 ] [ Units ▾ ]
```

**Date** — text entry accepted alongside the picker. Typing `11/08/2026` must work; forcing a calendar for a known date is hostile at a counter.

---

## 7 — Select, dropdown, combobox

Three different components. Fluent draws the distinction clearly: **use a dropdown when the user may only choose from the list; use a combobox when they may type to filter or enter a custom value.**

| Component | When |
|---|---|
| **Select** | ≤7 options, all visible, no search |
| **Dropdown** | 8–50 options, scrollable, no free text |
| **Combobox** | >50 options, or async, type to filter |

### Faceplate

Same metrics as a text input — 36px, `--r-md`, `--border` — with a trailing 16px chevron in `--text-2`. The faceplate always shows the current selection, never a placeholder once chosen.

### Popup

| Property | Value |
|---|---|
| Background | `--surface` |
| Elevation | `--elev-2` |
| Border | 1px `--border` |
| Radius | `--r-md` |
| Max height | 320px, then scroll |
| Option height | 32px |
| Option padding | 12px |
| Offset | 4px below the faceplate |

Width matches the faceplate unless options are longer, in which case it may grow — never shrink.

### Option states

- **Hover** — `--hover`
- **Focused** (keyboard) — `--hover` plus a 2px `--brand` left marker
- **Selected** — `--selected`, `--brand` text, trailing check icon
- **Disabled** — `--text-3`, with a reason in a tooltip

### Behaviour

- Opens on click, `↓`, `Enter`, or `Space`.
- `↑` `↓` moves, `Enter` selects, `Esc` closes and restores.
- Type-ahead jumps to the first match, even in a plain dropdown.
- Multi-select shows chips in the faceplate, overflowing to `+3 more`.
- Combobox filters as you type and **highlights the matched substring**.
- Async combobox shows a loading row, never an empty list that looks like no results.

**Grouping** with a 11px / 600 uppercase `--text-3` header, non-selectable. Used for supplier by region, product by category.

---

## 8 — Drawer

The signature interaction of the system.

```
SEARCH → TABLE → DRAWER → FULL TRANSACTION
```

Right-anchored, 480px wide (640px for dense content), full height, `--surface`, `--elev-3`, with a scrim at `rgba(15,23,42,.32)`.

Header carries the title, a subtitle, and a close button. Footer, when present, carries actions right-aligned. Body scrolls; header and footer do not.

**Drawer for:** preview, quick edit, quick information, activity history.
**Full page for:** purchase order creation, import request, receiving, POS, prescription processing, insurance claim, product creation.

Closes on `Esc`, scrim click, or the close button. Focus is trapped while open and returns to the trigger on close.

**A drawer never opens another drawer.** If that is needed, the flow belongs on a page.

---

## 9 — Modal

Centred, `--surface`, `--elev-3`, radius `--r-xl`, widths 400 / 560 / 720px.

Reserved for **decisions that block**: confirm a destructive action, resolve a conflict, complete a short focused task. Never for browsing, never for a long form.

Destructive confirmations name the object and the consequence — 15 words maximum:

> **Void SAL-00982?**
> Reverses 3 stock movements. Issues a credit note.
> `[ Cancel ]` `[ Void ]`

The destructive action uses `--bad` and is **never** the default focus.

---

## 10 — Buttons

| Variant | Background | Border | Text | Use |
|---|---|---|---|---|
| Primary | `--brand` | none | white | The one main action per view |
| Secondary | `--surface` | `--border` | `--text` | Everything else |
| Tertiary | transparent | none | `--text-2` | Cancel, dismiss |
| Danger | `--bad` | none | white | Destructive, confirmed |

Height 36px (40px in POS), padding 16px horizontal, radius `--r-md`, type 13px / 500.

**One primary per view.** Two primaries means neither is primary.

**Icons are not automatic.** Text-only buttons are usually cleaner. Use an icon for navigation, compact and toolbar buttons, and where recognition genuinely helps — not on `Save`, `Cancel`, `Export`.

Loading state replaces the label with a spinner and keeps the width, so the layout does not jump. Avoid disabled buttons where possible — a disabled control gives no explanation and shows no tooltip on touch; prefer keeping it enabled and explaining on use.

---

## 11 — Status, badges, chips

**Status** is a dot plus a label. Never colour alone, never a coloured row.

```
● Available    ● Expiring    ● Critical    ● Draft
```

Dot 8px, gap 6px, label 12px / 500 in the semantic colour.

**Badge** — a filled pill for counts and states: `3px 8px`, radius `999px`, 11px / 600, semantic tint background with same-family text.

**Chip** — a removable filter token: `--hover` background, `--border`, radius `--r-sm`, 12px, with a trailing `×`.

**Counter** — a numeric badge on an icon: `--bad` background, white, 16px minimum, `99+` beyond.

---

## 12 — Empty, loading, error

### Empty — state and act

```
              ▤

       No purchase orders

    [ Create purchase order ]
```

Icon 32px `--text-3`, heading 14px / 600, one action. Body line only when the action is non-obvious. Never `No data`. Never an illustration that takes half the screen. See [23-ui-copy.md](23-ui-copy.md).

| Cause | Message |
|---|---|
| Nothing exists yet | `No purchase orders` + create |
| Filters exclude everything | `No products match filters` + clear |
| Search found nothing | `No results for "amoxicilin"` |
| No permission | `No access` + who grants it |

### Loading — skeletons, not spinners

Skeletons match the real content's dimensions so nothing jumps. `--content` background with a subtle shimmer, disabled under `prefers-reduced-motion`.

Never skeletonize every tiny element. A spinner is acceptable only inside a button or for an action under 500ms.

### Error — what failed, what to do

```
⚠  Submission failed. Draft saved.
   [ Retry ]
```

Twelve words maximum. Never a raw backend error. Never `Error 500`. Never an apology.

---

## 13 — Navigation

### Sidebar

248px expanded, 68px collapsed. `--nav` background, `--border` right edge.

Items 36px high, 12px padding, 13px / 400, icon 16px at `--text-2`. Grouped under 10px / 600 uppercase `--text-3` headers with 12px above.

**Active item** — `--selected` background, `--brand` icon and text, **2px `--brand` left border**, radius 0 on that edge. Never a solid brand block filling the row.

Collapsed shows icons only with tooltips on hover; the active marker remains.

### Top bar

56px, `--topbar`, 1px `--border` bottom. Wordmark left, global search adjacent, notifications and user right. Never a big white header.

### Page header

```
Inventory                                    [ + Adjust stock ]
Monitor stock, batches, expiry and movements
```

Title 20px / 600, description 13px `--text-2`, actions right. A breadcrumb above only when nested more than one level.

### Tabs

Underline style. Rest `--text-2`, active `--text` with a 2px `--brand` underline. 13px / 500, 12px horizontal padding, 36px tall.

**Tabs are for one object's facets** — Overview, Items, Documents, Approval, History on a single purchase order. Never for navigating between unrelated pages.

---

## 14 — Forms

**Grouped into sections, never one long wall of fields.**

```
Basic information
  Product name · Generic name · Brand · Category

Pharmaceutical information
  Strength · Dosage form · Route · Prescription status

Regulatory
  Registration number · Registration date · Expiry
```

Section header 14px / 600, 24px above, 12px below. Fields in one or two columns; related short fields may pair. Label above the field, always.

**Long workflows use a stepper**, not fifty simultaneous fields:

```
① Product → ② Supplier → ③ Quantity & pricing → ④ Documents → ⑤ Review
```

**Autosave** on multi-step work, with `Draft saved 12 seconds ago` in `--text-3`.

Validation on blur, not on every keystroke. Errors are domain-aware: `Product expiry date cannot be in the past.` beats `Invalid date`.

---

## 15 — Icons

**Lucide, and only Lucide.**

| Context | Size | Stroke |
|---|---|---|
| Sidebar | 17px | 1.8 |
| Toolbar, buttons | 16px | 1.8 |
| Large actions | 18–20px | 1.75 |
| Empty state | 32px | 1.5 |

Colour inherits from the parent — an icon is never independently coloured except in a status context.

**Never:** mix icon families · use emoji as icons · colour icon backgrounds · exceed 20px in dense UI · put an icon in every button.

Canonical mapping — one icon per concept, everywhere:

| Concept | Icon | Concept | Icon |
|---|---|---|---|
| Overview | `LayoutDashboard` | Point of sale | `Receipt` |
| Marketplace | `Store` | Prescriptions | `ClipboardList` |
| Orders | `ShoppingCart` | Insurance | `ShieldCheck` |
| Inventory | `Package` | Finance | `Wallet` |
| Batches | `Boxes` | Reports | `ChartNoAxesCombined` |
| Transfers | `ArrowLeftRight` | Compliance | `BadgeCheck` |
| Suppliers | `Building2` | Cold chain | `Thermometer` |
| Imports | `Ship` | Expiry | `CalendarClock` |
| Documents | `FileText` | Scan | `ScanBarcode` |
| Settings | `Settings` | Search | `Search` |

---

## 16 — Typography

**Inter.** Fallback `"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif`. Mono: `ui-monospace, "Cascadia Mono", Consolas, monospace` for batch numbers, document numbers, codes.

| Role | Size / weight | Colour |
|---|---|---|
| Page title | 20 / 600 | `--text` |
| Section title | 14 / 600 | `--text` |
| Body, table cell | 13 / 400 | `--text` |
| Table header | 12 / 600 | `--text-2` |
| Label | 12 / 500 | `--text-2` |
| Helper, caption | 11 / 400 | `--text-3` |
| Group header | 10 / 600, `0.07em` tracking, uppercase | `--text-3` |

Two weights carry the system: 400 and 600. **500 for labels and buttons only. Never 700** — it reads heavy against these neutrals.

Rules: `tabular-nums` on every column of digits · sentence case everywhere · uppercase only for 10px group headers · `text-wrap: balance` on headings · never a 40px heading · never bold an entire sentence.

---

## 17 — Motion

Restrained. This is an operational system, not a showcase.

| Interaction | Duration | Easing |
|---|---|---|
| Hover, focus | 100ms | `ease-out` |
| Dropdown, popover | 120ms | `ease-out` |
| Drawer | 200ms | `cubic-bezier(.2,0,0,1)` |
| Modal | 160ms | `ease-out` |
| Skeleton shimmer | 1400ms | `linear` |

Never animate layout on data change — a table that reflows while a pharmacist is reading it is worse than one that snaps. All motion respects `prefers-reduced-motion`.

---

## 18 — Accessibility

Non-negotiable, applied per component.

- Contrast meets WCAG AA in **both** themes; large text 3:1, body 4.5:1.
- Every interactive element has a visible focus state. `outline: none` without a replacement is a defect.
- Status is never colour alone — always a dot with a label, or an icon.
- Full keyboard operation. **The entire POS flow completes without a mouse.**
- Focus trapped in modals and drawers, returned to the trigger on close.
- Tables use real `<table>` semantics with `<th scope>`; sort state announced via `aria-sort`.
- Icon-only buttons carry `aria-label`.
- Live regions announce async results — sale completed, payment resolved, sync finished.
- Minimum touch target 44px in POS.
- Never below 11px type anywhere.

---

## 19 — Review checklist

- [ ] No literal colour, size, spacing or radius — tokens only
- [ ] Correct in both themes, dark checked independently
- [ ] Every input has a persistent label; placeholder is an example, not an instruction
- [ ] Focus visible on every interactive element
- [ ] Status carries a label, not colour alone
- [ ] Table uses `DataTable`, compact default, sticky header, tabular numerics
- [ ] One primary button per view
- [ ] Empty, loading and error states all present and specific
- [ ] Lucide icons only, correct size and stroke
- [ ] Keyboard operable end to end
- [ ] Motion respects `prefers-reduced-motion`
- [ ] UI copy within the length limits in docs/23-ui-copy.md

---

**Sources:** [Fluent 2 Design System](https://fluent2.microsoft.design/) · [Fluent 2 Dropdown](https://fluent2.microsoft.design/components/web/react/core/dropdown/usage) · [Fluent 2 Combobox](https://fluent2.microsoft.design/components/web/react/core/combobox/usage) · [IBM Carbon data table](https://carbondesignsystem.com/components/data-table/usage/) · [Carbon data table accessibility](https://carbondesignsystem.com/components/data-table/accessibility/) · [Atlassian components](https://atlassian.design/components) · [SAP Fiori](https://www.sap.com/design-system/fiori-design-web/) · [Data table UX patterns](https://www.pencilandpaper.io/articles/ux-pattern-analysis-enterprise-data-tables)
