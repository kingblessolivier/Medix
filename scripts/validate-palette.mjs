/* Palette validator.
 *
 * docs/21-data-visualization.md says the colour decisions are computable,
 * so they get computed rather than judged by eye. This is that script.
 *
 * It reads the real tokens out of src/design/tokens.css — both themes —
 * and checks every pairing the interface actually renders:
 *
 *   text        WCAG AA 4.5:1   body, table cells, banner copy
 *   large text  WCAG AA 3.0:1   page titles at 20/600 and metrics
 *   graphical   WCAG AA 3.0:1   status dots, borders, focus rings
 *   chart       3.0:1 vs ground, and separable from each other under
 *               normal vision and the three dichromacies
 *
 * The trap this exists to catch: a saturated status hue reads fine as a
 * 8px dot and fails as text on its own tint. Green on green tint is the
 * usual casualty.
 *
 *   node scripts/validate-palette.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TOKENS = resolve(ROOT, "frontend/src/design/tokens.css");
const TAILWIND = resolve(ROOT, "frontend/tailwind.config.js");

/* ---------- colour maths ------------------------------------------- */

function rgb(hex) {
  const v = hex.trim().replace("#", "");
  const full = v.length === 3 ? [...v].map((c) => c + c).join("") : v;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
}

const channel = (c) => {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};

const luminance = ([r, g, b]) =>
  0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);

function contrast(a, b) {
  const [x, y] = [luminance(rgb(a)), luminance(rgb(b))];
  const [hi, lo] = x > y ? [x, y] : [y, x];
  return (hi + 0.05) / (lo + 0.05);
}

/* CIE Lab, for perceptual distance rather than RGB distance. */
function lab(hex) {
  let [r, g, b] = rgb(hex).map((c) => {
    const s = c / 255;
    return s > 0.04045 ? ((s + 0.055) / 1.055) ** 2.4 : s / 12.92;
  });
  const x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047;
  const y = r * 0.2126 + g * 0.7152 + b * 0.0722;
  const z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883;
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}

const deltaE = (a, b) => {
  const [l1, a1, b1] = lab(a);
  const [l2, a2, b2] = lab(b);
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2);
};

/* Brettel-style dichromacy simulation, good enough to catch collisions. */
const CVD = {
  deuteranopia: [
    [0.625, 0.375, 0], [0.7, 0.3, 0], [0, 0.3, 0.7],
  ],
  protanopia: [
    [0.567, 0.433, 0], [0.558, 0.442, 0], [0, 0.242, 0.758],
  ],
  tritanopia: [
    [0.95, 0.05, 0], [0, 0.433, 0.567], [0, 0.475, 0.525],
  ],
};

function simulate(hex, kind) {
  const [r, g, b] = rgb(hex);
  const m = CVD[kind];
  const out = m.map((row) =>
    Math.max(0, Math.min(255, Math.round(row[0] * r + row[1] * g + row[2] * b))),
  );
  return "#" + out.map((c) => c.toString(16).padStart(2, "0")).join("");
}

/* ---------- token parsing ------------------------------------------- */

function themeTokens(css, selector) {
  const start = css.indexOf(selector);
  if (start === -1) throw new Error(`No block for ${selector}`);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  const body = css.slice(open + 1, close);
  const tokens = {};
  for (const [, name, value] of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    if (value.trim().startsWith("#")) tokens[name] = value.trim();
  }
  return tokens;
}

/* ---------- the checks ---------------------------------------------- */

/** Text pairs: foreground token rendered as copy on a background token. */
const TEXT = [
  ["text", "surface"], ["text", "content"], ["text", "app"],
  ["text-2", "surface"], ["text-2", "content"], ["text-2", "app"], ["text-2", "nav"],
  ["text", "ok-bg"], ["text", "warn-bg"], ["text", "bad-bg"],
  ["brand-text", "surface"], ["brand-text", "selected"], ["brand-text", "brand-weak"],
  ["ok-text", "surface"], ["ok-text", "ok-bg"],
  ["warn-text", "surface"], ["warn-text", "warn-bg"],
  ["bad-text", "surface"], ["bad-text", "bad-bg"],
  ["info-text", "surface"], ["info-text", "info-bg"],
  // The label on a primary button. Not always white: in dark the brand is
  // a pale green, where white would sit at 1.6:1.
  ["on-brand", "brand"],
];

/* Two colours that mean different things must not read as the same
 * colour. The brand is green here, and so is success — they appear in the
 * same status column, so they are held apart perceptually. */
const DISTINCT = [
  ["brand", "ok", 12],
  ["brand-text", "ok-text", 10],
  ["info", "ok", 20],
];

/** Dimmed metadata and disabled copy — held to large-text 3:1. */
const LARGE = [["text-3", "surface"], ["text-3", "content"]];

