# 04 — Design system

## Philosophy

A pharmacist is in this software **ten hours a day**. An executive is in it for thirty seconds.

That single fact rules out most modern SaaS aesthetics. Large colourful cards, gradients, heavy shadows, oversized radii, an icon in every button — these are designed for someone who visits an app for four minutes. Over ten hours they are exhausting.

> **Remove visual noise, not information.**

Density is required. Clutter is not. The reference points are **Microsoft Fluent 2** and **SAP Fiori** for structure and surface, and **Linear / Stripe** for typographic craft. Keep enterprise information density; do not inherit enterprise visual age.

**Discipline: 90% neutral · 8% structural and accent · 2% strong semantic colour.** Not a mathematical rule — a visual one.

---

## Tokens

Defined once in `src/design/tokens.css` and bridged into Tailwind. **Never write a literal colour in a component.**

### Surfaces — five levels, never eight

| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--app` | `#F4F6F8` | `#14181B` | Workspace canvas |
| `--nav` | `#F0F3F6` | `#101418` | Sidebar |
| `--topbar` | `#F7F8FA` | `#171B1F` | Application shell |
| `--content` | `#F8FAFC` | `#1A1F24` | Secondary areas, table headers |
| `--surface` | `#FFFFFF` | `#1E242A` | Important content |
| `--hover` | `#EEF2F6` | `#252C33` | Interaction, quiet search field |
| `--selected` | `#E8F1FF` | `#12314C` | Active navigation |

Depth comes from barely perceptible differences between neutrals, not from putting every section in a floating card. The user never thinks *there are four backgrounds*; they feel the interface has structure.

Floating objects — modal, dropdown, command palette — sit on `--surface` plus a shadow. That is the fifth level. Do not invent a sixth.

### Borders — two, deliberately

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--border` | `#DCE2E8` | `#2B333A` | Structural: panels, inputs, table outline, shell edges |
| `--border-hair` | `#E4E8EC` | `#242B31` | Hairline: table rows, list separators |

Collapsing these into one is why tables turn into grids of boxes. At 1440p the hairline should almost disappear.

### Text

| Token | Light | Dark |
|---|---|---|
| `--text` | `#17212B` | `#E6EBEF` |
| `--text-2` | `#5F6B76` | `#96A2AC` |
| `--text-3` | `#8A959E` | `#6E7A84` |

### Brand and semantic

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `--brand` | `#0078D4` | `#4CA6E8` | Actions, active state, links |
| `--ok` | `#059669` | `#34C88A` | Available, approved, received, completed |
| `--warn` | `#D97706` | `#DFA23A` | Expiring, pending, low stock, awaiting approval |
| `--bad` | `#DC2626` | `#EA6E66` | Expired, rejected, critical, recalled |

Blue is an **accent**, never the environment. There is no giant blue background anywhere in Medix. Semantic colour means *status* and never decoration — never colour a whole table row, use a `●` dot or a small badge.

### Elevation

```css
--elev-0: none;                              /* normal content */
--elev-1: 0 1px 2px rgba(15,23,42,.04);      /* raised surface */
--elev-2: 0 4px 12px rgba(15,23,42,.08);     /* dropdown, popover */
--elev-3: 0 12px 32px rgba(15,23,42,.14);    /* modal, command palette */
```

No Mica or Acrylic. This is a browser application: solid surfaces plus subtle elevation. Transparency used sparingly or not at all.

### Radius

```css
--r-sm: 6px;   /* badges, small controls */
--r-md: 8px;   /* inputs, buttons */
--r-lg: 10px;  /* cards */
--r-xl: 12px;  /* major panels */
```

Nothing at 16 or above. Large radii read as consumer SaaS; Medix should feel institutional.

### Spacing

```
4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64
```

Never 13, 17, 19, 27, 31. Consistency is what makes an interface feel expensive.

---

## Typography

**Inter.** Fallback `"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif`.

The scale is set at the dense end — 20 and 13, not 24 and 14 — because at the larger scale the same table loses roughly four visible rows at laptop height. In a system where the table *is* the interface, **rows on screen beats generous headings**.

| Role | Size / weight |
|---|---|
| Page title | 20 / 600 |
| Section title | 14 / 600 |
| Body | 13 / 400 |
| Table cell | 13 / 400 |
| Table header | 12 / 600 |
| Label | 12 / 500 |
| Helper, caption | 11 / 400 |

