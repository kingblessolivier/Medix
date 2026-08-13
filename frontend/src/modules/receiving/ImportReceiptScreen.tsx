/* Import receipt — the depot's inbound.
 *
 * A full page, not a modal: this is where a consignment becomes stock,
 * and it carries three things that must be right at the same time — the
 * quantity, the batch identity, and what the goods actually cost to have
 * on the shelf.
 *
 * The cost part is the reason this screen is not just "receiving". A
 * depot's capital is not the invoice. Freight, duty and clearing are real
 * money spent acquiring the stock, and recorded beside the batch instead
 * of inside it they overstate every margin computed afterwards — quietly,
 * because each line still looks right on its own. The apportionment is
 * shown before posting rather than after, so the number can be argued
 * with while it is still changeable.
 *
 * See docs/30-delivery-plan.md stage 3 and docs/28 §12.2.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackagePlus, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  ApiFailure,
  api,
  type GoodsReceipt,
  type ProductRow,
  type QuantityEntry,
} from "@/lib/api";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Help } from "@/components/ui/Guidance";
import { Modal } from "@/components/ui/Modal";
import {
  Badge,
  Banner,
  Button,
  Checkbox,
  Field,
  Input,
  PageHeader,
  Select,
  StatusPill,
} from "@/components/ui";

const CURRENCY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

/** A line being entered, before anything is written. */
type Draft = {
  key: string;
  productId: string;
  productName: string;
  /** Levels this product packs in, largest first. */
  units: { code: string; name: string; factor: number }[];
  /** Counts keyed by unit code — the mixed entry. */
  counts: Record<string, string>;
  batch: string;
  expiry: string;
  manufactured: string;
  unitCost: string;
};

