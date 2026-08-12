/* Orders.
 *
 * One screen, two sides. A retail pharmacy sees the orders it placed; a
 * wholesale pharmacy sees the orders placed with it and can confirm them.
 * Which side you get is decided by held licences, not by a role name.
 *
 * See docs/05-modules.md §3 and ADR-006.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Send } from "lucide-react";
import { useState } from "react";

import { api, type OrderLine, type PurchaseOrder } from "@/lib/api";
import { DataTable, type Column, type Density } from "@/components/data/DataTable";
import { Button, ErrorState, PageHeader, StatusDot, type Tone } from "@/components/ui";
import { DetailList, Drawer } from "@/components/ui/Drawer";

const CURRENCY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });

const STATUS: Record<string, { tone: Tone; label: string }> = {
  DRAFT: { tone: "neutral", label: "Draft" },
  SUBMITTED: { tone: "warn", label: "Awaiting confirmation" },
  CONFIRMED: { tone: "info", label: "Confirmed" },
  PARTIALLY_RECEIVED: { tone: "warn", label: "Part received" },
  RECEIVED: { tone: "ok", label: "Received" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};

/* Received is a three-state fact — none, some, all — so it reads as a
   word rather than a number the eye has to compare. */
const LINE_COLUMNS: Column<OrderLine>[] = [
  { key: "product", header: "Product", render: (l) => l.product_name },
  {
    key: "qty",
    header: "Qty",
    numeric: true,
    render: (l) => `${l.quantity} ${l.uom_code.toLowerCase()}`,
  },
  {
    key: "received",
    header: "Received",
    numeric: true,
    render: (l) =>
      l.received_base > 0 && l.outstanding_base > 0 ? (
        <span className="text-warn-text">Part</span>
      ) : l.received_base > 0 ? (
        <span className="text-ok-text">All</span>
      ) : (
        "—"
      ),
  },
  {
    key: "value",
    header: "Value",
    numeric: true,
    render: (l) => CURRENCY.format(l.line_total),
  },
];

export function OrdersScreen({ canSupply }: { canSupply: boolean }) {
  const queryClient = useQueryClient();
  const [side, setSide] = useState<"placed" | "received">(canSupply ? "received" : "placed");
  const [density] = useState<Density>("compact");
  const [selected, setSelected] = useState<PurchaseOrder | null>(null);

  const orders = useQuery({
    queryKey: ["orders", side],
    queryFn: () => (side === "received" ? api.fulfilment() : api.orders()),
  });

  const confirm = useMutation({
    mutationFn: (id: string) => api.confirmOrder(id),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      setSelected(updated);
    },
  });

  const submit = useMutation({
    mutationFn: (id: string) => api.submitOrder(id),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      setSelected(updated);
    },
  });

  const rows = (orders.data?.results ?? []).filter((o) =>
    side === "placed" ? true : o.status !== "DRAFT",
  );

  const columns: Column<PurchaseOrder>[] = [
    {
      key: "number",
      header: "Order",
      mono: true,
      render: (o) => o.number || "draft",
      sortable: true,
      sortValue: (o) => o.number,
    },
    {
      key: "party",
      header: side === "received" ? "Pharmacy" : "Supplier",
      render: (o) => (side === "received" ? o.buyer_name : o.supplier_name),
    },
    {
      key: "value",
      header: "Value",
      numeric: true,
      sortable: true,
      render: (o) => CURRENCY.format(o.subtotal),
      sortValue: (o) => o.subtotal,
    },
    {
      key: "date",
      header: "Placed",
      render: (o) => (o.submitted_at ? DAY.format(new Date(o.submitted_at)) : "—"),
      sortable: true,
      sortValue: (o) => o.submitted_at ?? "",
    },
    {
      key: "status",
      header: "Status",
      render: (o) => {
        const s = STATUS[o.status] ?? STATUS.DRAFT;
        return <StatusDot tone={s.tone}>{s.label}</StatusDot>;
      },
    },
  ];

  if (orders.isError) {
    return (
      <>
        <PageHeader title="Orders" />
        <ErrorState message="Couldn't load orders." onRetry={() => orders.refetch()} />
      </>
    );
  }

  const awaiting = rows.filter((o) => o.status === "SUBMITTED").length;

  return (
    <>
      <PageHeader
        title="Orders"
        description={side === "received" ? "Orders placed with you" : "Orders you placed"}
        actions={
          canSupply ? (
            <SideToggle side={side} onChange={setSide} />
          ) : undefined
        }
      />

      {side === "received" && awaiting > 0 && (
        <div className="mb-4 flex flex-wrap gap-8 border-b border-hair pb-4">
          <Metric label="Awaiting confirmation" value={awaiting} tone="text-warn-text" />
          <Metric label="Total orders" value={rows.length} />
        </div>
      )}

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(o) => o.id}
        density={density}
        loading={orders.isLoading}
        caption="Purchase orders"
        onRowClick={setSelected}
        emptyHeading={side === "received" ? "No orders yet" : "No orders"}
        emptyBody={
          side === "received"
            ? "Orders from pharmacies appear here."
            : undefined
        }
      />

      <OrderDrawer
        order={selected}
        side={side}
        onClose={() => setSelected(null)}
        onConfirm={() => selected && confirm.mutate(selected.id)}
        onSubmit={() => selected && submit.mutate(selected.id)}
        busy={confirm.isPending || submit.isPending}
      />
    </>
  );
}

