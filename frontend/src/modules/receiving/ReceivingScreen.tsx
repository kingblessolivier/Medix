/* Receiving.
 *
 * A full page, not a drawer: this is the workflow where goods become
 * stock, and it is the only place batch number and expiry enter the
 * system. Get them wrong and recall traceability is gone — so both are
 * required per line, and nothing is written until the receipt is posted.
 *
 * Short delivery is normal, not an error. The document records what
 * actually arrived against what was ordered, and the difference is the
 * reason the document exists.
 *
 * See docs/05-modules.md §4 and docs/19-screens.md.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackageCheck, ScanLine, Snowflake } from "lucide-react";
import { useEffect, useState } from "react";

import {
  ApiFailure,
  api,
  type Discrepancy,
  type GoodsReceipt,
  type OrderLine,
  type PurchaseOrder,
} from "@/lib/api";
import { DataTable, type Column, type Density } from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  ErrorState,
  Input,
  PageHeader,
  StatusDot,
  StatusPill,
} from "@/components/ui";

const CURRENCY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });

/** What the receiver types per order line before anything is written. */
type Entry = {
  received: string;
  rejected: string;
  reason: string;
  batch: string;
  expiry: string;
};

type Row = { line: OrderLine; entry: Entry };

export function ReceivingScreen({ locationId }: { locationId: string | null }) {
  const [order, setOrder] = useState<PurchaseOrder | null>(null);

  if (order) {
    return (
      <ReceiveAgainstOrder order={order} locationId={locationId} onDone={() => setOrder(null)} />
    );
  }
  return <AwaitingDelivery onReceive={setOrder} />;
}

/* Step one: which delivery arrived. Only confirmed orders can be
   received — an order the supplier has not accepted is not on its way. */
function AwaitingDelivery({ onReceive }: { onReceive: (order: PurchaseOrder) => void }) {
  const [density] = useState<Density>("compact");
  const orders = useQuery({ queryKey: ["orders", "placed"], queryFn: () => api.orders() });

  const rows = (orders.data?.results ?? []).filter(
    (o) => o.status === "CONFIRMED" || o.status === "PARTIALLY_RECEIVED",
  );

  const columns: Column<PurchaseOrder>[] = [
    {
      key: "number",
      header: "Order",
      mono: true,
      width: "13rem",
      render: (o) => o.number,
      sortable: true,
      sortValue: (o) => o.number,
    },
    { key: "supplier", header: "Supplier", render: (o) => o.supplier_name },
    {
      key: "lines",
      header: "Lines",
      numeric: true,
      width: "5rem",
      render: (o) => o.lines.length.toLocaleString(),
    },
    {
      key: "value",
      header: "Value",
      numeric: true,
      width: "9rem",
      sortable: true,
      render: (o) => CURRENCY.format(o.subtotal),
      sortValue: (o) => o.subtotal,
    },
    {
      key: "confirmed",
      header: "Confirmed",
      width: "8rem",
      render: (o) => (o.confirmed_at ? DAY.format(new Date(o.confirmed_at)) : "—"),
      sortable: true,
      sortValue: (o) => o.confirmed_at ?? "",
    },
    {
      key: "status",
      header: "Status",
      width: "11rem",
      render: (o) =>
        o.status === "PARTIALLY_RECEIVED" ? (
          <StatusPill tone="warn">Part received</StatusPill>
        ) : (
          <StatusPill tone="info">Confirmed</StatusPill>
        ),
    },
  ];

  if (orders.isError) {
    return (
      <>
        <PageHeader title="Receiving" />
        <ErrorState message="Couldn't load orders." onRetry={() => orders.refetch()} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Receiving" description="Deliveries against confirmed orders" />
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(o) => o.id}
        density={density}
        loading={orders.isLoading}
        caption="Orders awaiting delivery"
        onRowClick={onReceive}
        emptyHeading="Nothing to receive"
        emptyBody="Confirmed orders appear here."
      />
    </>
  );
}

