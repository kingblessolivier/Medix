/* Orders.
 *
 * One screen, two sides. A retail pharmacy sees the orders it placed; a
 * wholesale pharmacy sees the orders placed with it and can confirm them.
 * Which side you get is decided by held licences, not by a role name.
 *
 * See docs/05-modules.md §3 and ADR-006.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleCheck, List, PackageCheck, Send, Truck } from "lucide-react";
import { useEffect, useState } from "react";

import {
  ApiFailure,
  api,
  type Alert,
  type OrderLine,
  type PurchaseOrder,
} from "@/lib/api";
import { DocumentChips } from "@/components/data/DocumentChips";
import {
  DataTable,
  DataToolbar,
  TableTabs,
  type Column,
  type Density,
  type RowAction,
  type TableTab,
} from "@/components/data/DataTable";
import {
  Banner,
  Button,
  ErrorState,
  Field,
  Input,
  PageHeader,
  StatusDot,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import { AlertStack } from "@/components/ui/AlertStack";
import { Consequence, NextAction } from "@/components/ui/Guidance";
import { OrderTimeline } from "./OrderTimeline";

const CURRENCY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });

const STATUS: Record<string, { tone: Tone; label: string }> = {
  DRAFT: { tone: "neutral", label: "Draft" },
  /* The buyer's own approval. Named for who is holding it rather than
     for the state: "pending" tells a pharmacist nothing about who to
     go and ask. */
  PENDING_APPROVAL: { tone: "warn", label: "With the owner" },
  REJECTED: { tone: "bad", label: "Sent back" },
  SUBMITTED: { tone: "warn", label: "Awaiting confirmation" },
  CONFIRMED: { tone: "info", label: "Confirmed" },
  PARTIALLY_DISPATCHED: { tone: "warn", label: "Part shipped" },
  DISPATCHED: { tone: "info", label: "Shipped" },
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

/* Saved views over the same list. The counts are the reason to click, so
   they are on the tab rather than discovered after it. */
const VIEWS: { id: string; label: string; icon: typeof List; match?: string[] }[] = [
  { id: "all", label: "All orders", icon: List },
  { id: "open", label: "Awaiting", icon: Truck, match: ["DRAFT", "SUBMITTED"] },
  {
    id: "confirmed",
    label: "To ship",
    icon: CircleCheck,
    match: ["CONFIRMED", "PARTIALLY_DISPATCHED"],
  },
  { id: "shipped", label: "Shipped", icon: Truck, match: ["DISPATCHED"] },
  { id: "closed", label: "Received", icon: Check, match: ["RECEIVED", "PARTIALLY_RECEIVED"] },
];

