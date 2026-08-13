/* Claims, and what schemes owe.
 *
 * Rejections sit in their own queue rather than in the ageing, because
 * they are not late — they are refused, and most are refused for a
 * technical reason that can be corrected and resubmitted. Folding them
 * into "overdue" makes the ageing look worse than it is while hiding
 * work that would recover the money.
 *
 * Under a capitation contract no claim is raised at all: the scheme paid
 * in advance, and the question becomes utilisation. That view is
 * separate for the same reason the model field exists.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Send } from "lucide-react";
import {
  DataTable,
  TableTabs,
  type Column,
  type TableTab,
} from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  Field,
  Input,
  PageHeader,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import {
  ApiFailure,
  api,
  type Claim,
  type ClaimLine,
  type SchemeReceivables,
} from "@/lib/api";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const STATUS: Record<string, { tone: Tone; label: string }> = {
  DRAFT: { tone: "neutral", label: "Draft" },
  SUBMITTED: { tone: "info", label: "Submitted" },
  RESUBMITTED: { tone: "info", label: "Resubmitted" },
  PART_PAID: { tone: "warn", label: "Part paid" },
  PAID: { tone: "ok", label: "Paid" },
  REJECTED: { tone: "bad", label: "Rejected" },
  WRITTEN_OFF: { tone: "neutral", label: "Written off" },
};

const VIEWS: { id: string; label: string; match?: string[] }[] = [
  { id: "draft", label: "To submit", match: ["DRAFT"] },
  { id: "open", label: "Awaiting payment", match: ["SUBMITTED", "RESUBMITTED", "PART_PAID"] },
  { id: "rejected", label: "Rejected", match: ["REJECTED"] },
  { id: "closed", label: "Settled", match: ["PAID", "WRITTEN_OFF"] },
  { id: "receivables", label: "Owed by scheme" },
];

export function ClaimsScreen() {
  const [view, setView] = useState("draft");
  const [selected, setSelected] = useState<Claim | null>(null);

  const claims = useQuery({ queryKey: ["claims"], queryFn: () => api.claims() });

  if (claims.isPending) return <Skeleton className="h-[400px]" />;

  const all = claims.data?.results ?? [];
  const chosen = VIEWS.find((v) => v.id === view);
  const rows = chosen?.match ? all.filter((c) => chosen.match!.includes(c.status)) : all;

  const tabs: TableTab[] = VIEWS.map((v) => ({
    id: v.id,
    label: v.label,
    count: v.match ? all.filter((c) => v.match!.includes(c.status)).length : undefined,
  }));

  const columns: Column<Claim>[] = [
    { key: "number", header: "Claim", mono: true, render: (c) => c.number || "—" },
    { key: "scheme", header: "Scheme", render: (c) => c.scheme_name },
    { key: "patient", header: "Patient", render: (c) => c.patient_name },
    { key: "member", header: "Member", mono: true, render: (c) => c.member_number },
    {
      key: "claimed",
      header: "Claimed",
      numeric: true,
      render: (c) => MONEY.format(c.claimed_amount),
    },
    {
      key: "outstanding",
      header: "Outstanding",
      numeric: true,
      render: (c) => MONEY.format(c.outstanding),
    },
    {
      key: "deadline",
      header: "Submit by",
      render: (c) => {
        if (!c.submit_by || c.status !== "DRAFT") return "—";
        const days = Math.ceil(
          (new Date(c.submit_by).getTime() - Date.now()) / 86_400_000,
        );
        /* The window is the thing that silently loses money: a claim
           submitted late is simply refused. */
        return days < 0 ? (
          <span className="text-bad-text">Missed</span>
        ) : days <= 7 ? (
          <span className="text-warn-text">{days} days</span>
        ) : (
          DAY.format(new Date(c.submit_by))
        );
      },
    },
    {
      key: "status",
      header: "Status",
      render: (c) => {
        const status = STATUS[c.status] ?? STATUS.DRAFT;
        return <StatusPill tone={status.tone}>{status.label}</StatusPill>;
      },
    },
  ];

  return (
    <>
      <PageHeader title="Claims" description="What schemes owe, and why not." />
      <TableTabs tabs={tabs} active={view} onChange={setView} />

      {view === "receivables" ? (
        <Receivables />
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(c) => c.id}
          density="compact"
          onRowClick={setSelected}
          caption="Claims"
          emptyHeading="No claims"
          emptyBody="Claims are raised when a covered sale completes."
        />
      )}

      <ClaimModal claim={selected} onClose={() => setSelected(null)} />
    </>
  );
}

