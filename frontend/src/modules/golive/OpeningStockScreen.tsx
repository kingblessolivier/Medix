/* Go-live — what is already on the shelves.
 *
 * The screen that decides whether anybody can start. A pharmacy adopting
 * Medix is standing in a room with two thousand boxes on the shelves,
 * and until those exist in the ledger every other feature is describing
 * an empty shop.
 *
 * Paste rather than type. Nobody is keying two thousand rows into a form,
 * and every pharmacy already has this list somewhere — a stock-take
 * sheet, a supplier statement, an old system's export. So the screen
 * takes a spreadsheet's worth of tab- or comma-separated text, shows what
 * it understood, and says which rows it could not use and why, with their
 * row numbers, so the sheet can be corrected and pasted again.
 *
 * Opening is not receiving, and the copy says so where it matters: this
 * stock was already here, and recording it as a purchase would inflate
 * the first period's buying and make the first month's margin
 * meaningless.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiFailure, api, type ProductRow } from "@/lib/api";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  Banner,
  Button,
  Field,
  PageHeader,
  Select,
  Skeleton,
  StatusPill,
} from "@/components/ui";
import { Consequence, Help, NextAction } from "@/components/ui/Guidance";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

type Parsed = {
  line: number;
  raw: string;
  name: string;
  product: ProductRow | null;
  batch_number: string;
  expiry_date: string;
  quantity: number;
  uom_code: string;
  unit_cost_base: number;
  problem: string;
};

const HEADINGS = /^(product|name|medicine)\b/i;

/** Anything a spreadsheet exports: tabs, commas or semicolons. */
function fields(row: string): string[] {
  const separator = row.includes("\t") ? "\t" : row.includes(";") ? ";" : ",";
  return row.split(separator).map((cell) => cell.trim().replace(/^"|"$/g, ""));
}

/* Matched on name because that is what a pharmacy's own sheet holds — an
   id would mean the list came out of Medix, and if it had, this screen
   would not be needed. Exact first, then a unique prefix; an ambiguous
   name is reported rather than guessed, because guessing here puts the
   wrong medicine on the shelf. */
function match(name: string, products: ProductRow[]): [ProductRow | null, string] {
  const needle = name.trim().toLowerCase();
  if (!needle) return [null, "No product name"];

  const exact = products.filter((p) => p.name.toLowerCase() === needle);
  if (exact.length === 1) return [exact[0], ""];

  const partial = products.filter((p) => p.name.toLowerCase().startsWith(needle));
  if (partial.length === 1) return [partial[0], ""];
  if (partial.length > 1) return [null, "Matches more than one product"];
  return [null, "No product of that name"];
}

function parse(text: string, products: ProductRow[]): Parsed[] {
  return text
    .split(/\r?\n/)
    .map((raw, index) => ({ raw: raw.trim(), line: index + 1 }))
    .filter((row) => row.raw.length > 0)
    // A pasted sheet usually brings its header with it.
    .filter((row) => !HEADINGS.test(row.raw))
    .map(({ raw, line }) => {
      const [name = "", batch = "", expiry = "", quantity = "", unit = "", cost = ""] =
        fields(raw);
      const [product, problem] = match(name, products);
      const count = Number(quantity);

      return {
        line,
        raw,
        name,
        product,
        batch_number: batch,
        expiry_date: expiry,
        quantity: Number.isFinite(count) ? count : 0,
        uom_code: unit.toUpperCase(),
        unit_cost_base: Number(cost) || 0,
        problem:
          problem ||
          (!batch ? "No batch number" : "") ||
          (!expiry ? "No expiry date" : "") ||
          (count > 0 ? "" : "Quantity must be positive"),
      };
    });
}

