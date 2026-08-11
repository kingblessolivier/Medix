# 21 — Data visualization

Charts in Medix answer questions. A chart that does not change a decision does not ship.

> **The colour part is computable, so it was computed.** Every palette below was validated with a six-check script — lightness band, chroma floor, colour-vision-deficiency separation across all pairs, normal-vision floor, and contrast against the surface. Nothing here was chosen by eye. Re-run the validator before changing any value.

---

## 1 — Choose the form before the colour

| The data's job | Form | Notes |
|---|---|---|
| One headline number | **Stat tile / hero number** | Not a chart. Most KPIs belong here |
| Magnitude across categories | **Horizontal bar** | Sorted by value, not alphabetically |
| Change over time, one or few series | **Line** | ≤4 series |
| Change over time, part-to-whole | **Stacked area** | Only when the total also matters |
| Composition at one moment | **Stacked bar (single)** | Never a pie. Never a donut |
| Distribution | **Histogram** or **box** | Batch age, basket size |
| Two measures, relationship | **Scatter** | Rare here |
| Risk banding | **Segmented bar with counts** | Expiry exposure |
| Progress to a target | **Bullet / progress bar** | MOQ fill, licence countdown |

**Often the answer is not a chart.** The pharmacist overview shows four numbers and a table — no chart at all above the fold. The executive overview earns exactly one.

### Forms banned in Medix

| Banned | Why | Use instead |
|---|---|---|
| **Pie and donut** | Angle comparison is unreliable; useless past 3 slices | Sorted bar, or a single stacked bar |
| **Dual-axis** | Two y-scales invite invented correlation. The single most common chart mistake | Two charts, small multiples, or index to a common base |
| **3D anything** | Perspective distorts magnitude | The 2D form |
| **Radar / spider** | Area scales with the square of the value | Sorted bar |
| **Gauge / speedometer** | Enormous ink for one number | Stat tile with a trend |
| **Word cloud** | Size encodes nothing reliable | Ranked list |
| **Decorative sparkline on every row** | Noise at table density | Sparkline only where trend is the point |

---

## 2 — The palettes

### Categorical — light mode

Four slots. Assigned **in fixed order, never cycled**.

| Slot | Hex | Typical use |
|---|---|---|
| 1 | `#0078D4` | First series — usually the primary measure |
| 2 | `#B5179E` | Second series |
| 3 | `#8A5A00` | Third series |
| 4 | `#009B9B` | Fourth series |

```
node scripts/validate_palette.js "#0078D4,#B5179E,#8A5A00,#009B9B" --mode light --pairs all

[PASS] Lightness band       all 4 inside L 0.43–0.77
[PASS] Chroma floor         all 4 >= 0.1
[PASS] CVD separation       worst all-pairs ΔE 9.3 (deutan)
[PASS] Normal-vision floor  worst all-pairs ΔE 15.1
[PASS] Contrast vs surface  all 4 >= 3:1
```

### Categorical — dark mode

**Selected independently, not lightened.** Naively lifting the light palette compresses every hue into one lightness band and separation collapses — measured, not assumed.

| Slot | Hex |
|---|---|
| 1 | `#3B96E3` |
| 2 | `#D253B4` |
| 3 | `#B5892C` |

```
node scripts/validate_palette.js "#3B96E3,#D253B4,#B5892C" --mode dark --surface "#1E242A" --pairs all
→ ALL CHECKS PASS   (worst all-pairs ΔE 8.4 deutan, 24.1 normal)
```

### Why only three and four

Green, amber and red are **reserved for status** and can never be reused as a series colour. That removes most of the hue circle. Every candidate fourth dark slot failed:

| Candidate | Failure |
|---|---|
| Teal `#0E8F8C` | Chroma floor; normal-vision ΔE 13.7 vs blue |
| Brighter teal `#19BDB4` | Outside lightness band; ΔE 14.8 vs blue |
| Violet `#9B6BE8` | CVD ΔE 2.8 vs blue under deuteranopia — invisible difference |

Violet against blue is the classic deuteranopia collision. It looks fine to us and vanishes for roughly one in twelve men.

### The rule that follows

**More than 4 series (light) or 3 (dark) is a signal to change the form, not to add a hue.**

1. Fold the tail into **Other**, sorted by value
2. Use **small multiples** — one panel per category, same scale
3. Facet by the dimension instead of colouring by it

A ninth series is never a generated hue.

---

## 3 — Sequential and diverging

**Sequential** — one hue, light to dark. For magnitude: stock value by branch, sales heat by hour.

```
#E8F1FB  #BEDAF2  #8DBDE8  #4E97DA  #0078D4  #005A9E  #003D6B
```

**Diverging** — two hues with a **neutral grey midpoint**, never a hue at zero. For variance against target, margin against plan, price change.

```
#B5179E ← #D46BC2 ← #E8B4DE ← #E5E7EA → #9CC9E8 → #3B96E3 → #0078D4
   under                        on target                      over
```

Never a rainbow. Never green-to-red as a diverging pair — it collides with status and fails for colour-blind readers in the one place where direction is the entire message.

---

## 4 — Status colours are reserved

| State | Light | Dark | Means |
|---|---|---|---|
| Good | `#059669` | `#34C88A` | Available, approved, healthy |
| Warning | `#D97706` | `#DFA23A` | Expiring, pending, low |
| Critical | `#DC2626` | `#EA6E66` | Expired, rejected, recalled |
| Neutral | `#5F6B76` | `#96A2AC` | Draft, not applicable |

Rules:

