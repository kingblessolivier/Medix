/* Accessibility validator.
 *
 * `validate-palette.mjs` already proves the colours meet WCAG AA in both
 * themes. Colour is only half of AA, and the other half — a control a
 * screen reader cannot name, a row only a mouse can open — is just as
 * mechanical to check. So it gets checked here rather than remembered.
 *
 * What it looks for, in order of how often it actually goes wrong:
 *
 *   1. an icon-only control with no accessible name
 *   2. a click handler on a div/span/tr that the keyboard cannot reach
 *   3. an input with neither an id from `Field` nor an aria-label
 *   4. a decorative icon that is not hidden from the reader
 *   5. a positive tabIndex, which reorders the whole document
 *   6. an image with no alt
 *
 * What it does not do: judge heading order, reading order, or whether a
 * label is a *good* name. Those need a person. This catches the ones
 * that need no judgement, so the person can spend their attention on the
 * ones that do.
 *
 *   node scripts/validate-a11y.mjs
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = resolve(ROOT, "frontend/src");

/* Elements that are not interactive and gain nothing from a click
 * handler unless they are given a role and a key handler as well. */
const INERT = ["div", "span", "li", "tr", "td", "p", "section", "article"];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (name.endsWith(".tsx")) out.push(path);
  }
  return out;
}

const lineOf = (source, index) => source.slice(0, index).split("\n").length;

/* Comments in this codebase explain the markup, so they contain markup.
 * Blanked rather than removed so every line number still lines up. */
const withoutComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "));

/** Icon components imported from lucide-react in this file. */
function lucideNames(source) {
  const names = new Set();
  const match = source.match(/import\s*\{([^}]+)\}\s*from\s*"lucide-react"/);
  if (match) {
    for (const name of match[1].split(",")) {
      const bare = name.trim().split(/\s+as\s+/).pop().trim();
      if (bare) names.add(bare);
    }
  }
  return names;
}

/** Every JSX opening tag, with its attribute text. */
function* tags(source) {
  const pattern = /<([A-Za-z][\w.]*)((?:[^<>{}]|\{[^{}]*\})*?)(\/?)>/g;
  for (const match of source.matchAll(pattern)) {
    yield {
      name: match[1],
      attrs: match[2],
      selfClosing: match[3] === "/",
      index: match.index,
      end: match.index + match[0].length,
    };
  }
}

const has = (attrs, name) => new RegExp(`(^|\\s)${name}[=\\s/]`).test(attrs + " ");

/* A primitive that forwards `{...rest}` may be given an id or a label by
 * its caller, and the caller is where that gets checked. */
const forwards = (attrs) => /\{\s*\.\.\.\w+\s*\}/.test(attrs);

function check(file, source) {
  const problems = [];
  source = withoutComments(source);
  const icons = lucideNames(source);
  const at = (index) => `${file}:${lineOf(source, index)}`;

  for (const tag of tags(source)) {
    const { name, attrs, index } = tag;

    /* 1. An icon-only control with no accessible name. The text child is
     *    the name where there is one; where the child is an icon there
     *    is nothing to read out. */
    if (name === "button" || name === "Button" || name === "IconButton") {
      const closing = source.indexOf(`</${name}>`, tag.end);
      const inner = tag.selfClosing || closing === -1 ? "" : source.slice(tag.end, closing);
      /* Anything that is not a nested element counts as a name: literal
       * text, or an expression that renders some. What is left after the
       * elements are removed is what a reader would announce. */
      const readableText = inner.replace(/<[^>]*>/g, " ").trim();
      const named =
        has(attrs, "aria-label") ||
        has(attrs, "aria-labelledby") ||
        has(attrs, "title") ||
        forwards(attrs);
      if (!readableText && !named) {
        problems.push(`${at(index)}  <${name}> has no accessible name`);
      }
    }

    /* 2. A handler on something the keyboard cannot reach. The scrim is
     *    the one exception: it is aria-hidden and Escape does the same
     *    job, so a keyboard user is not stranded. */
    if (INERT.includes(name) && has(attrs, "onClick")) {
      const reachable =
        (has(attrs, "role") && has(attrs, "tabIndex") && has(attrs, "onKeyDown")) ||
        has(attrs, "aria-hidden");
      if (!reachable) {
        problems.push(
          `${at(index)}  <${name} onClick> is mouse-only — needs role, tabIndex and onKeyDown`,
        );
      }
    }

    /* 3. An input with no name. `Field` passes an id to its child and
     *    renders the <label htmlFor>, so an id is a name here. */
    if (["input", "select", "textarea"].includes(name)) {
      const named =
        has(attrs, "id") ||
        has(attrs, "aria-label") ||
        has(attrs, "aria-labelledby") ||
        forwards(attrs);
      if (!named) problems.push(`${at(index)}  <${name}> has no label`);
    }

    /* 4. A decorative icon read aloud is noise between the label and the
     *    value. Hidden unless it is the only content of a control, where
     *    the control's own aria-label does the naming. */
    if (icons.has(name) && !has(attrs, "aria-hidden") && !has(attrs, "aria-label")) {
      problems.push(`${at(index)}  <${name}> icon is not aria-hidden`);
    }

    /* 5. A positive tabIndex reorders the entire document, not just this
     *    component. */
    const tabIndex = attrs.match(/tabIndex=\{(-?\d+)\}/);
    if (tabIndex && Number(tabIndex[1]) > 0) {
      problems.push(`${at(index)}  tabIndex={${tabIndex[1]}} reorders the document`);
    }

    /* 6. */
    if (name === "img" && !has(attrs, "alt")) {
      problems.push(`${at(index)}  <img> has no alt`);
    }
  }

  return problems;
}

const files = walk(SRC);
const problems = files.flatMap((path) =>
  check(relative(ROOT, path).replace(/\\/g, "/"), readFileSync(path, "utf8")),
);

if (problems.length === 0) {
  console.log(`PASS  ${files.length} files`);
  process.exit(0);
}

console.error(`FAIL  ${problems.length} problem${problems.length === 1 ? "" : "s"}\n`);
for (const line of problems) console.error("  " + line);
process.exit(1);