/* Marks, not letterforms: status dots, focus rings, and the edge of a
 * control. Dividers (--border, --border-hair) are deliberately absent —
 * WCAG 1.4.11 exempts pure decoration, and a table rule that met 3:1
 * would draw more attention than the data. The edge of an input is not
 * decoration, which is why --border-control exists. */
const GRAPHICAL = [
  ["ok", "surface"], ["warn", "surface"], ["bad", "surface"],
  ["brand", "surface"], ["brand", "content"],
  ["border-control", "surface"], ["border-control", "content"],
];

const CHART = ["chart-1", "chart-2", "chart-3", "chart-4"];

function check(themeName, tokens) {
  const failures = [];
  const note = (msg) => failures.push(`${themeName}: ${msg}`);
  const has = (t) => tokens[t] !== undefined;

  for (const [fg, bg] of TEXT) {
    if (!has(fg) || !has(bg)) {
      note(`missing token ${!has(fg) ? fg : bg}`);
      continue;
    }
    const r = contrast(tokens[fg], tokens[bg]);
    if (r < 4.5) note(`text ${fg} on ${bg} is ${r.toFixed(2)}:1, needs 4.5`);
  }

  for (const [fg, bg] of LARGE) {
    const r = contrast(tokens[fg], tokens[bg]);
    if (r < 3) note(`large text ${fg} on ${bg} is ${r.toFixed(2)}:1, needs 3.0`);
  }

  for (const [fg, bg] of GRAPHICAL) {
    const r = contrast(tokens[fg], tokens[bg]);
    if (r < 3) note(`mark ${fg} on ${bg} is ${r.toFixed(2)}:1, needs 3.0`);
  }

  for (const [a, b, floor] of DISTINCT) {
    if (!has(a) || !has(b)) {
      note(`missing token ${!has(a) ? a : b}`);
      continue;
    }
    const d = deltaE(tokens[a], tokens[b]);
    if (d < floor) note(`${a} vs ${b} ΔE ${d.toFixed(1)}, needs ${floor} — they mean different things`);
  }

  const series = CHART.filter(has);
  for (const c of series) {
    const r = contrast(tokens[c], tokens.surface);
    if (r < 3) note(`chart ${c} on surface is ${r.toFixed(2)}:1, needs 3.0`);
  }
  for (let i = 0; i < series.length; i++) {
    for (let j = i + 1; j < series.length; j++) {
      const [a, b] = [tokens[series[i]], tokens[series[j]]];
      const d = deltaE(a, b);
      if (d < 12) {
        note(`chart ${series[i]} vs ${series[j]} ΔE ${d.toFixed(1)}, needs 12`);
        continue;
      }
      for (const kind of Object.keys(CVD)) {
        const dc = deltaE(simulate(a, kind), simulate(b, kind));
        if (dc < 9) {
          note(
            `chart ${series[i]} vs ${series[j]} ΔE ${dc.toFixed(1)} under ${kind}, needs 9`,
          );
        }
      }
    }
  }
  return failures;
}

/* ---------- run ------------------------------------------------------ */

const css = readFileSync(TOKENS, "utf8");
const themes = {
  light: themeTokens(css, ':root[data-theme="light"]'),
  dark: themeTokens(css, ':root[data-theme="dark"]'),
};

const all = Object.entries(themes).flatMap(([name, tokens]) => check(name, tokens));

/* A correct token nobody can reach is not a correct colour.
 *
 * Every colour token has to be bridged into Tailwind or the class that
 * uses it resolves to nothing and the text quietly inherits --text. That
 * happened: --ok-text/--warn-text/--bad-text were defined, validated and
 * used, and none of them ever reached the page. Contrast checks passed
 * throughout, because they were reading tokens.css rather than what the
 * components could actually name. */
const bridged = new Set(
  [...readFileSync(TAILWIND, "utf8").matchAll(/var\(--([\w-]+)\)/g)].map((m) => m[1]),
);
/* Chart series are read straight off the custom property by the charting
 * layer — an SVG fill, never a Tailwind class — so they need no bridge. */
const NO_BRIDGE = /^chart-\d+$/;

for (const name of Object.keys(themes.light)) {
  if (NO_BRIDGE.test(name)) continue;
  if (!bridged.has(name)) {
    all.push(`bridge: --${name} is defined in tokens.css but not exposed in tailwind.config.js`);
  }
}

if (all.length === 0) {
  const counts = Object.entries(themes)
    .map(([n, t]) => `${n} ${Object.keys(t).length} tokens`)
    .join(", ");
  console.log(`PASS  ${TEXT.length + LARGE.length + GRAPHICAL.length} pairs per theme  (${counts})`);
  process.exit(0);
}

console.error(`FAIL  ${all.length} problem${all.length === 1 ? "" : "s"}\n`);
for (const line of all) console.error("  " + line);
process.exit(1);