function ClaimModal({ claim, onClose }: { claim: Claim | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [failure, setFailure] = useState("");
  const [responding, setResponding] = useState(false);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["claims"] });
    queryClient.invalidateQueries({ queryKey: ["scheme-receivables"] });
    setFailure("");
  };
  const onError = (error: unknown) =>
    setFailure(error instanceof ApiFailure ? error.error.message : "Not saved.");

  const submit = useMutation({
    mutationFn: () => api.submitClaim(claim!.id),
    onSuccess: refresh,
    onError,
  });
  const pay = useMutation({
    mutationFn: () =>
      api.recordClaimPayment(claim!.id, {
        amount: Number(amount),
        remittance_reference: reference,
      }),
    onSuccess: () => {
      refresh();
      setAmount("");
      setReference("");
    },
    onError,
  });

  if (!claim) return null;
  const status = STATUS[claim.status] ?? STATUS.DRAFT;
  const submittable = claim.status === "DRAFT" || claim.status === "REJECTED";
  /* A claim sat at SUBMITTED forever: the receivable never cleared and
     nothing recorded which line the scheme refused. */
  const awaiting = claim.status === "SUBMITTED" || claim.status === "RESUBMITTED";

  const lineColumns: Column<ClaimLine>[] = [
    { key: "product", header: "Product", render: (l) => l.product_name },
    { key: "gross", header: "Gross", numeric: true, render: (l) => MONEY.format(l.gross_amount) },
    {
      key: "cover",
      header: "Cover",
      numeric: true,
      render: (l) => `${(l.coverage_basis_points / 100).toFixed(0)}%`,
    },
    {
      key: "claimed",
      header: "Claimed",
      numeric: true,
      render: (l) => MONEY.format(l.covered_amount),
    },
    {
      key: "patient",
      header: "Patient paid",
      numeric: true,
      render: (l) => MONEY.format(l.patient_amount),
    },
    {
      key: "allowed",
      header: "Allowed",
      numeric: true,
      render: (l) =>
        l.is_rejected ? (
          <span className="text-bad-text" title={l.rejection_reason}>
            Rejected
          </span>
        ) : (
          MONEY.format(l.allowed_amount)
        ),
    },
  ];

  return (
    <Modal
      open
      title={claim.number || "Draft claim"}
      subtitle={`${claim.scheme_name} · ${claim.patient_name}`}
      onClose={onClose}
      size="lg"
      footer={
        submittable ? (
          <Button
            variant="primary"
            className="w-full"
            icon={<Send size={16} strokeWidth={1.9} aria-hidden />}
            loading={submit.isPending}
            onClick={() => submit.mutate()}
          >
            {claim.status === "REJECTED" ? "Resubmit" : "Submit claim"}
          </Button>
        ) : awaiting && !responding ? (
          <Button variant="primary" className="w-full" onClick={() => setResponding(true)}>
            Record response
          </Button>
        ) : undefined
      }
    >
      <div className="mb-4 flex items-center gap-2">
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
        {claim.status === "REJECTED" && (
          /* Rejected is not the end of it — most are technical. */
          <Badge tone="warn">Correctable</Badge>
        )}
      </div>

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}
      {claim.rejection_reason && (
        <Banner tone="bad" className="mb-4">
          {claim.rejection_reason}
        </Banner>
      )}

      <DetailList
        rows={[
          ["Sale", claim.sale_number],
          ["Member", claim.member_number],
          ["Dispensed", DAY.format(new Date(claim.dispensed_on))],
          ["Claimed", MONEY.format(claim.claimed_amount)],
          ["Allowed", claim.allowed_amount ? MONEY.format(claim.allowed_amount) : "—"],
          ["Patient paid", MONEY.format(claim.patient_paid)],
          ["Received", MONEY.format(claim.settled)],
          ["Outstanding", MONEY.format(claim.outstanding)],
        ]}
      />

      <h3 className="mb-2 mt-6 text-section font-semibold">Lines</h3>
      {responding && (
        <ResponsePanel
          claim={claim}
          onDone={() => {
            setResponding(false);
            refresh();
          }}
        />
      )}

      <DataTable
        columns={lineColumns}
        rows={claim.lines}
        rowKey={(l) => l.id}
        density="compact"
        caption={`Lines on ${claim.number || "this claim"}`}
        emptyHeading="No lines"
      />

      {claim.outstanding > 0 && claim.status !== "DRAFT" && (
        <>
          <h3 className="mb-2 mt-6 text-section font-semibold">Record a remittance</h3>
          <div className="flex items-end gap-2">
            <Field label="Amount" help={`${MONEY.format(claim.outstanding)} outstanding`}>
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={1}
                  max={claim.outstanding}
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className="tabular text-right"
                />
              )}
            </Field>
            <Field label="Remittance reference">
              {(id) => (
                <Input
                  id={id}
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                />
              )}
            </Field>
            <Button
              variant="primary"
              icon={<Check size={16} strokeWidth={1.9} aria-hidden />}
              disabled={!amount || Number(amount) <= 0}
              loading={pay.isPending}
              onClick={() => pay.mutate()}
            >
              Record
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}

