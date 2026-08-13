/* The recall console.
 *
 * The exit criterion for this whole area of the product: a simulated
 * recall traces every unit of a batch to its current location or its
 * sale, in under a minute. So the trace comes **first** and the action
 * second — you decide whether to recall by seeing how far the batch
 * travelled, not by recalling and then finding out.
 *
 * Two audiences, and the second is the one that matters. Stock still on
 * our own shelves is pulled by one click. The patients who already have
 * it, and the pharmacies we already shipped it to, have to be rung — and
 * that list is the actual product of a recall.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Phone, Search, TriangleAlert } from "lucide-react";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  Field,
  Input,
  PageHeader,
  Skeleton,
  StatusPill,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import { ApiFailure, api, type BatchTrace, type StockRow } from "@/lib/api";

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});
const WHEN = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Africa/Kigali",
});

export function RecallScreen() {
  const [search, setSearch] = useState("");
  const [tracing, setTracing] = useState<StockRow | null>(null);

  const stock = useQuery({ queryKey: ["stock"], queryFn: () => api.stock() });

  const term = search.trim().toLowerCase();
  /* Grouped by batch, not by balance row: a recall is about a batch, and
     the same batch across four locations is one decision. */
  const batches = new Map<string, StockRow & { locations: number; total: number }>();
  for (const row of stock.data?.results ?? []) {
    const existing = batches.get(row.batch);
    if (existing) {
      existing.locations += 1;
      existing.total += row.quantity_base;
    } else {
      batches.set(row.batch, { ...row, locations: 1, total: row.quantity_base });
    }
  }
  const rows = [...batches.values()].filter(
    (r) =>
      !term ||
      r.batch_number.toLowerCase().includes(term) ||
      r.product_name.toLowerCase().includes(term),
  );

  const columns: Column<(typeof rows)[number]>[] = [
    { key: "product", header: "Product", render: (r) => r.product_name },
    { key: "batch", header: "Batch", mono: true, render: (r) => r.batch_number },
    { key: "expiry", header: "Expires", render: (r) => DAY.format(new Date(r.expiry_date)) },
    {
      key: "locations",
      header: "Locations",
      numeric: true,
      render: (r) => r.locations.toLocaleString(),
    },
    {
      key: "held",
      header: "Units held",
      numeric: true,
      render: (r) => r.total.toLocaleString(),
    },
    {
      key: "status",
      header: "",
      render: (r) =>
        r.status === "RECALLED" ? <StatusPill tone="bad">Recalled</StatusPill> : null,
    },
  ];

  if (stock.isPending) return <Skeleton className="h-[400px]" />;

  return (
    <>
      <PageHeader
        title="Recall"
        description="Trace a batch, then decide."
      />

      <div className="mb-3 max-w-md">
        <Field label="Find a batch">
          {(id) => (
            <Input
              id={id}
              icon={Search}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Batch number or product"
            />
          )}
        </Field>
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.batch}
        density="compact"
        onRowClick={setTracing}
        caption="Batches held"
        emptyHeading="No batches"
        emptyBody="Batches appear here once stock is received."
      />

      <TraceModal row={tracing} onClose={() => setTracing(null)} />
    </>
  );
}