/* Step two: what actually arrived. Nothing is written until Post. */
function ReceiveAgainstOrder({
  order,
  locationId,
  onDone,
}: {
  order: PurchaseOrder;
  locationId: string | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [entries, setEntries] = useState<Record<string, Entry>>(() =>
    Object.fromEntries(
      order.lines.map((l) => [
        l.id,
        // Pre-filled with what is still outstanding: delivery in full is
        // the common case, and the receiver should be correcting rather
        // than transcribing.
        { received: String(outstanding(l)), rejected: "", reason: "", batch: "", expiry: "" },
      ]),
    ),
  );
  const [posted, setPosted] = useState<GoodsReceipt | null>(null);
  const [failure, setFailure] = useState("");

  const update = (lineId: string, patch: Partial<Entry>) =>
    setEntries((prev) => ({ ...prev, [lineId]: { ...prev[lineId], ...patch } }));

  /* The supplier's advance notice seeds a draft receipt at dispatch.
     Using it rather than opening a second one is what keeps one delivery
     to one receipt — two drafts against one delivery is how stock gets
     received twice. */
  const seeded = useQuery({
    queryKey: ["draft-receipt", order.id],
    queryFn: () => api.draftReceiptFor(order.id),
  });
  const draft = seeded.data?.results?.[0] ?? null;

  /* Batch and expiry are the two fields the receiver would otherwise
     copy off the cartons by hand, and the two most worth getting from
     the supplier. Applied once, when the draft arrives — after that the
     receiver's edits own the field. */
  const [seededFrom, setSeededFrom] = useState<string | null>(null);
  useEffect(() => {
    if (!draft || seededFrom === draft.id) return;
    setEntries((prev) => {
      const next = { ...prev };
      for (const seededLine of draft.lines ?? []) {
        if (!seededLine.order_line || !next[seededLine.order_line]) continue;
        next[seededLine.order_line] = {
          ...next[seededLine.order_line],
          received: String(seededLine.received),
          batch: seededLine.batch_number,
          expiry: seededLine.expiry_date,
        };
      }
      return next;
    });
    setSeededFrom(draft.id);
  }, [draft, seededFrom]);

  const post = useMutation({
    mutationFn: async () => {
      const receipt =
        draft ??
        (await api.startReceipt({ location: locationId!, order: order.id }));

      // A seeded draft already carries the supplier's figures. What goes
      // on the ledger is what the receiver counted, so the seeded lines
      // are cleared and rewritten from the screen — otherwise a
      // correction would be added on top and double the quantity.
      if (draft) await api.resetReceiptLines(receipt.id);

      for (const line of order.lines) {
        const entry = entries[line.id];
        const received = Number(entry.received);
        if (!received) continue;
        await api.addReceiptLine(receipt.id, {
          product: line.product,
          uom_code: line.uom_code,
          received,
          rejected: Number(entry.rejected) || 0,
          rejection_reason: entry.reason,
          batch_number: entry.batch.trim(),
          expiry_date: entry.expiry,
          unit_cost_base: line.unit_price,
          order_line: line.id,
        });
      }
      return api.postReceipt(receipt.id);
    },
    onSuccess: (receipt) => {
      setPosted(receipt);
      setFailure("");
      // Stock moved and the order advanced — both views are now stale.
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["stock"] });
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Couldn't post receipt."),
  });

  const rows: Row[] = order.lines.map((l) => ({ line: l, entry: entries[l.id] }));
  // Batch and expiry are the traceability record. A line with a quantity
  // but no batch cannot be posted, so the page says so before the attempt.
  const incomplete = rows.filter(
    ({ entry }) => Number(entry.received) > 0 && (!entry.batch.trim() || !entry.expiry),
  ).length;
  const receiving = rows.filter(({ entry }) => Number(entry.received) > 0).length;
  const shortLines = rows.filter(
    ({ line, entry }) => Number(entry.received) < outstanding(line),
  ).length;
  const total = rows.reduce(
    (sum, { line, entry }) => sum + line.unit_price * (Number(entry.received) || 0),
    0,
  );

  if (posted) return <Posted receipt={posted} onDone={onDone} />;

  const columns: Column<Row>[] = [
    {
      key: "product",
      header: "Product",
      render: ({ line }) => <span className="text-body">{line.product_name}</span>,
    },
    {
      key: "ordered",
      header: "Ordered",
      numeric: true,
      width: "7rem",
      render: ({ line }) => (
        <span className="text-text-2">
          {outstanding(line)} {line.uom_code.toLowerCase()}
        </span>
      ),
    },
    {
      key: "received",
      header: "Received",
      numeric: true,
      width: "7rem",
      render: ({ line, entry }) => (
        <Input
          type="number"
          min={0}
          aria-label={`Received, ${line.product_name}`}
          value={entry.received}
          onChange={(e) => update(line.id, { received: e.target.value })}
          invalid={Number(entry.received) > outstanding(line)}
          className="tabular text-right"
        />
      ),
    },
    {
      key: "batch",
      header: "Batch",
      width: "10rem",
      render: ({ line, entry }) => (
        <Input
          aria-label={`Batch number, ${line.product_name}`}
          placeholder="Batch no."
          value={entry.batch}
          onChange={(e) => update(line.id, { batch: e.target.value })}
          invalid={Number(entry.received) > 0 && !entry.batch.trim()}
          className="font-mono"
        />
      ),
    },
    {
      key: "expiry",
      header: "Expiry",
      width: "10rem",
      render: ({ line, entry }) => (
        <Input
          type="date"
          aria-label={`Expiry, ${line.product_name}`}
          value={entry.expiry}
          onChange={(e) => update(line.id, { expiry: e.target.value })}
          invalid={Number(entry.received) > 0 && !entry.expiry}
        />
      ),
    },
    {
      key: "rejected",
      header: "Rejected",
      numeric: true,
      width: "6.5rem",
      render: ({ line, entry }) => (
        <Input
          type="number"
          min={0}
          aria-label={`Rejected, ${line.product_name}`}
          value={entry.rejected}
          onChange={(e) => update(line.id, { rejected: e.target.value })}
          className="tabular text-right"
        />
      ),
    },
    {
      key: "reason",
      header: "Reason",
      width: "12rem",
      render: ({ line, entry }) => (
        <Input
          aria-label={`Rejection reason, ${line.product_name}`}
          placeholder={Number(entry.rejected) > 0 ? "Required" : "—"}
          value={entry.reason}
          onChange={(e) => update(line.id, { reason: e.target.value })}
          invalid={Number(entry.rejected) > 0 && !entry.reason.trim()}
        />
      ),
    },
  ];

  const ready = receiving > 0 && incomplete === 0 && Boolean(locationId);

  return (
    <>
      <PageHeader
        title={`Receive ${order.number}`}
        description={order.supplier_name}
        actions={
          <>
            <Button variant="tertiary" onClick={onDone}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon={<PackageCheck size={16} strokeWidth={1.9} />}
              loading={post.isPending}
              disabled={!ready}
              onClick={() => post.mutate()}
            >
              Post receipt
            </Button>
          </>
        }
      />

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}
      {/* The supplier's advance notice already filled these lines in.
          Saying so matters: the receiver is confirming a count, not
          entering one, and a pre-filled figure they did not check is
          exactly what a goods receipt exists to catch. */}
      {draft?.transfer_id && (
        <Banner tone="info" className="mb-4">
          {`Pre-filled from ${draft.transfer_id}. Count against the cartons before posting.`}
        </Banner>
      )}
      {incomplete > 0 && (
        <Banner tone="warn" className="mb-4">
          {incomplete} line{incomplete === 1 ? "" : "s"} missing batch or expiry
        </Banner>
      )}

      {/* The three numbers that decide whether to post, above the grid
          rather than buried under it. */}
      <div className="mb-4 flex flex-wrap gap-x-10 gap-y-3 border-b border-hair pb-4">
        <Metric label="Lines" value={`${receiving} of ${order.lines.length}`} />
        <Metric label="Short" value={String(shortLines)} tone={shortLines ? "text-warn-text" : ""} />
        <Metric label="Received value" value={CURRENCY.format(total)} />
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={({ line }) => line.id}
        density="spacious"
        caption={`Lines on ${order.number}`}
      />

      <p className="mt-3 flex items-center gap-1.5 text-help text-text-2">
        <ScanLine size={14} strokeWidth={1.8} aria-hidden />
        Posting creates batches and moves stock. It cannot be undone.
      </p>
    </>
  );
}