- **Never used as a series colour**, in any chart, ever.
- Always shipped with **an icon or a label** — never colour alone.
- Never colour an entire table row; use a `●` dot or a small badge.

> **Note for implementers.** Categorical slot 3 (`#8A5A00` / `#B5892C`) sits near the warning hue. They never share an encoding role, but do not place a categorical gold series immediately beside a status badge in the same visual block.

---

## 5 — Mark specification

| Element | Spec |
|---|---|
| Bar / column | 4px rounded on the data end only, square at the baseline |
| Bar gap | 2px surface-coloured gap between adjacent bars and stacked segments |
| Line | 2px, no drop shadow, no gradient fill under it unless the area is the point |
| Area fill | 12% opacity of the series hue, hard 2px line on top |
| Point marker | ≥8px, 2px surface ring where marks overlap |
| Grid | Horizontal only, `--border-hair`, behind the marks |
| Axis | 1px `--border`; no axis box, no vertical axis line on bar charts |
| Zero line | Always visible when values can be negative |

**Bars start at zero. Always.** A truncated bar axis misrepresents magnitude and is the second most common chart lie after dual axes. Line charts may use a non-zero axis when the change is the subject — say so in the axis label.

---

## 6 — Text and labels

**Text wears text tokens, never the series colour.** A coloured mark beside a label carries identity; the label itself stays `--text` or `--text-2`.

- Numbers use `font-variant-numeric: tabular-nums` everywhere.
- **Selective direct labels** — never a number on every point. Label the endpoint, the extremes, and the value being discussed.
- Legend is present whenever there are ≥2 series. A single series needs none — the title names it.
- With ≤4 series, direct-label as well as legend, so identity never depends on colour alone.
- Axis labels state the unit: `RWF (millions)`, `packs`, `days to expiry`.

---

## 7 — Interaction

An HTML chart is interactive by default.

| Form | Hover behaviour |
|---|---|
| Line, area | Crosshair with a tooltip showing all series at that x |
| Bar, column | Per-mark tooltip |
| Heat cell | Per-cell tooltip |
| Stat tile with no plot | None |

Hit targets are larger than the mark. Filters sit in one row above the chart, never inside it. Every chart offers a **table view** toggle — this is the accessibility fallback and the relief for any contrast warning.

---

## 8 — The Medix chart inventory

Every chart that ships, and the question it answers.

### Retail pharmacist
| Chart | Form | Question |
|---|---|---|
| Sales today | Stat tile + sparkline | Are we on pace? |
| Expiry exposure | Segmented bar, status colours | What is about to be lost? |
| Hourly sales | Column | When is the counter busy? |

### Owner / executive
| Chart | Form | Question |
|---|---|---|
| Revenue trend | Line, 1 series | Are we growing? |
| Gross margin by category | Horizontal bar, sorted | What actually makes money? |
| Branch comparison | Grouped bar, ≤3 series | Which branch is underperforming? |
| Stock value vs expiry risk | Stacked bar | How much capital is at risk? |
| Receivables aging | Stacked bar by bucket | What is owed and how late? |
| Vendor price movement | Line, small multiples | Who is quietly raising prices? |

### Inventory
| Chart | Form | Question |
|---|---|---|
| Expiry banding | Segmented bar with counts | 30 / 90 / 180 / healthy |
| Stock-out days | Column | Which fast movers ran dry? |
| Batch age distribution | Histogram | Is stock turning? |
| Cold chain excursions | Timeline strip | When did temperature leave range? |

### Wholesale
| Chart | Form | Question |
|---|---|---|
| Order fulfilment rate | Line | Are we shipping on time? |
| Demand by product | Horizontal bar, sorted | What are pharmacies asking for? |
| Consolidation fill | Bullet vs MOQ | Will this import go ahead? |

### Compliance
| Chart | Form | Question |
|---|---|---|
| Licence expiry runway | Horizontal bar by days remaining | What lapses next? |
| Fiscal submission rate | Line with threshold band | Are we compliant today? |
| Claim turnaround | Box plot by scheme | Which insurer is slow? |

---

## 9 — Implementation

**Library:** Recharts, wrapped in a Medix `<Chart>` component so no screen touches the library directly. That wrapper owns tokens, the palette, tooltips, the legend, the table-view toggle, empty and loading states.

```tsx
<Chart.Line
  data={revenueByMonth}
  series={[{ key: "revenue", label: "Revenue" }]}
  yUnit="RWF (millions)"
  emptyMessage="No sales recorded yet."
/>
```

**Never** pass a colour into a chart component. The palette is assigned by slot order inside the wrapper — which is what guarantees colour follows the entity and a filter that removes a series does not repaint the survivors.

### Rules enforced by lint

- No hex colour in any file under `components/charts/` except the palette module
- No `PieChart`, `RadarChart`, or a second `YAxis` in one chart — banned imports and a custom rule
- Every `<Chart.*>` requires `emptyMessage`

---

## 10 — Review checklist

- [ ] The chart answers a stated question; a stat tile would not do better
- [ ] Form chosen before colour
- [ ] Palette assigned by slot order, not cycled
- [ ] **Validator run and passing** for any changed palette, in both modes
- [ ] Single y-axis
- [ ] Bars start at zero
- [ ] Status colours not used as series colours
- [ ] Legend for ≥2 series; direct labels for ≤4
- [ ] Text in text tokens, not series colours
- [ ] Tabular figures on all numbers
- [ ] Axis labels carry units
- [ ] Hover layer present
- [ ] Table view available
- [ ] Correct in both themes — dark independently checked, not assumed
- [ ] Rendered and looked at: no label collisions, no overflow
