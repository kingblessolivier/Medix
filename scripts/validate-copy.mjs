/* Copy validator.
 *
 * docs/23-ui-copy.md sets word limits and a banned list, and neither is
 * something a reviewer can hold in their head across ninety screens. The
 * limits are countable, so they get counted.
 *
 * What it reads: the copy-bearing props of the shared primitives
 * (`title`, `description`, `heading`, `body`, `label`, `message`,
 * `placeholder`, `confirmLabel`) and the text children of `<Button>`.
 * That is where interface copy actually lives — a string in a `const`
 * that is later passed to one of those props gets caught at the prop.
 *
 * What it deliberately does not read: `className`, event handlers, chart
 * axis keys, and anything inside `{}`. A false positive that has to be
 * silenced every week is a lint nobody runs.
 *
 *   node scripts/validate-copy.mjs
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = resolve(ROOT, "frontend/src");

/* ---------- the table in docs/23 ------------------------------------- */

const LIMITS = {
  button: 3,
  label: 3,
  title: 3,
  description: 8,
  heading: 5,
  body: 10,
  error: 12,
  tooltip: 8,
  confirmation: 15,
};

/* Which prop carries which kind of copy. A prop not listed here is not
 * interface copy — `name`, `code`, `id` and friends are data. */
const PROPS = {
  title: "title",
  description: "description",
  heading: "heading",
  body: "body",
  label: "label",
  message: "error",
  placeholder: "label",
  confirmLabel: "button",
  cancelLabel: "button",
  emptyHeading: "heading",
  emptyBody: "body",
  helper: "tooltip",
  tooltip: "tooltip",
};

/* Elements whose text children are copy. */
const CHILDREN = {
  Button: "button",
  StatusPill: "label",
  Badge: "label",
  StatusDot: "label",
};

/* ---------- the banned list ------------------------------------------ */

const BANNED = [
  [/\bplease\b/i, "please — the tool is not asking a favour"],
  [/\bsorry\b/i, "sorry — not an apology"],
  [/\bunfortunately\b/i, "unfortunately — not an apology"],
  [/\bsuccessfully\b/i, "successfully — the result is the confirmation"],
  [/\bsimply\b/i, "simply — presumes"],
  [/\beasy\b|\beasily\b/i, "easy — presumes"],
  [/\bclick here\b/i, "click here — the label names the destination"],
  [/\boops\b|\bwhoops\b/i, "oops — not a toy"],
  [/\bare you sure\b/i, "are you sure — say what happens instead"],
  [/(^|\s)(we|our|us)(\s|$|'|,|\.)/i, "first person — the system has no we"],
  [/!/, "! — shouty"],
];

/* Trade abbreviations and proper nouns keep their capitals mid-sentence.
 * docs/23: abbreviations the trade already uses are fine; never invent one. */
const KEEPS_CAPITALS = new Set(
  (
    "GRN PO POM RFQ MOQ EBM FEFO RWF VAT RRA FDA SKU UoM ID QR VSDC CSV PDF OTC ASN OCR " +
    "Medix Rwanda Kigali RSSB MPI CIF FOB EXW UoM API IP POS TIN NDC GTIN ATC WHO " +
    "Q1 Q2 Q3 Q4 A B C I II III IV V"
  ).split(" "),
);

/* ---------- extraction ------------------------------------------------ */

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (name.endsWith(".tsx") || name.endsWith(".ts")) out.push(path);
  }
  return out;
}

function lineOf(source, index) {
  return source.slice(0, index).split("\n").length;
}

/** Copy passed as a literal prop: `title="Stock on hand"`. */
function fromProps(source) {
  const found = [];
  const pattern = /(\w+)=\{?"([^"\n]{2,})"\}?/g;
  for (const match of source.matchAll(pattern)) {
    const kind = PROPS[match[1]];
    if (!kind) continue;
    found.push({ kind, text: match[2], line: lineOf(source, match.index) });
  }
  return found;
}

/** Copy as the text child of a known element: `<Button>Create order</Button>`. */
function fromChildren(source) {
  const found = [];
  for (const [element, kind] of Object.entries(CHILDREN)) {
    const pattern = new RegExp(
      `<${element}(?:\\s[^>]*)?>\\s*([^<>{}\\n][^<>{}]*?)\\s*</${element}>`,
      "g",
    );
    for (const match of source.matchAll(pattern)) {
      found.push({ kind, text: match[1].trim(), line: lineOf(source, match.index) });
    }
  }
  return found;
}

/* ---------- checks ---------------------------------------------------- */

const words = (text) => text.trim().split(/\s+/).filter(Boolean);

function check(file, entry) {
  const problems = [];
  const { kind, text, line } = entry;
  const where = `${file}:${line}`;

  /* Interpolated copy is checked at its parts, not as a template. */
  if (text.includes("${") || text.includes("{")) return problems;

  const limit = LIMITS[kind];
  const count = words(text).length;
  if (limit && count > limit) {
    problems.push(`${where}  ${kind} is ${count} words, limit ${limit}: "${text}"`);
  }

  for (const [pattern, why] of BANNED) {
    if (pattern.test(text)) problems.push(`${where}  ${why}: "${text}"`);
  }

  /* No terminal punctuation on labels, headings, buttons. Full stops
   * belong in helper text and errors, which may be two sentences. */
  if (["button", "label", "title", "heading"].includes(kind) && /[.:;]$/.test(text)) {
    problems.push(`${where}  ${kind} ends in punctuation: "${text}"`);
  }

  /* Sentence case. Title Case is the most common drift because it reads
   * as "designed" — it is just louder.
   *
   * Checked per sentence, not per string: helper text and errors are
   * allowed two sentences, and the second one starts with a capital
   * like every sentence does. */
  for (const sentence of text.split(/(?<=[.?])\s+/)) {
    let flagged = false;
    for (const word of words(sentence).slice(1)) {
      const bare = word.replace(/[^A-Za-z]/g, "");
      if (!bare || KEEPS_CAPITALS.has(bare)) continue;
      if (bare.length > 1 && /^[A-Z][a-z]/.test(bare)) {
        problems.push(`${where}  Title Case: "${text}" — sentence case only`);
        flagged = true;
        break;
      }
      if (bare.length > 2 && bare === bare.toUpperCase()) {
        problems.push(`${where}  ALL CAPS: "${text}" — only 10px group headers`);
        flagged = true;
        break;
      }
    }
    if (flagged) break;
  }

  return problems;
}

/* ---------- run ------------------------------------------------------- */

const files = walk(SRC);
const problems = [];
let checked = 0;

for (const path of files) {
  const source = readFileSync(path, "utf8");
  const file = relative(ROOT, path).replace(/\\/g, "/");
  for (const entry of [...fromProps(source), ...fromChildren(source)]) {
    checked += 1;
    problems.push(...check(file, entry));
  }
}

if (problems.length === 0) {
  console.log(`PASS  ${checked} strings across ${files.length} files`);
  process.exit(0);
}

console.error(`FAIL  ${problems.length} problem${problems.length === 1 ? "" : "s"} in ${checked} strings\n`);
for (const line of problems) console.error("  " + line);
process.exit(1);
