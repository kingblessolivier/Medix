/* What is about to stop working.
 *
 * A premises licence that lapses withdraws every capability the
 * organization had, on a date nobody was watching. A pharmacist
 * registration that lapses stops dispensing. Neither announces itself —
 * the system simply starts refusing — so this screen exists to give the
 * warning that the refusal will not.
 *
 * Read from live records, never from a status column somebody has to
 * remember to update. A compliance dashboard that can be stale is worse
 * than none, because it is believed.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  Banner,
  Button,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import { Consequence, NextAction } from "@/components/ui/Guidance";
import { AlertStack } from "@/components/ui/AlertStack";
import { ApiFailure, api, type ComplianceState } from "@/lib/api";

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

/** Runway, not a date. "In 23 days" is actionable; a date needs mental
    arithmetic every time somebody reads it. */
function runway(days: number): { tone: Tone; label: string } {
  if (days < 0) return { tone: "bad", label: "Expired" };
  if (days <= 30) return { tone: "bad", label: `${days} days` };
  if (days <= 90) return { tone: "warn", label: `${days} days` };
  return { tone: "ok", label: `${days} days` };
}

type Licence = ComplianceState["licences"][number];
type Registration = ComplianceState["registrations"][number];

export function ComplianceScreen() {
  const [renewing, setRenewing] = useState<"licence" | "registration" | null>(null);

  const compliance = useQuery({
    queryKey: ["compliance"],
    queryFn: () => api.compliance(),
  });

  if (compliance.isPending) return <Skeleton className="h-[400px]" />;
  const state = compliance.data as ComplianceState;

  const lapsed = state.licences.filter((l) => !l.is_valid);
  const canDispense = state.registrations.some((r) => r.is_valid);

  const licenceColumns: Column<Licence>[] = [
    { key: "kind", header: "Licence", render: (l) => l.kind_label },
    { key: "number", header: "Number", mono: true, render: (l) => l.number },
    { key: "expiry", header: "Expires", render: (l) => DAY.format(new Date(l.expiry)) },
    {
      key: "runway",
      header: "Remaining",
      render: (l) => {
        const state = runway(l.days_remaining);
        return <StatusPill tone={state.tone}>{state.label}</StatusPill>;
      },
    },
    {
      key: "capability",
      header: "Capability",
      render: (l) =>
        l.is_valid ? (
          <span className="text-ok-text">Granted</span>
        ) : (
          /* Says the consequence, not the state. "Expired" is a fact
             about a date; "withdrawn" is what it did to the pharmacy. */
          <span className="text-bad-text">Withdrawn</span>
        ),
    },
  ];

  const registrationColumns: Column<Registration>[] = [
    { key: "name", header: "Pharmacist", render: (r) => r.name },
    { key: "council", header: "Council number", mono: true, render: (r) => r.council_number },
    { key: "expiry", header: "Expires", render: (r) => DAY.format(new Date(r.expiry)) },
    {
      key: "runway",
      header: "Remaining",
      render: (r) => {
        const state = runway(r.days_remaining);
        return <StatusPill tone={state.tone}>{state.label}</StatusPill>;
      },
    },
    {
      key: "dispensing",
      header: "Dispensing",
      render: (r) =>
        r.is_valid ? (
          <span className="text-ok-text">Permitted</span>
        ) : (
          <span className="text-bad-text">Blocked</span>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Compliance"
        description="What is about to lapse."
        actions={
          <div className="flex gap-2">
            <Button
              variant="secondary"
              icon={<Plus size={15} strokeWidth={2} aria-hidden />}
              onClick={() => setRenewing("licence")}
            >
              Add licence
            </Button>
            <Button
              variant="primary"
              icon={<Plus size={15} strokeWidth={2} aria-hidden />}
              onClick={() => setRenewing("registration")}
            >
              Add registration
            </Button>
          </div>
        }
      />

      <AlertStack alerts={state.alerts} className="mb-5" />

      {/* A screen that reports "dispensing: blocked" and offers no way to
          fix it teaches people to ignore it. Both of these were exactly
          that until the records became writable. */}
      {!canDispense && (
        <NextAction
          heading="Record a registration"
          detail="Nothing can be dispensed until a pharmacist is registered."
          action={
            <Button variant="primary" onClick={() => setRenewing("registration")}>
              Add registration
            </Button>
          }
        />
      )}
      {canDispense && lapsed.length > 0 && (
        <NextAction
          heading={`Renew ${lapsed.length} licence${lapsed.length === 1 ? "" : "s"}`}
          detail="An expired licence withdraws what this pharmacy may do."
          action={
            <Button variant="primary" onClick={() => setRenewing("licence")}>
              Add licence
            </Button>
          }
        />
      )}

      <h2 className="mb-2 text-section font-semibold text-text">Premises licences</h2>
      <DataTable
        columns={licenceColumns}
        rows={state.licences}
        rowKey={(l) => l.id}
        density="compact"
        caption="Premises licences"
        emptyHeading="No licences"
        emptyBody="Without one this organization can do nothing."
      />

      <h2 className="mb-2 mt-6 text-section font-semibold text-text">
        Pharmacist registrations
      </h2>
      <DataTable
        columns={registrationColumns}
        rows={state.registrations}
        rowKey={(r) => r.id}
        density="compact"
        caption="Pharmacist registrations"
        emptyHeading="No registrations"
        emptyBody="Stock can be held. Nothing can be dispensed."
      />

      {renewing === "licence" && <LicenceModal onClose={() => setRenewing(null)} />}
      {renewing === "registration" && (
        <RegistrationModal onClose={() => setRenewing(null)} />
      )}
    </>
  );
}

/* The stored values, not the Python attribute names — `RETAIL`, not
   `RETAIL_PHARMACY`. Getting that wrong is invisible until the server
   rejects the choice, which is how this was found. Source of truth is
   `LicenceKind` in backend/core/models.py. */
const KINDS = [
  ["RETAIL", "Retail pharmacy"],
  ["WHOLESALE", "Wholesale pharmacy"],
  ["IMPORTER", "Importer"],
  ["DISTRIBUTOR", "Distributor"],
] as const;

/* Adding, not editing. A renewed licence is a second record covering a
   second period — rewriting the first one's expiry would erase the fact
   that there was ever a gap, which is the thing an inspection asks
   about. */
function LicenceModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [branch, setBranch] = useState("");
  const [kind, setKind] = useState<string>(KINDS[0][0]);
  const [number, setNumber] = useState("");
  const [issuedOn, setIssuedOn] = useState("");
  const [expiry, setExpiry] = useState("");
  const [failure, setFailure] = useState("");

  const branches = useQuery({ queryKey: ["branches"], queryFn: () => api.branches() });

  useEffect(() => {
    const first = branches.data?.results?.[0];
    if (first && !branch) setBranch(first.id);
  }, [branches.data, branch]);

  const save = useMutation({
    mutationFn: () =>
      api.saveLicence({
        branch,
        kind,
        number,
        issued_on: issuedOn,
        expiry,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["compliance"] });
      queryClient.invalidateQueries({ queryKey: ["capabilities"] });
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  const ready = branch && number.trim() && issuedOn && expiry;

  return (
    <Modal
      open
      title="Add licence"
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Add licence
        </Button>
      }
    >
      <Consequence
        lines={[
          "Grants what this licence permits, from its issue date.",
          "A renewal is a new record. The old one stays readable.",
        ]}
      />
      {failure && (
        <Banner tone="bad" className="mt-3">
          {failure}
        </Banner>
      )}
      <div className="mt-4 flex flex-col gap-4">
        <Field label="Branch" required>
          {(id) => (
            <Select id={id} value={branch} onChange={(e) => setBranch(e.target.value)}>
              {(branches.data?.results ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Type" required>
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Number" help="As printed on the Rwanda FDA licence." required>
          {(id) => (
            <Input id={id} value={number} onChange={(e) => setNumber(e.target.value)} />
          )}
        </Field>
        <Field label="Issued" required>
          {(id) => (
            <Input
              id={id}
              type="date"
              value={issuedOn}
              onChange={(e) => setIssuedOn(e.target.value)}
            />
          )}
        </Field>
        <Field label="Expires" required>
          {(id) => (
            <Input
              id={id}
              type="date"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}

function RegistrationModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState("");
  const [councilNumber, setCouncilNumber] = useState("");
  const [issuedOn, setIssuedOn] = useState("");
  const [expiry, setExpiry] = useState("");
  const [failure, setFailure] = useState("");

  const colleagues = useQuery({
    queryKey: ["colleagues"],
    queryFn: () => api.colleagues(),
  });

  useEffect(() => {
    const first = colleagues.data?.results?.[0];
    if (first && !user) setUser(first.id);
  }, [colleagues.data, user]);

  const save = useMutation({
    mutationFn: () =>
      api.saveRegistration({
        user,
        council_number: councilNumber,
        issued_on: issuedOn,
        expiry,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["compliance"] });
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  const ready = user && councilNumber.trim() && issuedOn && expiry;

  return (
    <Modal
      open
      title="Add registration"
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Add registration
        </Button>
      }
    >
      <Consequence
        lines={[
          "Lets this person verify prescriptions and dispense.",
          "Their council number goes on every dispensing record.",
        ]}
      />
      {failure && (
        <Banner tone="bad" className="mt-3">
          {failure}
        </Banner>
      )}
      <div className="mt-4 flex flex-col gap-4">
        <Field label="Pharmacist" required>
          {(id) => (
            <Select id={id} value={user} onChange={(e) => setUser(e.target.value)}>
              {(colleagues.data?.results ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Council number" required>
          {(id) => (
            <Input
              id={id}
              value={councilNumber}
              onChange={(e) => setCouncilNumber(e.target.value)}
            />
          )}
        </Field>
        <Field label="Issued" required>
          {(id) => (
            <Input
              id={id}
              type="date"
              value={issuedOn}
              onChange={(e) => setIssuedOn(e.target.value)}
            />
          )}
        </Field>
        <Field label="Expires" required>
          {(id) => (
            <Input
              id={id}
              type="date"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}