export function ImportReceiptScreen({ locationId }: { locationId: string | null }) {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [posted, setPosted] = useState<GoodsReceipt | null>(null);
  const [failure, setFailure] = useState("");

  const [invoice, setInvoice] = useState("");
  const [currency, setCurrency] = useState("RWF");
  const [rate, setRate] = useState("1");
  const [rateDate, setRateDate] = useState("");
  const [official, setOfficial] = useState(true);
  const [freight, setFreight] = useState("");
  const [duty, setDuty] = useState("");
  const [clearing, setClearing] = useState("");
  const [other, setOther] = useState("");

  const products = useQuery({
    queryKey: ["products", "import"],
    queryFn: () => api.products("?page_size=200"),
  });

  const charges =
    (Number(freight) || 0) +
    (Number(duty) || 0) +
    (Number(clearing) || 0) +
    (Number(other) || 0);

  /* Goods value in RWF, so the split shown here matches the one the
     server will compute. Integer maths throughout — the rate is scaled
     by 10,000 precisely so no float touches money. */
  const rateScaled = Math.round((Number(rate) || 1) * 10_000);
  const valueOf = (d: Draft) => {
    const base = d.units.reduce(
      (sum, u) => sum + (Number(d.counts[u.code]) || 0) * u.factor,
      0,
    );
    const cost = Number(d.unitCost) || 0;
    const rwf = currency === "RWF" ? cost : Math.floor((cost * rateScaled) / 10_000);
    return { base, value: rwf * base };
  };

  const totalValue = drafts.reduce((sum, d) => sum + valueOf(d).value, 0);

  /* Shown before posting, not after. Largest-share-first remainder, the
     same rule Money.allocate uses, so this preview and the posted figure
     agree to the franc. */
  const shares = (() => {
    if (charges <= 0 || drafts.length === 0) return drafts.map(() => 0);
    const weights = drafts.map((d) =>
      totalValue > 0 ? valueOf(d).value : valueOf(d).base,
    );
    const total = weights.reduce((a, b) => a + b, 0);
    if (total === 0) return drafts.map(() => 0);
    const out = weights.map((w) => Math.floor((charges * w) / total));
    let remainder = charges - out.reduce((a, b) => a + b, 0);
    const order = weights
      .map((w, i) => [w, i] as const)
      .sort((a, b) => b[0] - a[0] || a[1] - b[1]);
    for (let i = 0; remainder > 0; i++, remainder--) {
      out[order[i % order.length][1]] += 1;
    }
    return out;
  })();

  function addDraft(product: ProductRow) {
    setDrafts((prev) => [
      ...prev,
      {
        key: `${product.id}-${prev.length}`,
        productId: product.id,
        productName: product.name,
        units: [],
        counts: {},
        batch: "",
        expiry: "",
        manufactured: "",
        unitCost: "",
      },
    ]);
  }

  const post = useMutation({
    mutationFn: async () => {
      const receipt = await api.startReceipt({ location: locationId! });
      await api.setLandedCost(receipt.id, {
        invoice_number: invoice,
        invoice_currency: currency,
        fx_rate_scaled: rateScaled,
        fx_rate_date: rateDate || null,
        fx_rate_is_official: official,
        freight: Number(freight) || 0,
        customs_duty: Number(duty) || 0,
        clearing_fees: Number(clearing) || 0,
        other_charges: Number(other) || 0,
      });
      for (const d of drafts) {
        const entries: QuantityEntry[] = d.units
          .filter((u) => Number(d.counts[u.code]) > 0)
          .map((u) => ({ uom_code: u.code, count: Number(d.counts[u.code]) }));
        if (entries.length === 0) continue;
        // The line is stated in the smallest unit entered, so a mixed
        // count always resolves to a whole number of it.
        const smallest = d.units
          .filter((u) => Number(d.counts[u.code]) > 0)
          .reduce((a, b) => (a.factor <= b.factor ? a : b));
        await api.addReceiptLine(receipt.id, {
          product: d.productId,
          uom_code: smallest.code,
          entries,
          batch_number: d.batch.trim(),
          expiry_date: d.expiry,
          unit_cost_base: Number(d.unitCost) || 0,
        });
      }
      return api.postReceipt(receipt.id);
    },
    onSuccess: (receipt) => {
      setPosted(receipt);
      setFailure("");
      queryClient.invalidateQueries({ queryKey: ["stock"] });
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Couldn't post receipt."),
  });

  if (posted) {
    return <Posted receipt={posted} onDone={() => { setPosted(null); setDrafts([]); }} />;
  }

  const incomplete = drafts.filter(
    (d) =>
      !d.batch.trim() ||
      !d.expiry ||
      d.units.every((u) => !Number(d.counts[u.code])),
  ).length;
  const ready = drafts.length > 0 && incomplete === 0 && Boolean(locationId);

  return (
    <>
      <PageHeader
        title="Import receipt"
        description="Record a consignment into the depot"
        actions={
          <Button
            variant="primary"
            icon={<PackagePlus size={16} strokeWidth={1.9} aria-hidden />}
            loading={post.isPending}
            disabled={!ready}
            onClick={() => post.mutate()}
          >
            Post receipt
          </Button>
        }
      />

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}
      {incomplete > 0 && (
        <Banner tone="warn" className="mb-4">
          {incomplete} line{incomplete === 1 ? "" : "s"} missing quantity, batch or expiry
        </Banner>
      )}

      <section className="mb-5 rounded-lg border border-border bg-surface p-4">
        <h2 className="mb-3 text-section font-semibold">Invoice</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Field label="Invoice number">
            {(id) => (
              <Input id={id} value={invoice} onChange={(e) => setInvoice(e.target.value)} />
            )}
          </Field>
          <Field label="Currency">
            {(id) => (
              <Select id={id} value={currency} onChange={(e) => setCurrency(e.target.value)}>
                {["RWF", "USD", "EUR", "GBP", "KES", "INR"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Rate to RWF" help={currency === "RWF" ? "Not applicable" : undefined}>
            {(id) => (
              <Input
                id={id}
                type="number"
                step="0.0001"
                value={rate}
                disabled={currency === "RWF"}
                onChange={(e) => setRate(e.target.value)}
                className="tabular text-right"
              />
            )}
          </Field>
          <Field label="Rate date">
            {(id) => (
              <Input
                id={id}
                type="date"
                value={rateDate}
                disabled={currency === "RWF"}
                onChange={(e) => setRateDate(e.target.value)}
              />
            )}
          </Field>
          <Field label="Rate source">
            {(id) => (
              <Select
                id={id}
                value={official ? "official" : "indicative"}
                disabled={currency === "RWF"}
                onChange={(e) => setOfficial(e.target.value === "official")}
              >
                <option value="official">Official</option>
                <option value="indicative">Indicative</option>
              </Select>
            )}
          </Field>
        </div>
      </section>

      <section className="mb-5 rounded-lg border border-border bg-surface p-4">
        <h2 className="text-section font-semibold">Landed charges</h2>
        <p className="mb-3 mt-0.5 text-help text-text-2">
          Billed in RWF. Apportioned into batch cost by value when posted.
        </p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {(
            [
              ["Freight", freight, setFreight],
              ["Customs duty", duty, setDuty],
              ["Clearing", clearing, setClearing],
              ["Other", other, setOther],
            ] as const
          ).map(([label, value, setter]) => (
            <Field key={label} label={label}>
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={0}
                  value={value}
                  onChange={(e) => setter(e.target.value)}
                  className="tabular text-right"
                />
              )}
            </Field>
          ))}
          <div>
            <p className="text-label font-medium text-text-2">Total</p>
            <p className="tabular mt-1.5 text-metric font-semibold">
              {CURRENCY.format(charges)}
            </p>
          </div>
        </div>
      </section>

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <div className="w-full max-w-sm">
          <Field label="Add product">
            {(id) => (
              <Select
                id={id}
                value=""
                onChange={(e) => {
                  const product = products.data?.results.find((p) => p.id === e.target.value);
                  if (product) addDraft(product);
                }}
              >
                <option value="">Choose a product…</option>
                {(products.data?.results ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
      </div>

      {drafts.length === 0 ? (
        <div className="rounded-lg border border-border bg-surface px-6 py-14 text-center">
          <p className="text-section font-semibold">No lines</p>
          <p className="mt-1 text-body text-text-2">Choose a product to begin.</p>
        </div>
      ) : (
        <LineGrid
          drafts={drafts}
          shares={shares}
          onChange={setDrafts}
          valueOf={valueOf}
        />
      )}

      <p className="mt-3 text-help text-text-2">
        Posting creates batches and moves stock. It cannot be undone.
      </p>
    </>
  );
}

/* The entry grid. Quantity is entered per packaging level rather than as
   one number: a clerk counts "two cartons and five packs", and making
   them do the multiplication is where the errors come from. */
function LineGrid({
  drafts,
  shares,
  onChange,
  valueOf,
}: {
  drafts: Draft[];
  shares: number[];
  onChange: (next: Draft[]) => void;
  valueOf: (d: Draft) => { base: number; value: number };
}) {
  const units = useQuery({
    queryKey: ["import-units", drafts.map((d) => d.productId).join(",")],
    queryFn: async () => {
      const map: Record<string, { code: string; name: string; factor: number }[]> = {};
      for (const id of new Set(drafts.map((d) => d.productId))) {
        const detail = await api.product(id);
        map[id] = [...detail.units]
          .sort((a, b) => b.factor_to_base - a.factor_to_base)
          .map((u) => ({ code: u.code, name: u.name, factor: u.factor_to_base }));
      }
      return map;
    },
    enabled: drafts.length > 0,
  });

  const resolved = drafts.map((d) => ({
    ...d,
    units: d.units.length ? d.units : (units.data?.[d.productId] ?? []),
  }));

  const patch = (key: string, changes: Partial<Draft>) =>
    onChange(resolved.map((d) => (d.key === key ? { ...d, ...changes } : d)));

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-border bg-content">
            <Th>Product</Th>
            <Th>Quantity</Th>
            <Th>Batch</Th>
            <Th>Expiry</Th>
            <Th numeric>Unit cost</Th>
            <Th numeric>
              <Help term="Landed share">
                This line's share of freight, duty and clearing. It goes into the
                batch cost, so margins are true.
              </Help>
            </Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {resolved.map((d, index) => (
            <tr key={d.key} className="border-b border-hair last:border-0 align-top">
              <td className="px-3 py-2.5 text-body">{d.productName}</td>
              <td className="px-3 py-2.5">
                <div className="flex flex-wrap gap-1.5">
                  {d.units.map((u) => (
                    <div key={u.code} className="w-24">
                      <Input
                        type="number"
                        min={0}
                        placeholder="0"
                        aria-label={`${u.name}, ${d.productName}`}
                        value={d.counts[u.code] ?? ""}
                        onChange={(e) =>
                          patch(d.key, { counts: { ...d.counts, [u.code]: e.target.value } })
                        }
                        className="tabular text-right"
                      />
                      <p className="mt-0.5 text-help text-text-3">{u.code.toLowerCase()}</p>
                    </div>
                  ))}
                </div>
                <p className="mt-1 text-help text-text-2">
                  {valueOf(d).base.toLocaleString()} base units
                </p>
              </td>
              <td className="px-3 py-2.5">
                <Input
                  placeholder="Batch number"
                  aria-label={`Batch number, ${d.productName}`}
                  value={d.batch}
                  onChange={(e) => patch(d.key, { batch: e.target.value })}
                  invalid={!d.batch.trim()}
                  className="w-28 font-mono"
                />
              </td>
              <td className="px-3 py-2.5">
                <Input
                  type="date"
                  aria-label={`Expiry, ${d.productName}`}
                  value={d.expiry}
                  onChange={(e) => patch(d.key, { expiry: e.target.value })}
                  invalid={!d.expiry}
                  className="w-36"
                />
              </td>
              <td className="px-3 py-2.5">
                <Input
                  type="number"
                  min={0}
                  aria-label={`Unit cost, ${d.productName}`}
                  value={d.unitCost}
                  onChange={(e) => patch(d.key, { unitCost: e.target.value })}
                  className="tabular w-24 text-right"
                />
              </td>
              <td className="tabular px-3 py-2.5 text-right text-body">
                {CURRENCY.format(shares[index] ?? 0)}
              </td>
              <td className="px-3 py-2.5 text-right">
                <button
                  type="button"
                  aria-label={`Remove ${d.productName}`}
                  onClick={() => onChange(resolved.filter((x) => x.key !== d.key))}
                  className="rounded-sm p-1 text-text-3 transition-colors hover:bg-hover hover:text-bad-text"
                >
                  <Trash2 size={15} strokeWidth={1.8} aria-hidden />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Posted({ receipt, onDone }: { receipt: GoodsReceipt; onDone: () => void }) {
  const columns: Column<GoodsReceipt["lines"][number]>[] = [
    { key: "product", header: "Product", render: (l) => l.product_name },
    {
      key: "qty",
      header: "Received",
      numeric: true,
      render: (l) => `${l.received} ${l.uom_code.toLowerCase()}`,
    },
    { key: "batch", header: "Batch", mono: true, render: (l) => l.batch_number },
    { key: "expiry", header: "Expiry", render: (l) => l.expiry_date },
    {
      key: "landed",
      header: "Landed share",
      numeric: true,
      render: (l) => CURRENCY.format(l.landed_cost_share),
    },
  ];

  return (
    <>
      <PageHeader
        title={receipt.number}
        description="Consignment received"
        actions={
          <Button variant="primary" icon={<Plus size={16} strokeWidth={2} aria-hidden />} onClick={onDone}>
            New receipt
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <StatusPill tone="ok">Posted</StatusPill>
        <span className="text-body text-text-2">
          {receipt.invoice_number || "no invoice"} · {receipt.invoice_currency}
          {receipt.invoice_currency !== "RWF" && (
            <> @ {(receipt.fx_rate_scaled / 10_000).toFixed(2)}{receipt.fx_rate_is_official ? " official" : " indicative"}</>
          )}
        </span>
        <span className="text-body text-text-2">
          charges {CURRENCY.format(receipt.landed_charges)}
        </span>
      </div>

      <DataTable
        columns={columns}
        rows={receipt.lines}
        rowKey={(l) => l.id}
        density="compact"
        caption={`Lines on ${receipt.number}`}
      />

      <Paperwork receipt={receipt} />
    </>
  );
}

/* Two of these are gates rather than filing.
 *
 * A registered medicine imported with no Certificate of Analysis is
 * quarantined on receipt, and a cold-chain log carrying a breach
 * quarantines too. Neither could be attached from anywhere in the
 * product, so a depot's imports held themselves and nothing said why. */
const IMPORT_KINDS = [
  ["CERTIFICATE_OF_ANALYSIS", "Certificate of analysis"],
  ["COLD_CHAIN_LOG", "Cold-chain temperature log"],
  ["IMPORT_LICENCE", "Import licence or permit"],
  ["COMMERCIAL_INVOICE", "Commercial invoice"],
  ["PACKING_LIST", "Packing list"],
  ["BILL_OF_LADING", "Bill of lading or air waybill"],
  ["CERTIFICATE_OF_ORIGIN", "Certificate of origin"],
  ["CUSTOMS_DECLARATION", "Customs import declaration"],
  ["PROFORMA_INVOICE", "Proforma invoice"],
] as const;

/** The two that decide whether stock can be sold. */
const GATES = new Set(["CERTIFICATE_OF_ANALYSIS", "COLD_CHAIN_LOG"]);

function Paperwork({ receipt }: { receipt: GoodsReceipt }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<string>("CERTIFICATE_OF_ANALYSIS");
  const [number, setNumber] = useState("");
  const [issuedBy, setIssuedBy] = useState("");
  const [breach, setBreach] = useState(false);
  const [failure, setFailure] = useState("");

  const documents = useQuery({
    queryKey: ["import-documents", receipt.id],
    queryFn: () => api.importDocuments(receipt.id),
  });

  const save = useMutation({
    mutationFn: () =>
      api.saveImportDocument({
        receipt: receipt.id,
        kind,
        number,
        issued_by: issuedBy,
        // Covers every batch on this receipt. A per-batch certificate
        // needs the batch id, and the receipt line carries only its
        // number — the Batch row is created at posting.
        batch: null,
        breach: kind === "COLD_CHAIN_LOG" ? breach : false,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["import-documents", receipt.id] });
      queryClient.invalidateQueries({ queryKey: ["stock"] });
      setAdding(false);
      setNumber("");
      setIssuedBy("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not filed."),
  });

  const verify = useMutation({
    mutationFn: (id: string) => api.verifyImportDocument(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["import-documents", receipt.id] }),
  });

  const rows = documents.data?.results ?? [];
  const hasCoA = rows.some((d) => d.kind === "CERTIFICATE_OF_ANALYSIS");

  return (
    <section className="mt-8">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="text-section font-semibold text-text">Paperwork</h2>
        <Button variant="secondary" onClick={() => setAdding(true)}>
          Attach document
        </Button>
      </div>

      {!hasCoA && (
        <Banner tone="warn" className="mb-3">
          No certificate of analysis. Registered medicines stay held.
        </Banner>
      )}

      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}

      {rows.length === 0 ? (
        <p className="text-body text-text-2">Nothing filed.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-hair border-y border-hair">
          {rows.map((document) => (
            <li
              key={document.id}
              className="flex items-baseline justify-between gap-3 py-2"
            >
              <span className="min-w-0">
                <span className="block truncate text-body text-text">
                  {document.kind_label}
                  {GATES.has(document.kind) && (
                    <span className="ml-2 text-help text-text-3">releases stock</span>
                  )}
                </span>
                <span className="block truncate font-mono text-help text-text-3">
                  {document.number || "no number"}
                  {document.batch_number && ` · ${document.batch_number}`}
                </span>
              </span>
              <span className="flex items-center gap-2">
                {document.breach && <Badge tone="bad">Breach</Badge>}
                {document.is_verified ? (
                  <Badge tone="ok">Verified</Badge>
                ) : (
                  <Button variant="secondary" onClick={() => verify.mutate(document.id)}>
                    Verify
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <Modal
          open
          title="Attach document"
          subtitle={receipt.number}
          onClose={() => setAdding(false)}
          footer={
            <Button
              variant="primary"
              className="w-full"
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              Attach
            </Button>
          }
        >
          <div className="flex flex-col gap-4">
            <Field label="Document" required>
              {(id) => (
                <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
                  {IMPORT_KINDS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            {kind === "CERTIFICATE_OF_ANALYSIS" && (
              <Banner tone="info">
                Releases every batch on this receipt.
              </Banner>
            )}

            <Field label="Number">
              {(id) => (
                <Input
                  id={id}
                  value={number}
                  onChange={(e) => setNumber(e.target.value)}
                />
              )}
            </Field>
            <Field label="Issued by">
              {(id) => (
                <Input
                  id={id}
                  value={issuedBy}
                  onChange={(e) => setIssuedBy(e.target.value)}
                />
              )}
            </Field>

            {kind === "COLD_CHAIN_LOG" && (
              <>
                {/* Recorded, not warned about: by the time anyone reads a
                    warning the product is already damaged. */}
                <Checkbox
                  checked={breach}
                  onChange={setBreach}
                  label="Shows a breach"
                />
                {breach && (
                  <Banner tone="bad">
                    Holds the whole consignment. Nothing ships until reviewed.
                  </Banner>
                )}
              </>
            )}
          </div>
        </Modal>
      )}
    </section>
  );
}

function Th({ children, numeric }: { children?: React.ReactNode; numeric?: boolean }) {
  return (
    <th
      className={
        "whitespace-nowrap px-3 py-2 text-label font-semibold text-text-2 " +
        (numeric ? "text-right" : "text-left")
      }
    >
      {children}
    </th>
  );
}
