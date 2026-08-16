/* Day end — the screen that replaces the notebook.
 *
 * The pharmacist reviews an exception rather than reconstructing a day.
 * Every sale was recorded as it happened, so the only number a person
 * supplies is the one only a person can know: what is actually in the
 * drawer.
 *
 * X report reads the shift. Z report closes it. That distinction is the
 * whole design — you can check the drawer against the system at any
 * point in the day without ending the day, and a cashier who can only
 * find out by closing will not check.
 *
 * Two refusals, both deliberate, both in `sales/shifts.py`:
 *
 *   Closing over a pending mobile-money request is refused, because that
 *   produces a variance which is not a counting error and will be chased
 *   as one.
 *
 *   A variance past the threshold needs a reason before the day closes.
 *   Not a warning — a shortfall nobody wrote a sentence about is a
 *   shortfall nobody looked into.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiFailure, api, type DayEnd, type Shift, type Till } from "@/lib/api";
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
} from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import { Consequence, Help, NextAction } from "@/components/ui/Guidance";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const money = (minor: number) => MONEY.format(minor);

const WHEN = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Africa/Kigali",
});

const METHOD: Record<string, string> = {
  CASH: "Cash",
  MOBILE_MONEY: "Mobile money",
  CARD: "Card",
  INSURANCE: "Insurance",
  CREDIT: "Credit",
};

export function DayEndScreen() {
  const [closing, setClosing] = useState(false);

  const shifts = useQuery({ queryKey: ["shifts"], queryFn: () => api.shifts() });
  const tills = useQuery({ queryKey: ["tills"], queryFn: () => api.tills() });

  const open = (shifts.data?.results ?? []).find((s) => s.status === "OPEN") ?? null;

  const report = useQuery({
    queryKey: ["x-report", open?.id],
    queryFn: () => api.xReport(open!.id),
    enabled: Boolean(open),
    // The drawer changes as the day goes on.
    refetchInterval: 60_000,
  });

  if (shifts.isPending || tills.isPending) return <Skeleton className="h-[400px]" />;

  return (
    <>
      <PageHeader title="Day end" description="What the drawer should hold" />

      {open ? (
        <OpenShift
          shift={open}
          report={report.data}
          onClose={() => setClosing(true)}
        />
      ) : (
        <NoShift tills={tills.data?.results ?? []} />
      )}

      <FiscalExceptions />

      <ClosedShifts shifts={(shifts.data?.results ?? []).filter((s) => s.status !== "OPEN")} />

      {closing && open && report.data && (
        <CloseModal
          shift={open}
          report={report.data}
          onClose={() => setClosing(false)}
        />
      )}
    </>
  );
}

/* Nothing can be reconciled until a shift is open, and a sale rung up
   before one exists belongs to no day. So this is the first thing on the
   screen when there is no shift, not a setting buried somewhere. */