function Receivables() {
  const ageing = useQuery({
    queryKey: ["scheme-receivables"],
    queryFn: () => api.schemeReceivables(),
  });

  if (ageing.isPending) return <Skeleton className="h-[300px]" />;
  const data = ageing.data as SchemeReceivables;
  const buckets = Object.keys(data.buckets);

  const columns: Column<(typeof data.schemes)[number]>[] = [
    { key: "scheme", header: "Scheme", render: (row) => String(row.scheme) },
    ...buckets.map((bucket) => ({
      key: bucket,
      header: bucket === "91+" ? "90+ days" : `${bucket} days`,
      numeric: true,
      render: (row: (typeof data.schemes)[number]) =>
        Number(row[bucket]) > 0 ? MONEY.format(Number(row[bucket])) : "—",
    })),
    {
      key: "rejected",
      header: "Rejected",
      numeric: true,
      render: (row) =>
        Number(row.rejected) > 0 ? (
          <span className="text-bad-text">{MONEY.format(Number(row.rejected))}</span>
        ) : (
          "—"
        ),
    },
    {
      key: "total",
      header: "Outstanding",
      numeric: true,
      render: (row) => MONEY.format(Number(row.outstanding)),
    },
  ];

  return (
    <>
      {data.rejected_total > 0 && (
        /* Kept out of the ageing on purpose, and said out loud so it is
           not mistaken for money that is merely late. */
        <Banner tone="warn" className="mb-3">
          {`${MONEY.format(data.rejected_total)} rejected. Correctable and resubmittable.`}
        </Banner>
      )}
      <DataTable
        columns={columns}
        rows={data.schemes}
        rowKey={(row) => String(row.scheme)}
        density="compact"
        caption="Owed by scheme"
        emptyHeading="Nothing outstanding"
        emptyBody="Every submitted claim has been settled."
      />
    </>
  );
}


/* What the scheme actually allowed, line by line.
 *
 * Per line rather than in total because a partial rejection is the
 * common case: "they paid 28,000 of 45,000" tells a pharmacy nothing
 * about which line to fix or resubmit. Each line is either allowed an
 * amount or refused with a reason, and the reason is required — a
 * rejection nobody explained is one nobody can answer. */
function ResponsePanel({ claim, onDone }: { claim: Claim; onDone: () => void }) {
  const [allowed, setAllowed] = useState<Record<string, string>>(() =>
    Object.fromEntries(claim.lines.map((l) => [l.id, String(l.covered_amount)])),
  );
  const [rejections, setRejections] = useState<Record<string, string>>({});
  const [reference, setReference] = useState("");
  const [failure, setFailure] = useState("");

  const respond = useMutation({
    mutationFn: () =>
      api.respondToClaim(claim.id, {
        allowed: Object.fromEntries(
          Object.entries(allowed)
            .filter(([id]) => !rejections[id])
            .map(([id, value]) => [id, Number(value) || 0]),
        ),
        rejections,
        scheme_reference: reference,
      }),
    onSuccess: onDone,
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Not recorded.",
      ),
  });

  function toggleRejection(id: string) {
    setRejections((current) => {
      const next = { ...current };
      if (id in next) delete next[id];
      else next[id] = "";
      return next;
    });
  }

  const unexplained = Object.entries(rejections).filter(([, why]) => !why.trim());

  return (
    <section className="mb-5 rounded-md border border-info bg-info-bg p-3">
      <p className="mb-2 text-body font-medium text-text">What the scheme allowed</p>

      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}

      <ul className="flex flex-col divide-y divide-hair">
        {claim.lines.map((line) => {
          const rejected = line.id in rejections;
          return (
            <li key={line.id} className="py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 flex-1 truncate text-body text-text">
                  {line.product_name}
                  <span className="ml-2 text-help text-text-3">
                    claimed {MONEY.format(line.covered_amount)}
                  </span>
                </span>
                {rejected ? (
                  <Button variant="secondary" onClick={() => toggleRejection(line.id)}>
                    Undo
                  </Button>
                ) : (
                  <span className="flex items-center gap-2">
                    <Input
                      aria-label={`Allowed for ${line.product_name}`}
                      type="number"
                      min={0}
                      value={allowed[line.id] ?? ""}
                      onChange={(e) =>
                        setAllowed((c) => ({ ...c, [line.id]: e.target.value }))
                      }
                      className="w-28 tabular text-right"
                    />
                    <Button variant="secondary" onClick={() => toggleRejection(line.id)}>
                      Refused
                    </Button>
                  </span>
                )}
              </div>
              {rejected && (
                <div className="mt-2">
                  <Input
                    aria-label={`Why ${line.product_name} was refused`}
                    value={rejections[line.id]}
                    onChange={(e) =>
                      setRejections((c) => ({ ...c, [line.id]: e.target.value }))
                    }
                    placeholder="Not covered"
                    invalid={!rejections[line.id].trim()}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <div className="mt-3 flex flex-col gap-3">
        <Field label="Scheme reference" help="What they called it on the remittance.">
          {(id) => (
            <Input
              id={id}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
          )}
        </Field>
        <div className="flex gap-2">
          <Button variant="secondary" className="flex-1" onClick={onDone}>
            Cancel
          </Button>
          <Button
            variant="primary"
            className="flex-1"
            disabled={unexplained.length > 0}
            loading={respond.isPending}
            onClick={() => respond.mutate()}
          >
            Record response
          </Button>
        </div>
      </div>
    </section>
  );
}