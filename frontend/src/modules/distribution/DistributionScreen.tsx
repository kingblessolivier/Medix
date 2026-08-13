/* The depot's side of the counter.
 *
 * One queue per thing a picker actually does, in the order the order
 * moves: confirm it, start picking, dispatch it. Tabs rather than a
 * status column, because the question is never "show me everything" —
 * it is "what is waiting on me".
 *
 * Confirming can be refused (credit, licence) or warned about (credit
 * near its limit), so the modal carries the alert stack and the retry
 * names the codes accepted.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, PackageCheck, Truck } from "lucide-react";
import { DocumentChips } from "@/components/data/DocumentChips";
import {
  DataTable,
  TableTabs,
  type Column,
  type TableTab,
} from "@/components/data/DataTable";
import {
  Banner,
  Button,
  ErrorState,
  PageHeader,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import { AlertStack } from "@/components/ui/AlertStack";
import { OrderTimeline } from "@/modules/orders/OrderTimeline";
import {
  ApiFailure,
  api,
  type Alert,
  type OrderLine,
  type PurchaseOrder,
} from "@/lib/api";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });

const STAGE: Record<string, { tone: Tone; label: string }> = {
  SUBMITTED: { tone: "warn", label: "To confirm" },
  CONFIRMED: { tone: "info", label: "To pick" },
  PREPARING: { tone: "info", label: "Picking" },
  PARTIALLY_DISPATCHED: { tone: "warn", label: "Part shipped" },
  DISPATCHED: { tone: "ok", label: "Shipped" },
  PARTIALLY_RECEIVED: { tone: "ok", label: "Part received" },
  RECEIVED: { tone: "ok", label: "Received" },
  REJECTED: { tone: "bad", label: "Rejected" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};

/* The queues, in the order an order passes through them. */
const QUEUES: { id: string; label: string; match: string[] }[] = [
  { id: "confirm", label: "To confirm", match: ["SUBMITTED"] },
  { id: "pick", label: "To pick", match: ["CONFIRMED", "PREPARING"] },
  { id: "ship", label: "To ship", match: ["PREPARING", "PARTIALLY_DISPATCHED"] },
  { id: "sent", label: "Sent", match: ["DISPATCHED", "PARTIALLY_RECEIVED", "RECEIVED"] },
];

const LINE_COLUMNS: Column<OrderLine>[] = [
  { key: "product", header: "Product", render: (l) => l.product_name },
  {
    key: "qty",
    header: "Ordered",
    numeric: true,
    render: (l) => `${l.quantity} ${l.uom_code.toLowerCase()}`,
  },
  {
    key: "outstanding",
    header: "Still to ship",
    numeric: true,
    render: (l) =>
      l.undispatched_base > 0 ? (
        <span className="text-warn-text">{l.undispatched_base.toLocaleString()}</span>
      ) : (
        <span className="text-ok-text">None</span>
      ),
  },
  { key: "value", header: "Value", numeric: true, render: (l) => MONEY.format(l.line_total) },
];