function SideToggle({
  side,
  onChange,
}: {
  side: "placed" | "received";
  onChange: (s: "placed" | "received") => void;
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-sm border border-border">
      {(
        [
          ["received", "Received"],
          ["placed", "Placed"],
        ] as const
      ).map(([value, label]) => (
        <button
          key={value}
          type="button"
          onClick={() => onChange(value)}
          aria-pressed={side === value}
          className={
            "px-2.5 py-1 text-help transition-colors " +
            (side === value
              ? "bg-selected font-semibold text-brand-text"
              : "text-text-2 hover:bg-hover")
          }
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div>
      <p className="text-help text-text-2">{label}</p>
      <p className={`tabular text-metric font-semibold ${tone ?? ""}`}>
        {value.toLocaleString()}
      </p>
    </div>
  );
}

function OrderDrawer({
  order,
  side,
  onClose,
  onConfirm,
  onSubmit,
  busy,
}: {
  order: PurchaseOrder | null;
  side: "placed" | "received";
  onClose: () => void;
  onConfirm: () => void;
  onSubmit: () => void;
  busy: boolean;
}) {
  if (!order) return null;
  const status = STATUS[order.status] ?? STATUS.DRAFT;

  // Only the supplier confirms; only the buyer submits. Enforced in the
  // service too — this just avoids offering an action that will refuse.
  const action =
    side === "received" && order.status === "SUBMITTED" ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<Check size={16} strokeWidth={1.9} />}
        loading={busy}
        onClick={onConfirm}
      >
        Confirm order
      </Button>
    ) : side === "placed" && order.status === "DRAFT" ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<Send size={16} strokeWidth={1.9} />}
        loading={busy}
        onClick={onSubmit}
      >
        Submit order
      </Button>
    ) : undefined;

  return (
    <Drawer
      open
      title={order.number || "Draft order"}
      subtitle={side === "received" ? order.buyer_name : order.supplier_name}
      onClose={onClose}
      footer={action}
    >
      <div className="mb-4">
        <StatusDot tone={status.tone}>{status.label}</StatusDot>
      </div>

      <DetailList
        rows={[
          ["Value", CURRENCY.format(order.subtotal)],
          ["Deliver to", order.deliver_to_name],
          ["Placed", order.submitted_at ? DAY.format(new Date(order.submitted_at)) : "—"],
          ["Confirmed", order.confirmed_at ? DAY.format(new Date(order.confirmed_at)) : "—"],
        ]}
      />

      <h3 className="mb-2 mt-6 text-section font-semibold">Items</h3>
      <DataTable
        columns={LINE_COLUMNS}
        rows={order.lines}
        rowKey={(l) => l.id}
        density="compact"
        caption={`Items on ${order.number || "this order"}`}
        emptyHeading="No items"
      />
    </Drawer>
  );
}
