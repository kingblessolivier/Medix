/* Stock take — counting a room, and the correction that follows.
 *
 * The count is not the correction. A counter walks the shelves and writes
 * down what is there; the adjustment that reconciles the ledger is a
 * separate act by somebody who can authorise it. Collapsing them would
 * let anybody with a clipboard rewrite a balance, which is the control a
 * stock take exists to provide.
 *
 * Expected is frozen when each line is counted, not when the sheet is
 * approved. A pharmacy does not close to count, and a sale made while the
 * counter was three aisles away must not appear as a discrepancy against
 * the person who counted correctly.
 *
 * A variance needs a reason when it is *worth* something — value, not
 * unit count, because ten capsules out of a thousand is the ordinary
 * imprecision of counting a shelf and demanding a sentence for it trains
 * people to type "counting error" on every line, which buries the two
 * vials of insulin that actually went missing.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  ApiFailure,
  api,
  type StockCount,
  type StockCountLine,
  type StockRow,
} from "@/lib/api";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  Banner,
  Button,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { Consequence, Help, NextAction } from "@/components/ui/Guidance";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

const STATUS: Record<string, { tone: Tone; label: string }> = {
  COUNTING: { tone: "warn", label: "Being counted" },
  SUBMITTED: { tone: "info", label: "With the approver" },
  APPROVED: { tone: "ok", label: "Approved" },
  CANCELLED: { tone: "neutral", label: "Abandoned" },
};

export function StockCountScreen() {
  const queryClient = useQueryClient();
  const [failure, setFailure] = useState("");
  const [locationId, setLocationId] = useState("");

  const counts = useQuery({ queryKey: ["stock-counts"], queryFn: () => api.stockCounts() });
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.locations() });

  const open = useMutation({
    mutationFn: () => api.openCount(locationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stock-counts"] });
      setFailure("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not opened."),
  });

  if (counts.isPending || locations.isPending) return <Skeleton className="h-[400px]" />;

  const rows = counts.data?.results ?? [];
  const live = rows.find((c) => c.status === "COUNTING" || c.status === "SUBMITTED");

  return (
    <>
      <PageHeader title="Stock count" description="Count a room, then reconcile" />

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {live ? (
        <ActiveCount count={live} />
      ) : (
        <>
          <NextAction
            heading="Count a room"
            detail="Nothing moves until the sheet is approved."
          />
          <div className="mb-6 flex max-w-md items-end gap-2">
            <div className="flex-1">
              <Field label="Room">
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
            <Button
              variant="primary"
              disabled={!locationId}
              loading={open.isPending}
              onClick={() => open.mutate()}
            >
              Start counting
            </Button>
          </div>
        </>
      )}

      <h2 className="mb-2 text-section font-semibold text-text">Earlier counts</h2>
      <DataTable
        columns={[
          { key: "ref", header: "Count", mono: true, render: (c: StockCount) => c.reference },
          { key: "room", header: "Room", render: (c: StockCount) => c.location_name },
          {
            key: "by",
            header: "Counted by",
            render: (c: StockCount) => c.counted_by_name || "—",
          },
          {
            key: "variance",
            header: "Difference",
            numeric: true,
            render: (c: StockCount) =>
              c.variance_base === 0 ? "—" : c.variance_base.toLocaleString(),
          },
          {
            key: "status",
            header: "Status",
            render: (c: StockCount) => {
              const state = STATUS[c.status] ?? STATUS.COUNTING;
              return <StatusPill tone={state.tone}>{state.label}</StatusPill>;
            },
          },
        ]}
        rows={rows.filter((c) => c.status === "APPROVED" || c.status === "CANCELLED")}
        rowKey={(c) => c.id}
        density="compact"
        caption="Stock counts"
        emptyHeading="No counts"
        emptyBody="A count reconciles the shelf to the ledger."
      />
    </>
  );
}

function ActiveCount({ count }: { count: StockCount }) {
  const queryClient = useQueryClient();
  const [failure, setFailure] = useState("");
  const [batchId, setBatchId] = useState("");
  const [counted, setCounted] = useState("");
  const [reason, setReason] = useState("");

  const stock = useQuery({ queryKey: ["stock"], queryFn: () => api.stock() });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["stock-counts"] });
    setFailure("");
  };
  const onError = (error: unknown) =>
    setFailure(error instanceof ApiFailure ? error.error.message : "Not saved.");

  const record = useMutation({
    mutationFn: () =>
      api.countBatch(count.id, {
        batch: batchId,
        counted_base: Number(counted),
        reason,
      }),
    onSuccess: () => {
      refresh();
      setCounted("");
      setReason("");
      setBatchId("");
    },
    onError,
  });
  const submit = useMutation({
    mutationFn: () => api.submitCount(count.id),
    onSuccess: refresh,
    onError,
  });
  const approve = useMutation({
    mutationFn: () => api.approveCount(count.id),
    onSuccess: () => {
      refresh();
      queryClient.invalidateQueries({ queryKey: ["stock"] });
    },
    onError,
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelCount(count.id, "Abandoned"),
    onSuccess: refresh,
    onError,
  });

  const onShelf = (stock.data?.results ?? []).filter(
    (row: StockRow) => row.location === count.location && row.status === "AVAILABLE",
  );
  const counting = count.status === "COUNTING";
  const unexplained = count.lines.filter((line) => line.needs_a_reason);

  const columns: Column<StockCountLine>[] = [
    { key: "product", header: "Product", render: (l) => l.product_name },
    { key: "batch", header: "Batch", mono: true, render: (l) => l.batch_number },
    {
      key: "expected",
      header: "Ledger said",
      numeric: true,
      render: (l) => l.expected_base.toLocaleString(),
    },
    {
      key: "counted",
      header: "Counted",
      numeric: true,
      render: (l) => l.counted_base.toLocaleString(),
    },
    {
      key: "variance",
      header: "Difference",
      numeric: true,
      render: (l) =>
        l.variance_base === 0 ? (
          <span className="text-text-3">—</span>
        ) : (
          <span className={l.variance_base < 0 ? "text-bad-text" : "text-warn-text"}>
            {l.variance_base > 0 ? "+" : ""}
            {l.variance_base.toLocaleString()}
            <span className="ml-2 text-help text-text-3">
              {MONEY.format(l.variance_value)}
            </span>
          </span>
        ),
    },
    {
      key: "reason",
      header: "Reason",
      render: (l) =>
        l.needs_a_reason ? (
          <StatusPill tone="bad">Needs a reason</StatusPill>
        ) : (
          <span className="text-text-2">{l.reason || "—"}</span>
        ),
    },
  ];

  return (
    <section className="mb-8">
      {counting ? (
        <NextAction
          heading={`Counting ${count.location_name}`}
          detail="Nothing moves until somebody approves the sheet."
          action={
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => cancel.mutate()}>
                Abandon
              </Button>
              <Button
                variant="primary"
                disabled={count.lines.length === 0 || unexplained.length > 0}
                loading={submit.isPending}
                onClick={() => submit.mutate()}
              >
                Hand it in
              </Button>
            </div>
          }
        />
      ) : (
        <NextAction
          heading="Approve the count"
          detail="This is the step that moves stock."
          action={
            <Button
              variant="primary"
              loading={approve.isPending}
              onClick={() => approve.mutate()}
            >
              Approve
            </Button>
          }
        />
      )}

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {!counting && (
        <div className="mb-4">
          <Consequence
            lines={[
              "Adjusts every line that differs, through the ledger.",
              "The counter cannot approve their own sheet.",
            ]}
          />
        </div>
      )}

      {counting && (
        <div className="mb-4 flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface p-3">
          <div className="min-w-[16rem] flex-1">
            <Field label="Batch">
              {(id) => (
                <Select id={id} value={batchId} onChange={(e) => setBatchId(e.target.value)}>
                  <option value="">Choose a batch</option>
                  {onShelf.map((row: StockRow) => (
                    <option key={row.batch} value={row.batch}>
                      {row.product_name} · {row.batch_number}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <div className="w-32">
            <Field
              label={
                (
                  <Help term="Counted">
                    What is physically on the shelf, in base units. The ledger figure is
                    frozen now, so a sale made while you count is not a difference.
                  </Help>
                ) as unknown as string
              }
            >
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={0}
                  value={counted}
                  onChange={(e) => setCounted(e.target.value)}
                  className="tabular text-right"
                />
              )}
            </Field>
          </div>
          <div className="min-w-[14rem] flex-1">
            <Field label="Reason" help="Only if the difference is a large one.">
              {(id) => (
                <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
              )}
            </Field>
          </div>
          <Button
            variant="primary"
            disabled={!batchId || counted === ""}
            loading={record.isPending}
            onClick={() => record.mutate()}
          >
            Record
          </Button>
        </div>
      )}

      {count.lines.length === 0 ? (
        <EmptyState heading="Nothing counted" body="Pick a batch and record what is there." />
      ) : (
        <DataTable
          columns={columns}
          rows={count.lines}
          rowKey={(l) => l.id}
          density="compact"
          caption={`Lines on ${count.reference}`}
        />
      )}
    </section>
  );
}