export function OrdersScreen({
  canSupply,
  locationId,
  organizationId,
}: {
  canSupply: boolean;
  locationId: string | null;
  /** The reader's own organization, so their side of the timeline reads
      as "You" rather than as the counterparty. */
  organizationId?: string;
}) {
  const queryClient = useQueryClient();
  const [side, setSide] = useState<"placed" | "received">(canSupply ? "received" : "placed");
  const [density] = useState<Density>("compact");
  const [selected, setSelected] = useState<PurchaseOrder | null>(null);
  const [view, setView] = useState("all");
  const [filter, setFilter] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [rejecting, setRejecting] = useState<PurchaseOrder | null>(null);
  const [failure, setFailure] = useState("");

  const orders = useQuery({
    queryKey: ["orders", side],
    queryFn: () => (side === "received" ? api.fulfilment() : api.orders()),
  });

  /* Warnings the depot has been shown and accepted for this order. Held
     per confirm attempt rather than for the session: accepting a credit
     warning on one order must not silently accept it on the next. */
  const [pending, setPending] = useState<Alert[]>([]);
  const [accepted, setAccepted] = useState<string[]>([]);

  const confirm = useMutation({
    mutationFn: (id: string) => api.confirmOrder(id, { acknowledged: accepted }),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      setPending([]);
      setAccepted([]);
      // Refresh the drawer only if it is already open on this order.
      // Assigning unconditionally made a bulk confirm throw the last
      // order's drawer in the user's face.
      setSelected((open) => (open && open.id === updated.id ? updated : open));
    },
    onError: (error) => {
      // A refused confirm names what has to be accepted. Show those
      // rather than the generic message — "acknowledgement required"
      // tells the depot nothing about which customer or which limit.
      if (error instanceof ApiFailure) {
        const raised = (error.error.meta?.alerts as Alert[] | undefined) ?? [];
        setPending(raised);
        if (raised.length === 0) setFailure(error.error.message);
      }
    },
  });

  const sendForApproval = useMutation({
    mutationFn: (id: string) => api.requestApproval(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["orders"] }),
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Couldn't send for approval.",
      ),
  });

  const reject = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.rejectOrder(id, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      setRejecting(null);
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Couldn't send back."),
  });

  const submit = useMutation({
    mutationFn: (id: string) => api.submitOrder(id),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      // Refresh the drawer only if it is already open on this order.
      // Assigning unconditionally made a bulk confirm throw the last
      // order's drawer in the user's face.
      setSelected((open) => (open && open.id === updated.id ? updated : open));
    },
  });

  // A supplier never sees the buyer's unsent drafts.
  const visible = (orders.data?.results ?? []).filter((o) =>
    side === "placed" ? true : o.status !== "DRAFT",
  );

  const counts = Object.fromEntries(
    VIEWS.map((v) => [
      v.id,
      v.match ? visible.filter((o) => v.match!.includes(o.status)).length : visible.length,
    ]),
  );
  const tabs: TableTab[] = VIEWS.map((v) => ({
    id: v.id,
    label: v.label,
    icon: v.icon,
    count: counts[v.id],
  }));

  const term = filter.trim().toLowerCase();
  const chosenView = VIEWS.find((v) => v.id === view);
  const dispatch = useMutation({
    mutationFn: (id: string) => api.dispatchOrder(id, { from_location: locationId! }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["orders"] }),
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Couldn't dispatch."),
  });

  const rows = visible
    .filter((o) => !chosenView?.match || chosenView.match.includes(o.status))
    .filter(
      (o) =>
        !term ||
        o.number.toLowerCase().includes(term) ||
        (side === "received" ? o.buyer_name : o.supplier_name).toLowerCase().includes(term),
    );

  /* Confirming a stack of orders one drawer at a time is the tedium this
     removes. Only the supplier may confirm, and only what is submitted. */
  const confirmable = (o: PurchaseOrder) => side === "received" && o.status === "SUBMITTED";

  /* Dispatch is what takes the goods out of the supplier's ledger.
     Without it the buyer gains stock that never left here. */
  const shippable = (o: PurchaseOrder) =>
    side === "received" && ["CONFIRMED", "PARTIALLY_DISPATCHED"].includes(o.status);

  const rowActions: RowAction<PurchaseOrder>[] = [
    { label: "Open", onSelect: setSelected },
    ...(side === "received"
      ? [
          {
            label: "Confirm order",
            onSelect: (o: PurchaseOrder) => confirm.mutate(o.id),
            disabled: (o: PurchaseOrder) => !confirmable(o),
          },
        ]
      : [
          /* Two steps, because they are two people. A pharmacist raises
             the order and sends it; somebody who can commit the money
             releases it to the depot. */
          {
            label: "Send for approval",
            onSelect: (o: PurchaseOrder) => sendForApproval.mutate(o.id),
            disabled: (o: PurchaseOrder) => o.status !== "DRAFT",
          },
          {
            label: "Approve and send",
            onSelect: (o: PurchaseOrder) => submit.mutate(o.id),
            disabled: (o: PurchaseOrder) => o.status !== "PENDING_APPROVAL",
          },
          {
            label: "Send back",
            onSelect: (o: PurchaseOrder) => setRejecting(o),
            disabled: (o: PurchaseOrder) => o.status !== "PENDING_APPROVAL",
            danger: true,
          },
        ]),
    ...(side === "received"
      ? [
          {
            label: "Dispatch",
            onSelect: (o: PurchaseOrder) => dispatch.mutate(o.id),
            disabled: (o: PurchaseOrder) => !shippable(o) || !locationId,
          },
        ]
      : []),
  ];

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
        return <StatusPill tone={s.tone}>{s.label}</StatusPill>;
      },
    },
    {
      key: "documents",
      header: "Documents",
      width: "11rem",
      render: (o) => <DocumentChips subject={o.id} label={o.number || "Draft"} />,
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
          <Metric label="Total orders" value={visible.length} />
        </div>
      )}

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      <WhatToDoNow
        orders={visible}
        side={side}
        onSend={(o) => sendForApproval.mutate(o.id)}
        onApprove={(o) => submit.mutate(o.id)}
        onConfirm={(o) => confirm.mutate(o.id)}
      />

      <TableTabs tabs={tabs} active={view} onChange={setView} />

      <DataToolbar
        search={filter}
        onSearch={setFilter}
        searchPlaceholder="Filter orders"
      />

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(o) => o.id}
        density={density}
        loading={orders.isLoading}
        caption="Purchase orders"
        onRowClick={setSelected}
        selectable
        selected={checked}
        onSelectionChange={setChecked}
        rowLabel={(o) => `Select order ${o.number || "draft"}`}
        rowActions={rowActions}
        bulkActions={(picked) => {
          const ready = picked.filter(confirmable);
          return (
            <Button
              variant="primary"
              icon={<Check size={15} strokeWidth={2} aria-hidden />}
              disabled={ready.length === 0 || confirm.isPending}
              onClick={() => {
                ready.forEach((o) => confirm.mutate(o.id));
                setChecked(new Set());
              }}
            >
              Confirm {ready.length}
            </Button>
          );
        }}
        emptyHeading={side === "received" ? "No orders yet" : "No orders"}
        emptyBody={
          side === "received"
            ? "Orders from pharmacies appear here."
            : undefined
        }
      />

      <OrderModal
        order={selected}
        side={side}
        alerts={pending}
        onAcknowledge={(alert) => setAccepted((codes) => [...codes, alert.code])}
        acknowledged={accepted}
        onClose={() => {
          setSelected(null);
          setPending([]);
          setAccepted([]);
        }}
        onConfirm={() => selected && confirm.mutate(selected.id)}
        onSubmit={() => selected && submit.mutate(selected.id)}
        onDispatch={() => selected && dispatch.mutate(selected.id)}
        busy={confirm.isPending || submit.isPending || dispatch.isPending}
        viewerOrganization={organizationId}
      />

      <SendBackModal
        order={rejecting}
        busy={reject.isPending}
        onClose={() => setRejecting(null)}
        onSend={(reason) =>
          rejecting && reject.mutate({ id: rejecting.id, reason })
        }
      />
    </>
  );
}