function NoShift({ tills }: { tills: Till[] }) {
  const queryClient = useQueryClient();
  const [till, setTill] = useState("");
  const [float, setFloat] = useState("0");
  const [failure, setFailure] = useState("");

  useEffect(() => {
    if (tills[0] && !till) setTill(tills[0].id);
  }, [tills, till]);

  const open = useMutation({
    mutationFn: () => api.openShift(till, Number(float) || 0),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["shifts"] });
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not opened."),
  });

  if (tills.length === 0) {
    return (
      <EmptyState
        heading="No till"
        body="A till is the drawer a shift is counted against."
        action={<NewTill />}
      />
    );
  }

  return (
    <>
      <NextAction
        heading="Open the day"
        detail="Sales rung up before this belong to no day."
      />
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}
      <div className="flex max-w-md flex-col gap-4 rounded-lg border border-border bg-surface p-4">
        <Field label="Till">
          {(id) => (
            <Select id={id} value={till} onChange={(e) => setTill(e.target.value)}>
              {tills.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Opening float" help="What is in the drawer before trading.">
          {(id) => (
            <Input
              id={id}
              type="number"
              min={0}
              value={float}
              onChange={(e) => setFloat(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>
        <Button
          variant="primary"
          loading={open.isPending}
          onClick={() => open.mutate()}
        >
          Open shift
        </Button>
      </div>
    </>
  );
}

function NewTill() {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [failure, setFailure] = useState("");

  const branches = useQuery({ queryKey: ["branches"], queryFn: () => api.branches() });

  const save = useMutation({
    mutationFn: () =>
      api.saveTill({
        name,
        code,
        branch: branches.data!.results[0].id,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tills"] });
      setAdding(false);
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  if (!adding) {
    return (
      <Button variant="primary" onClick={() => setAdding(true)}>
        Add till
      </Button>
    );
  }

  return (
    <Modal open title="Add till" onClose={() => setAdding(false)}>
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}
      <div className="flex flex-col gap-4">
        <Field label="Name" required>
          {(id) => (
            <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />
          )}
        </Field>
        <Field label="Code" required>
          {(id) => (
            <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} />
          )}
        </Field>
        <Button
          variant="primary"
          disabled={!name.trim() || !code.trim() || !branches.data?.results.length}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Add till
        </Button>
      </div>
    </Modal>
  );
}

function OpenShift({
  shift,
  report,
  onClose,
}: {
  shift: Shift;
  report: DayEnd | undefined;
  onClose: () => void;
}) {
  return (
    <>
      <NextAction
        heading="Count the drawer"
        detail={`${shift.till_name}, open since ${WHEN.format(new Date(shift.opened_at))}.`}
        action={
          <Button variant="primary" onClick={onClose} disabled={!report}>
            Close the day
          </Button>
        }
      />

      {report?.pending_payments ? (
        <Banner tone="warn" className="mb-4">
          {`${report.pending_payments} payment${report.pending_payments === 1 ? "" : "s"} still pending.`}
        </Banner>
      ) : null}

      {!report ? (
        <Skeleton className="h-[220px]" />
      ) : (
        <>
          <div className="mb-5 grid gap-4 sm:grid-cols-4">
            <Figure label="Sales" value={money(report.sales_total)} />
            <Figure label="Transactions" value={String(report.transactions)} />
            <Figure label="Items" value={String(report.items_sold)} />
            <Figure
              label="Expected in drawer"
              value={money(report.expected_cash)}
              strong
            />
          </div>

          <h2 className="mb-2 text-section font-semibold text-text">Taken by</h2>
          <DetailList
            rows={[
              ...Object.entries(report.by_method).map(
                ([method, total]) =>
                  [METHOD[method] ?? method, money(total)] as [string, string],
              ),
              ["Opening float", money(shift.opening_float)],
              ["Discounts", money(report.discounts)],
              ["Tax", money(report.tax_total)],
            ]}
          />
        </>
      )}
    </>
  );
}

function Figure({
  label,
  value,
  strong,
}: {
  label: string;
  value: string;
  strong?: boolean;
}) {
  return (
    <div>
      <p className="text-help text-text-2">{label}</p>
      <p
        className={
          "tabular text-metric " + (strong ? "font-semibold text-text" : "text-text")
        }
      >
        {value}
      </p>
    </div>
  );
}

function CloseModal({
  shift,
  report,
  onClose,
}: {
  shift: Shift;
  report: DayEnd;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [counted, setCounted] = useState("");
  const [reason, setReason] = useState("");
  const [allowPending, setAllowPending] = useState(false);
  const [failure, setFailure] = useState("");
  const [code, setCode] = useState("");

  const close = useMutation({
    mutationFn: () =>
      api.closeShift(shift.id, {
        counted_cash: Number(counted),
        variance_reason: reason,
        allow_pending: allowPending,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["shifts"] });
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApiFailure) {
        setCode(error.error.code);
        setFailure(error.error.message);
      } else {
        setFailure("Not closed.");
      }
    },
  });

  const variance = counted === "" ? null : Number(counted) - report.expected_cash;

  return (
    <Modal
      open
      title="Close the day"
      subtitle={shift.till_name}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={counted === ""}
          loading={close.isPending}
          onClick={() => close.mutate()}
        >
          Close the day
        </Button>
      }
    >
      <Consequence
        lines={[
          "Ends the shift. Later sales belong to the next one.",
          "Records the variance against this till.",
        ]}
      />

      <div className="mt-4 flex flex-col gap-4">
        <DetailList
          rows={[
            [
              (
                <Help term="Expected">
                  Opening float plus every payment that actually settled. A pending
                  request is not in the drawer.
                </Help>
              ) as unknown as string,
              money(report.expected_cash),
            ],
          ]}
        />

        <Field label="Counted" help="What is physically in the drawer." required>
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

        {/* Shown as it is typed, so the reason field appearing is not a
            surprise at the moment of pressing the button. */}
        {variance !== null && variance !== 0 && (
          <Banner tone={Math.abs(variance) > 1000 ? "bad" : "warn"}>
            {variance > 0
              ? `${money(variance)} more than expected.`
              : `${money(-variance)} short.`}
          </Banner>
        )}

        {(code === "variance_unexplained" ||
          (variance !== null && Math.abs(variance) > 1000)) && (
          <Field label="Reason" help="A shortfall nobody explained is one nobody looked into." required>
            {(id) => (
              <Input
                id={id}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Two notes miscounted"
              />
            )}
          </Field>
        )}

        {code === "sales_pending" && (
          <div>
            <Banner tone="warn" className="mb-2">
              {failure}
            </Banner>
            {/* Closing over a pending request produces a variance that is
                not a counting error and will be chased as one. Allowed,
                but only on purpose. */}
            <Button variant="secondary" onClick={() => setAllowPending(true)}>
              Close anyway
            </Button>
          </div>
        )}

        {failure && code !== "sales_pending" && <Banner tone="bad">{failure}</Banner>}
      </div>
    </Modal>
  );
}

function ClosedShifts({ shifts }: { shifts: Shift[] }) {
  if (shifts.length === 0) return null;
  return (
    <section className="mt-8">
      <h2 className="mb-2 text-section font-semibold text-text">Earlier days</h2>
      <ul className="flex flex-col divide-y divide-hair border-y border-hair">
        {shifts.slice(0, 10).map((shift) => (
          <li
            key={shift.id}
            className="flex items-baseline justify-between gap-3 py-2"
          >
            <span className="text-body text-text">
              {shift.till_name}
              <span className="ml-2 text-help text-text-3">
                {shift.closed_at ? WHEN.format(new Date(shift.closed_at)) : ""}
              </span>
            </span>
            <span className="flex items-center gap-3">
              <span className="tabular text-body text-text-2">
                {shift.counted_cash === null ? "—" : money(shift.counted_cash)}
              </span>
              {shift.variance === null || shift.variance === 0 ? (
                <StatusPill tone="ok">Balanced</StatusPill>
              ) : (
                <StatusPill tone={Math.abs(shift.variance) > 1000 ? "bad" : "warn"}>
                  {shift.variance > 0
                    ? `${money(shift.variance)} over`
                    : `${money(-shift.variance)} short`}
                </StatusPill>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}


/* Sales that did not fiscalize.
 *
 * F11: a sale either carries an accepted fiscal invoice or it appears
 * here, and is never silently unfiscalized. The queue and its retry
 * existed server-side from the start; the word doing the work in that
 * requirement is *visible*, and nothing showed it.
 *
 * On this screen because day end is when a pharmacist is reconciling and
 * is the one moment they will act on it. */
function FiscalExceptions() {
  const queryClient = useQueryClient();
  const [failure, setFailure] = useState("");

  const exceptions = useQuery({
    queryKey: ["fiscal-exceptions"],
    queryFn: () => api.fiscalExceptions(),
  });

  const retry = useMutation({
    mutationFn: (id: string) => api.retryFiscal(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["fiscal-exceptions"] }),
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not resubmitted."),
  });

  const rows = exceptions.data ?? [];
  if (rows.length === 0) return null;

  return (
    <section className="mt-8">
      <h2 className="mb-1 text-section font-semibold text-text">Not fiscalized</h2>
      <p className="mb-2 text-help text-text-2">
        These sales completed. Their invoices did not.
      </p>

      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}

      <ul className="flex flex-col divide-y divide-hair border-y border-hair">
        {rows.map((row) => (
          <li key={row.id} className="flex items-baseline justify-between gap-3 py-2">
            <span className="min-w-0">
              <span className="block truncate font-mono text-body text-text">
                {row.sale_number}
              </span>
              <span className="block truncate text-help text-text-2">
                {row.error_message || row.error_code || row.status}
                {row.attempts > 0 && ` · ${row.attempts} attempts`}
              </span>
            </span>
            <Button
              variant="secondary"
              loading={retry.isPending}
              onClick={() => retry.mutate(row.id)}
            >
              Resubmit
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}