Rules:

- Never a 40px heading. `Inventory` at 20, then `Monitor stock, batches, expiry and movements.` at 13. That is enough.
- Do not make everything bold. Hierarchy comes from whitespace, size and colour.
- `font-variant-numeric: tabular-nums` on every column of digits.
- Sentence case. Never ALL CAPS body text; uppercase only for small eyebrow labels with letter-spacing.

---

## Icons

**Lucide only.** One family, everywhere.

- 16–18px, stroke 1.75–2.
- Never mix in Font Awesome, Material, Heroicons, or emoji.
- Never 24–32px colourful icons.
- Never an icon in every button — text-only buttons are usually cleaner. Use icons for navigation, compact buttons, toolbars, status, and recognition.

Canonical navigation mapping:

| Item | Icon |
|---|---|
| Overview | `LayoutDashboard` |
| Marketplace | `Store` |
| Orders | `ShoppingCart` |
| Inventory | `Package` |
| Point of sale | `Receipt` |
| Transfers | `ArrowLeftRight` |
| Prescriptions | `ClipboardList` |
| Insurance | `ShieldCheck` |
| Finance | `Wallet` |
| Reports | `ChartNoAxesCombined` |
| Settings | `Settings` |

---

## Component metrics

| Element | Value |
|---|---|
| Sidebar expanded | 248px |
| Sidebar collapsed | 68px |
| Content max-width | 1440px |
| Page padding | 24px |
| Card padding | 16–20px |
| Input height | 36–40px |
| Button height | 36px |
| Table row — compact | 40px |
| Table row — comfortable | 48px |
| Sidebar item | 36–40px |

---

## Application shell

```
┌──────────────────────────────────────────────────────────────┐
│ GLOBAL HEADER      search ⌘K              notifications  user │
├──────────────┬───────────────────────────────────────────────┤
│              │  PAGE HEADER    title · description · actions │
│   SIDEBAR    ├───────────────────────────────────────────────┤
│   248px      │  PRIMARY WORKSPACE                            │
│   grouped    ├───────────────────────────────────────────────┤
│              │  SECONDARY INFORMATION                        │
├──────────────┴───────────────────────────────────────────────┤
│ ● All systems operational        last sync 10:42       v1.0  │
└──────────────────────────────────────────────────────────────┘
```

Every module lives in this shell. Only the content area changes, and it always follows: **page header → primary workspace → secondary information**.

### Sidebar

Grouped into sections — never thirty flat items.

```
MAIN            Overview
OPERATIONS      Inventory · Point of sale · Transfers
COMMERCE        Marketplace · Orders · Procurement
PATIENTS        Prescriptions · Insurance
FINANCE         Transactions · Invoices
REPORTING       Analytics
────────────────
ADMINISTRATION  Settings
```

Active item: `--selected` background, `--brand` icon and text, 2px left border. Never a solid brand block.

### Top bar

Disappears into the environment — same neutral family as the workspace, separated by a 1px divider. Not a big white header.

The search field is **quiet**: `--hover` background, showing `⌘K`. On focus it becomes `--surface` with a brand border. A search field that shouts is competing with the content.

---

## Layout rules

**Not everything is a card.** Some sections are borderless, some carry a light background, and only important things get a true surface. The failure mode is a grid of cards where every element looks equally important.

```
❌  [ KPI card ] [ KPI card ] [ KPI card ]
    [ chart card ]
    [ table card ]

✅  Revenue  18.4M  ↑12.4%     Orders  428  ↑8.2%
    ─────────────────────────────────────────────
    Sales performance
    [ chart ]
    ─────────────────────────────────────────────
    Recent orders
    [ table, almost no decoration ]
```

**Tables dominate over cards.** In this system the table is the primary interface, not filler. Recent orders does not go inside a huge card — it is a heading, a view-all link, and rows.

**KPIs are capped at 4–6** and are borderless, separated from what follows by a single hairline.

**Use whitespace, not borders, to separate sections.** Think *information → whitespace → information*, not *border → border → border*.

---

## Core components

### DataTable

The most important component in the product. Every data list uses it; never hand-roll a `<table>`.

Provides: sticky header · sort · filter · search · column visibility · **density control** · row selection · bulk actions · pagination · inline actions · empty state · loading skeleton · CSV export.