export function DistributionScreen({
  locationId,
  organizationId,
}: {
  locationId: string | null;
  organizationId?: string;
}) {
  const queryClient = useQueryClient();
  const [queue, setQueue] = useState("confirm");
  const [selected, setSelected] = useState<PurchaseOrder | null>(null);
  const [pending, setPending] = useState<Alert[]>([]);
  const [accepted, setAccepted] = useState<string[]>([]);
  const [failure, setFailure] = useState("");

  const orders = useQuery({
    queryKey: ["orders", "received"],
    queryFn: () => api.fulfilment(),
  });

  const refresh = (updated: PurchaseOrder) => {
    queryClient.invalidateQueries({ queryKey: ["orders"] });
    setSelected((open) => (open && open.id === updated.id ? updated : open));
    setPending([]);
    setAccepted([]);
    setFailure("");
  };

  const onRefusal = (error: unknown) => {
    if (!(error instanceof ApiFailure)) return;
    const raised = (error.error.meta?.alerts as Alert[] | undefined) ?? [];
    setPending(raised);
    if (raised.length === 0) setFailure(error.error.message);
  };

  const confirm = useMutation({
    mutationFn: (id: string) => api.confirmOrder(id, { acknowledged: accepted }),
    onSuccess: refresh,
    onError: onRefusal,
  });

  const prepare = useMutation({
    mutationFn: (id: string) => api.prepareOrder(id),
    onSuccess: refresh,
    onError: onRefusal,
  });

  const dispatch = useMutation({
    mutationFn: (id: string) =>
      api.dispatchOrder(id, { from_location: locationId! }).then(() => api.order(id)),
    onSuccess: refresh,
    onError: onRefusal,
  });

  if (orders.isPending) return <Skeleton className="h-[400px]" />;
  if (orders.isError) {
    return (
      <>
        <PageHeader title="Distribution" />
        <ErrorState message="Could not load the queue." onRetry={() => orders.refetch()} />
      </>
    );
  }

  const all = orders.data.results;
  const chosen = QUEUES.find((q) => q.id === queue) ?? QUEUES[0];
  const rows = all.filter((o) => chosen.match.includes(o.status));

  const tabs: TableTab[] = QUEUES.map((q) => ({
    id: q.id,
    label: q.label,
    count: all.filter((o) => q.match.includes(o.status)).length,
  }));

  const columns: Column<PurchaseOrder>[] = [
    { key: "number", header: "Order", mono: true, render: (o) => o.number },
    { key: "buyer", header: "Pharmacy", render: (o) => o.buyer_name },
    {
      key: "lines",
      header: "Lines",
      numeric: true,
      render: (o) => o.lines.length.toLocaleString(),
    },
    { key: "value", header: "Value", numeric: true, render: (o) => MONEY.format(o.subtotal) },
    {
      key: "terms",
      header: "Terms",
      render: (o) =>
        o.payment_terms_days === 0 ? "On receipt" : `Net ${o.payment_terms_days}`,
    },
    {
      key: "placed",
      header: "Placed",
      render: (o) => (o.submitted_at ? DAY.format(new Date(o.submitted_at)) : "—"),
    },
    {
      key: "stage",
      header: "Stage",
      render: (o) => {
        const stage = STAGE[o.status] ?? STAGE.SUBMITTED;
        return <StatusPill tone={stage.tone}>{stage.label}</StatusPill>;
      },
    },
    {
      key: "documents",
      header: "Documents",
      width: "11rem",
      render: (o) => <DocumentChips subject={o.id} label={o.number} />,
    },
  ];

  return (
    <>
      <PageHeader title="Distribution" description="Orders placed with this depot." />
      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}
      <TableTabs tabs={tabs} active={queue} onChange={setQueue} />
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(o) => o.id}
        density="compact"
        onRowClick={setSelected}
        caption={`Orders ${chosen.label.toLowerCase()}`}
        emptyHeading="Nothing waiting"
        emptyBody="Orders appear here as pharmacies place them."
      />

      <FulfilmentModal
        order={selected}
        alerts={pending.filter((a) => !accepted.includes(a.code))}
        onAcknowledge={(alert) => setAccepted((codes) => [...codes, alert.code])}
        onClose={() => {
          setSelected(null);
          setPending([]);
          setAccepted([]);
          setFailure("");
        }}
        onConfirm={() => selected && confirm.mutate(selected.id)}
        onPrepare={() => selected && prepare.mutate(selected.id)}
        onDispatch={() => selected && dispatch.mutate(selected.id)}
        busy={confirm.isPending || prepare.isPending || dispatch.isPending}
        canDispatch={Boolean(locationId)}
        viewerOrganization={organizationId}
      />
    </>
  );
}

function FulfilmentModal({
  order,
  alerts,
  onAcknowledge,
  onClose,
  onConfirm,
  onPrepare,
  onDispatch,
  busy,
  canDispatch,
  viewerOrganization,
}: {
  order: PurchaseOrder | null;
  alerts: Alert[];
  onAcknowledge: (alert: Alert) => void;
  onClose: () => void;
  onConfirm: () => void;
  onPrepare: () => void;
  onDispatch: () => void;
  busy: boolean;
  canDispatch: boolean;
  viewerOrganization?: string;
}) {
  if (!order) return null;
  const stage = STAGE[order.status] ?? STAGE.SUBMITTED;

  /* One action per stage. Offering all three at once would invite
     dispatching something nobody confirmed. */
  const blocking = alerts.some((a) => a.severity === "WARNING");
  const action =
    order.status === "SUBMITTED" ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<Check size={16} strokeWidth={1.9} aria-hidden />}
        loading={busy}
        disabled={blocking}
        onClick={onConfirm}
      >
        Confirm order
      </Button>
    ) : order.status === "CONFIRMED" ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<PackageCheck size={16} strokeWidth={1.9} aria-hidden />}
        loading={busy}
        onClick={onPrepare}
      >
        Start picking
      </Button>
    ) : ["PREPARING", "PARTIALLY_DISPATCHED"].includes(order.status) ? (
      <Button
        variant="primary"
        className="w-full"
        icon={<Truck size={16} strokeWidth={1.9} aria-hidden />}
        loading={busy}
        disabled={!canDispatch}
        onClick={onDispatch}
      >
        Dispatch
      </Button>
    ) : undefined;

  return (
    <Modal
      open
      title={order.number}
      subtitle={order.buyer_name}
      onClose={onClose}
      footer={action}
      size="lg"
    >
      <div className="mb-4">
        <StatusPill tone={stage.tone}>{stage.label}</StatusPill>
      </div>

      {/* Credit and licence refusals land here, above the button they
          blocked. */}
      <AlertStack alerts={alerts} onAcknowledge={onAcknowledge} className="mb-4" />

      <DetailList
        rows={[
          ["Value", MONEY.format(order.subtotal)],
          ["Deliver to", order.deliver_to_name],
          [
            "Terms",
            order.payment_terms_days === 0
              ? "Payable on receipt"
              : `Net ${order.payment_terms_days} days`,
          ],
          ["Placed", order.submitted_at ? DAY.format(new Date(order.submitted_at)) : "—"],
        ]}
      />

      <h3 className="mb-2 mt-6 text-section font-semibold">Items</h3>
      <DataTable
        columns={LINE_COLUMNS}
        rows={order.lines}
        rowKey={(l) => l.id}
        density="compact"
        caption={`Items on ${order.number}`}
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