function TraceModal({
  row,
  onClose,
}: {
  row: StockRow | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [authority, setAuthority] = useState("");
  const [failure, setFailure] = useState("");
  const [done, setDone] = useState<{ reference: string; locations: number } | null>(null);

  const trace = useQuery({
    queryKey: ["batch-trace", row?.batch],
    queryFn: () => api.batchTrace(row!.batch),
    enabled: Boolean(row),
  });

  const recall = useMutation({
    mutationFn: () =>
      api.recallBatch({ batch: row!.batch, reason, authority_reference: authority }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["stock"] });
      queryClient.invalidateQueries({ queryKey: ["batch-trace"] });
      setDone({ reference: result.reference, locations: result.locations });
      setConfirming(false);
      setFailure("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not recalled."),
  });

  if (!row) return null;
  const found = trace.data as BatchTrace | undefined;

  return (
    <Modal
      open
      title={row.batch_number}
      subtitle={row.product_name}
      onClose={() => {
        onClose();
        setDone(null);
        setReason("");
        setAuthority("");
      }}
      size="lg"
      footer={
        done ? undefined : (
          <Button
            variant="danger"
            className="w-full"
            icon={<TriangleAlert size={16} strokeWidth={1.9} />}
            onClick={() => setConfirming(true)}
          >
            Recall this batch
          </Button>
        )
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {done && (
        <Banner tone="ok" className="mb-4">
          {`${done.reference} · pulled from ${done.locations} location${done.locations === 1 ? "" : "s"}. Contact the list below.`}
        </Banner>
      )}

      {trace.isPending ? (
        <Skeleton className="h-[200px]" />
      ) : !found ? null : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-3">
            <Figure label="On our shelves" value={found.on_hand_base.toLocaleString()} />
            <Figure
              label="Dispensed to patients"
              value={found.dispensed_base.toLocaleString()}
              urgent={found.dispensed_base > 0}
            />
            <Figure
              label="Shipped to pharmacies"
              value={found.dispatched_base.toLocaleString()}
              urgent={found.dispatched_base > 0}
            />
          </div>

          {/* The half of a recall that actually protects anyone. Above
              the stock figures in importance, so it is above them on the
              page too. */}
          <h3 className="mb-2 text-section font-semibold">
            Patients to contact
            {found.patients.length > 0 && (
              <Badge tone="bad">{found.patients.length}</Badge>
            )}
          </h3>
          {found.patients.length === 0 ? (
            <p className="mb-5 text-body text-text-2">None dispensed.</p>
          ) : (
            <ul className="mb-5 flex flex-col divide-y divide-hair border-y border-hair">
              {found.patients.map((patient) => (
                <li
                  key={`${patient.sale}-${patient.patient}`}
                  className="flex items-baseline justify-between gap-3 py-2"
                >
                  <span className="text-body text-text">
                    {patient.patient || "Not recorded"}
                    <span className="ml-2 font-mono text-help text-text-3">
                      {patient.sale}
                    </span>
                  </span>
                  <span className="flex items-center gap-3">
                    {patient.phone ? (
                      <span className="flex items-center gap-1 text-body text-text-2">
                        <Phone size={13} strokeWidth={1.9} aria-hidden />
                        {patient.phone}
                      </span>
                    ) : (
                      <span className="text-help text-warn-text">No phone recorded</span>
                    )}
                    <span className="tabular-nums text-body text-text-2">
                      {patient.quantity_base.toLocaleString()}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}

          <h3 className="mb-2 text-section font-semibold">
            Pharmacies to notify
            {found.customers.length > 0 && (
              <Badge tone="warn">{found.customers.length}</Badge>
            )}
          </h3>
          {found.customers.length === 0 ? (
            <p className="text-body text-text-2">None shipped.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-hair border-y border-hair">
              {found.customers.map((customer) => (
                <li
                  key={customer.delivery_note}
                  className="flex items-baseline justify-between gap-3 py-2"
                >
                  <span className="text-body text-text">
                    {customer.customer}
                    <span className="ml-2 font-mono text-help text-text-3">
                      {customer.delivery_note}
                    </span>
                  </span>
                  <span className="flex items-center gap-3 text-body text-text-2">
                    {customer.dispatched_at && (
                      <span className="text-help">
                        {WHEN.format(new Date(customer.dispatched_at))}
                      </span>
                    )}
                    <span className="tabular-nums">
                      {customer.quantity_base.toLocaleString()}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {confirming && (
        <Modal
          open
          title="Recall this batch"
          subtitle={`${row.product_name} · ${row.batch_number}`}
          onClose={() => setConfirming(false)}
          footer={
            <Button
              variant="danger"
              className="w-full"
              disabled={!reason.trim()}
              loading={recall.isPending}
              onClick={() => recall.mutate()}
            >
              Recall from every location
            </Button>
          }
        >
          {/* Says what it does before it does it. Recall is not
              reversible by another click. */}
          <Banner tone="bad" className="mb-4">
            Pulls this batch from every location at once. Not reversible.
          </Banner>

          <div className="flex flex-col gap-4">
            <Field label="Reason" help="Recorded against every movement." required>
              {(id) => (
                <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
              )}
            </Field>
            <Field label="Authority reference" help="Rwanda FDA notice, if there is one.">
              {(id) => (
                <Input
                  id={id}
                  value={authority}
                  onChange={(e) => setAuthority(e.target.value)}
                />
              )}
            </Field>
          </div>
        </Modal>
      )}
    </Modal>
  );
}

function Figure({
  label,
  value,
  urgent = false,
}: {
  label: string;
  value: string;
  urgent?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      <p className="text-label font-medium text-text-2">{label}</p>
      <p
        className={
          urgent
            ? "mt-1 text-section font-semibold tabular-nums text-bad-text"
            : "mt-1 text-section font-semibold tabular-nums text-text"
        }
      >
        {value}
      </p>
    </div>
  );
}