export function OpeningStockScreen() {
  const [locationId, setLocationId] = useState("");
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState<{
    batches: number;
    movements: number;
    base_units: number;
    skipped: { row: number; reason: string }[];
  } | null>(null);
  const [failure, setFailure] = useState("");

  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.locations() });
  const products = useQuery({
    queryKey: ["products", "all"],
    queryFn: () => api.products("?page_size=500"),
  });

  const rows = parse(text, products.data?.results ?? []);
  const usable = rows.filter((row) => row.product && !row.problem);
  const unusable = rows.filter((row) => !row.product || row.problem);

  const load = useMutation({
    mutationFn: () =>
      api.loadOpeningStock({
        location: locationId,
        rows: usable.map((row) => ({
          product: row.product!.id,
          batch_number: row.batch_number,
          expiry_date: row.expiry_date,
          quantity: row.quantity,
          uom_code: row.uom_code,
          unit_cost_base: row.unit_cost_base,
        })),
      }),
    onSuccess: (result) => {
      setLoaded(result);
      setText("");
      setFailure("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not loaded."),
  });

  if (locations.isPending || products.isPending) return <Skeleton className="h-[400px]" />;

  const columns: Column<Parsed>[] = [
    { key: "line", header: "Row", numeric: true, render: (r) => String(r.line) },
    {
      key: "product",
      header: "Product",
      render: (r) =>
        r.product ? (
          r.product.name
        ) : (
          <span className="text-bad-text">{r.name || "—"}</span>
        ),
    },
    { key: "batch", header: "Batch", mono: true, render: (r) => r.batch_number || "—" },
    { key: "expiry", header: "Expiry", render: (r) => r.expiry_date || "—" },
    {
      key: "quantity",
      header: "Quantity",
      numeric: true,
      render: (r) => `${r.quantity.toLocaleString()} ${r.uom_code.toLowerCase()}`,
    },
    {
      key: "cost",
      header: "Unit cost",
      numeric: true,
      render: (r) => MONEY.format(r.unit_cost_base),
    },
    {
      key: "state",
      header: "",
      render: (r) =>
        r.problem ? (
          <StatusPill tone="bad">{r.problem}</StatusPill>
        ) : (
          <StatusPill tone="ok">Ready</StatusPill>
        ),
    },
  ];

  if (loaded) {
    return (
      <>
        <PageHeader title="Opening stock" description="What was already on the shelves" />
        <Banner tone="ok" className="mb-4">
          {`${loaded.movements} batches on the shelf · ${loaded.base_units.toLocaleString()} base units.`}
        </Banner>
        {loaded.skipped.length > 0 && (
          <>
            <h2 className="mb-2 text-section font-semibold text-text">Not loaded</h2>
            <ul className="mb-4 flex flex-col divide-y divide-hair border-y border-hair">
              {loaded.skipped.map((row) => (
                <li key={row.row} className="flex gap-3 py-2 text-body">
                  <span className="tabular text-text-3">Row {row.row}</span>
                  <span className="text-text-2">{row.reason}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        <Button variant="primary" onClick={() => setLoaded(null)}>
          Load more
        </Button>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Opening stock"
        description="What was already on the shelves"
      />

      <NextAction
        heading="Paste your stock list"
        detail="One row per batch. Nothing is on the shelf until this is done."
      />

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      <div className="mb-4 flex flex-col gap-4 rounded-lg border border-border bg-surface p-4">
        <div className="max-w-xs">
          <Field label="Room" required>
            {(id) => (
              <Select
                id={id}
                value={locationId}
                onChange={(e) => setLocationId(e.target.value)}
              >
                <option value="">Choose a room</option>
                {(locations.data?.results ?? []).map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>

        <Field
          label={
            (
              <Help term="Stock list">
                Product, batch, expiry, quantity, unit, cost — one row each, separated
                by tabs or commas. Paste it straight from a spreadsheet.
              </Help>
            ) as unknown as string
          }
        >
          {(id) => (
            <textarea
              id={id}
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
              className="w-full rounded-md border border-control bg-surface px-3 py-2 font-mono text-body text-text placeholder:text-text-3"
            />
          )}
        </Field>

        {/* The shape of a row, shown rather than described. A placeholder
            this long is unreadable in a textarea and disappears the
            moment somebody starts typing — which is exactly when they
            still need it. */}
        <p className="font-mono text-help text-text-3">
          Amoxicillin 500mg, AMX-0021, 2027-05-31, 40, PACK, 280
        </p>
      </div>

      {rows.length > 0 && (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-body text-text-2">
              {`${usable.length} ready`}
              {unusable.length > 0 && ` · ${unusable.length} to fix`}
            </p>
            <Button
              variant="primary"
              disabled={!locationId || usable.length === 0}
              loading={load.isPending}
              onClick={() => load.mutate()}
            >
              Put on the shelf
            </Button>
          </div>

          {/* Said before the button, not after it. Recording go-live
              stock as a purchase would inflate the first period's buying
              and make the first month's margin meaningless. */}
          <div className="mb-4">
            <Consequence
              lines={[
                "Recorded as opening stock, not as a purchase.",
                "Each batch keeps the cost you paid, so margins are true.",
              ]}
            />
          </div>

          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(r) => String(r.line)}
            density="compact"
            caption="Rows read from the pasted list"
            emptyHeading="Nothing pasted"
          />
        </>
      )}
    </>
  );
}
