/* The only file under components/charts that names a colour.
 *
 * Slots are assigned in fixed order and never cycled, which is what
 * makes colour follow the entity: filtering a series out must not
 * repaint the survivors, and it cannot if slot 2 is always slot 2.
 *
 * Four slots in light, three in dark — measured, not assumed. Green,
 * amber and red are reserved for status and can never be a series. A
 * fifth series is a signal to change the form: fold the tail into
 * "Other", facet, or use small multiples. It is never a new hue.
 *
 * Values live in tokens.css and are read at runtime rather than copied,
 * so `node scripts/validate-palette.mjs` validates what actually
 * renders. See docs/21-data-visualization.md.
 */

/** How many categorical slots this theme has been validated for. */
export const SLOTS_LIGHT = 4;
export const SLOTS_DARK = 3;

/* Recharts needs a resolved colour string, not `var(--chart-1)`, because
   it writes some fills into SVG attributes rather than CSS. Reading the
   computed custom property keeps tokens.css the single source. */
function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

export type ChartTokens = {
  series: string[];
  grid: string;
  axis: string;
  text: string;
  muted: string;
  surface: string;
  status: { ok: string; warn: string; bad: string; neutral: string };
};

/** Resolved once per render pass — a theme change remounts the chart. */
export function chartTokens(): ChartTokens {
  return {
    series: [
      token("--chart-1", "#0078d4"),
      token("--chart-2", "#b5179e"),
      token("--chart-3", "#8a5a00"),
      token("--chart-4", "#00706e"),
    ],
    grid: token("--border-hair", "#e4e8ec"),
    axis: token("--border", "#dce2e8"),
    text: token("--text", "#17212b"),
    muted: token("--text-2", "#5f6b76"),
    surface: token("--surface", "#ffffff"),
    status: {
      ok: token("--ok", "#059669"),
      warn: token("--warn", "#d97706"),
      bad: token("--bad", "#dc2626"),
      neutral: token("--text-3", "#868f98"),
    },
  };
}

/** True while the viewer is in dark, so the slot budget can tighten. */
export function isDark(): boolean {
  if (typeof window === "undefined") return false;
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit) return explicit === "dark";
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** The validated slot budget for the theme in force. */
export function slotBudget(): number {
  return isDark() ? SLOTS_DARK : SLOTS_LIGHT;
}