/* Step three: what the delivery actually was. Discrepancies are the
   record the supplier gets asked about. */
function Posted({ receipt, onDone }: { receipt: GoodsReceipt; onDone: () => void }) {
  const discrepancies = useQuery({
    queryKey: ["discrepancies", receipt.id],
    queryFn: () => api.discrepancies(receipt.id),
  });

  const rows = discrepancies.data ?? [];

  const columns: Column<Discrepancy>[] = [
    { key: "product", header: "Product", render: (d) => d.product },
    { key: "ordered", header: "Ordered", numeric: true, width: "7rem", render: (d) => d.ordered },
    { key: "received", header: "Received", numeric: true, width: "7rem", render: (d) => d.received },
    {
      key: "short",
      header: "Short",
      numeric: true,
      width: "6rem",
      render: (d) => (d.short_by ? <span className="text-warn-text">{d.short_by}</span> : "—"),
    },
    {
      key: "rejected",
      header: "Rejected",
      numeric: true,
      width: "6.5rem",
      render: (d) => (d.rejected ? <span className="text-bad-text">{d.rejected}</span> : "—"),
    },
    {
      key: "reason",
      header: "Reason",
      render: (d) => <span className="text-text-2">{d.reason || "—"}</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title={receipt.number}
        description="Stock received"
        actions={
          <Button variant="primary" onClick={onDone}>
            Done
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <StatusDot tone="ok">Posted</StatusDot>
        {receipt.has_discrepancy && <Badge tone="warn">Discrepancy</Badge>}
        {receipt.transport_temperature_ok === false && (
          <Badge tone="bad">
            <Snowflake size={11} strokeWidth={2} className="mr-1 inline" aria-hidden />
            Cold chain
          </Badge>
        )}
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(d) => d.product}
        density="compact"
        caption="Discrepancies against the order"
        emptyHeading="Delivered in full"
        emptyBody="Nothing differed from the order."
      />
    </>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <p className="text-help text-text-2">{label}</p>
      <p className={`tabular text-metric font-semibold ${tone ?? ""}`}>{value}</p>
    </div>
  );
}

/** Still to arrive, in the order's own unit. */
function outstanding(line: OrderLine): number {
  if (line.quantity_base <= 0) return line.quantity;
  const perUnit = line.quantity_base / line.quantity;
  return Math.max(0, Math.round(line.outstanding_base / perUnit));
}