Header on `--content`, 12/600 in `--text-2`. Rows separated by `--border-hair`. Row height by density.

### Drawer versus page

```
SEARCH → TABLE → DETAIL DRAWER → FULL TRANSACTION
```

**Drawer** for preview, quick edit, quick information, activity history. It keeps the user in context.

**Full page** for genuine workflows only: purchase order creation, import request, receiving, POS, prescription processing, insurance claim, product creation.

### Transaction template

Reusable for every document — PO, GRN, invoice, claim, import request.

```
TransactionHeader     type, number, status, linked references, actions
TransactionTabs       Overview · Items · Delivery · Documents · Approval · History
TransactionSection    grouped fields, never one long vertical wall
ItemGrid              line items with inline edit
ApprovalTimeline      created → submitted → reviewed → approved → completed
```

Forms are **grouped**, never a flat list of thirty inputs: Basic information · Pharmaceutical information · Regulatory · Commercial.

Long workflows use a **stepper**, not fifty simultaneous fields, and **autosave drafts** with a visible `Draft saved 12 seconds ago`.

### Status stepper

```
Requested ──● Quoted ──● Approved ──○ Shipped ──○ Arrived ──○ Received
```

### Command palette

`⌘K` / `Ctrl+K`. Searches products, orders, invoices, patients, prescriptions, suppliers, batches and documents — and **runs actions**: "Create purchase order", "Open GRN-00124", "Show expired products".

Shortcuts: `N` new order · `P` point of sale · `I` inventory · `R` reports.

---

## States

**Loading** — skeletons, not `Loading…`. Do not skeletonize every tiny element.

**Empty** — teach, do not apologize.

```
No purchase orders yet
Your approved purchase orders will appear here.
[ Create purchase order ]
```

**Error** — explain what happened and what to do. Never a raw backend error.

```
We couldn't submit this order.
Your connection may have been interrupted. Your draft has been saved.
[ Try again ]
```

**Validation** — immediate and domain-aware. `Product expiry date cannot be in the past.` matters more here than a generic required-field message.

**Notifications** — grouped and prioritized, never "you have 47 notifications".

```
ATTENTION
3 products expiring soon
2 orders awaiting approval
1 insurance claim rejected
```

---

## Themes

Both light and dark are first-class. Dark is not an inversion — it uses its own Fluent-style neutral ramp, and the brand lifts to `#4CA6E8` because `#0078D4` dies on a dark ground.

Implementation is token-level: define the palette on `:root`, redefine tokens under `@media (prefers-color-scheme: dark)`, then again under `:root[data-theme="dark"]` and `:root[data-theme="light"]` so an explicit toggle wins in both directions. Style components through tokens only.

Night shifts are a real use case in this domain. Dark mode is a requirement, not a nicety.

---

## Responsive

Desktop-first — this is an operational system.

| Breakpoint | Behaviour |
|---|---|
| Desktop | Sidebar 248px, content fluid to 1440px |
| Tablet | Sidebar collapsed to 68px |
| Mobile | Bottom navigation or drawer |

Tables on mobile scroll horizontally or switch to a card layout. Never force twelve columns into 375px.

POS is the exception: larger touch targets throughout, because it may run on a tablet at a counter.

---

## Accessibility

- Contrast meets WCAG AA in both themes.
- Every interactive element has a visible focus state.
- Full keyboard operation — the whole POS flow must be completable without a mouse.
- `prefers-reduced-motion` respected.
- Status is never conveyed by colour alone; a dot carries a label.
- Minimum body text 13px; never below 11px anywhere.

---

## The ten principles

1. **Calm** — healthcare software should reduce stress, not create it.
2. **Clear** — every screen has one obvious primary purpose.
3. **Dense when necessary** — professionals need information density, never visual clutter.
4. **Progressive** — advanced functionality appears when the user needs it.
5. **Consistent** — same components, spacing, icons and patterns everywhere.
6. **Fast** — minimize clicks, typing and page transitions.
7. **Traceable** — every important transaction is explainable.
8. **Accessible** — strong contrast, keyboard support, readable type, large targets.
9. **Responsive** — desktop-first for operations, usable on tablet and mobile.
10. **Intelligent** — surface what needs attention rather than making users hunt for problems.

**The target.** Open Medix and think *this is incredibly simple*. Use it for a month and think *this is extremely powerful*. Simple on the surface, deep underneath.