/* Sending an order back needs a reason the raiser can act on.
 *
 * Required rather than optional: an order returned with no explanation is
 * an argument the pharmacist cannot answer, and they will simply send the
 * same order again. */
function SendBackModal({
  order,
  busy,
  onClose,
  onSend,
}: {
  order: PurchaseOrder | null;
  busy: boolean;
  onClose: () => void;
  onSend: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");

  useEffect(() => setReason(""), [order?.id]);
  if (!order) return null;

  return (
    <Modal
      open
      title="Send back"
      subtitle={order.supplier_name}
      onClose={onClose}
      footer={
        <div className="flex gap-2">
          <Button variant="secondary" className="flex-1" onClick={onClose}>
            Keep waiting
          </Button>
          <Button
            variant="primary"
            className="flex-1"
            loading={busy}
            disabled={!reason.trim()}
            onClick={() => onSend(reason)}
          >
            Send back
          </Button>
        </div>
      }
    >
      <Consequence
        lines={[
          "Returns the order to the pharmacist who raised it.",
          "The depot never sees it. Nothing is ordered.",
        ]}
      />
      <div className="mt-4">
        <Field label="Reason" required>
          {(id) => (
            <Input
              id={id}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Too much stock"
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}

/* A status says where the order is. It does not say what is now expected
 * of the person reading it, and "Approved" is not an instruction.
 *
 * One line, one button, and only when there is something to do. A banner
 * that is always present is a banner nobody reads. */
function WhatToDoNow({
  orders,
  side,
  onSend,
  onApprove,
  onConfirm,
}: {
  orders: PurchaseOrder[];
  side: "placed" | "received";
  onSend: (order: PurchaseOrder) => void;
  onApprove: (order: PurchaseOrder) => void;
  onConfirm: (order: PurchaseOrder) => void;
}) {
  if (side === "received") {
    const waiting = orders.filter((o) => o.status === "SUBMITTED");
    if (waiting.length === 0) return null;
    return (
      <NextAction
        heading={`Confirm ${waiting.length} order${waiting.length === 1 ? "" : "s"}`}
        detail="A pharmacy is waiting to hear whether you can supply."
        action={
          <Button variant="primary" onClick={() => onConfirm(waiting[0])}>
            Confirm oldest
          </Button>
        }
      />
    );
  }

  const pending = orders.filter((o) => o.status === "PENDING_APPROVAL");
  if (pending.length > 0) {
    return (
      <NextAction
        heading={`Approve ${pending.length} order${pending.length === 1 ? "" : "s"}`}
        detail="Nothing is sent to the depot until you release it."
        action={
          <Button variant="primary" onClick={() => onApprove(pending[0])}>
            Open oldest
          </Button>
        }
      />
    );
  }

  const drafts = orders.filter((o) => o.status === "DRAFT" && o.lines.length > 0);
  if (drafts.length > 0) {
    return (
      <NextAction
        heading={`Send ${drafts.length} draft${drafts.length === 1 ? "" : "s"} for approval`}
        detail="A draft sits here until somebody releases it."
        action={
          <Button variant="primary" onClick={() => onSend(drafts[0])}>
            Send oldest
          </Button>
        }
      />
    );
  }

  const shipped = orders.filter((o) => o.status === "DISPATCHED");
  if (shipped.length > 0) {
    return (
      <NextAction
        tone="waiting"
        heading={`${shipped.length} on the way`}
        detail="Receive them under Deliveries when they arrive."
      />
    );
  }
  return null;
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

function OrderModal({
  order,
  side,
  onClose,
  onConfirm,
  onSubmit,
  onDispatch,
  busy,
  viewerOrganization,
  alerts,
  acknowledged,
  onAcknowledge,
}: {
  order: PurchaseOrder | null;
  side: "placed" | "received";
  onClose: () => void;
  onConfirm: () => void;
  onSubmit: () => void;
  onDispatch: () => void;
  busy: boolean;
  viewerOrganization?: string;
  alerts: Alert[];
  acknowledged: string[];
  onAcknowledge: (alert: Alert) => void;
}) {
  if (!order) return null;
  const status = STATUS[order.status] ?? STATUS.DRAFT;

  /* An accepted warning drops off the stack, so what is left is exactly
     what still stands between the depot and confirming. */
  const outstanding = alerts.filter((a) => !acknowledged.includes(a.code));

  // Only the supplier confirms; only the buyer submits. Enforced in the
  // service too — this just avoids offering an action that will refuse.
  const action =
    side === "received" &&
    ["CONFIRMED", "PARTIALLY_DISPATCHED"].includes(order.status) ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<PackageCheck size={16} strokeWidth={1.9} aria-hidden />}
        loading={busy}
        onClick={onDispatch}
      >
        Dispatch order
      </Button>
    ) : side === "received" && order.status === "SUBMITTED" ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<Check size={16} strokeWidth={1.9} aria-hidden />}
        loading={busy}
        onClick={onConfirm}
      >
        Confirm order
      </Button>
    ) : side === "placed" && order.status === "DRAFT" ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<Send size={16} strokeWidth={1.9} aria-hidden />}
        loading={busy}
        onClick={onSubmit}
      >
        Submit order
      </Button>
    ) : undefined;

  return (
    <Modal
      open
      title={order.number || "Draft order"}
      subtitle={side === "received" ? order.buyer_name : order.supplier_name}
      onClose={onClose}
      footer={action}
    >
      <div className="mb-4">
        <StatusDot tone={status.tone}>{status.label}</StatusDot>
      </div>

      {/* Above the control they block, never floating. */}
      <AlertStack
        alerts={outstanding}
        onAcknowledge={onAcknowledge}
        className="mb-4"
      />

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

      <h3 className="mb-3 mt-6 text-section font-semibold">History</h3>
      <OrderTimeline
        events={order.events ?? []}
        viewerOrganization={viewerOrganization}
      />
    </Modal>
  );
}
