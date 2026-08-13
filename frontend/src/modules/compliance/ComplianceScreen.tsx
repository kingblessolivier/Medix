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

import { useQuery } from "@tanstack/react-query";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  PageHeader,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { AlertStack } from "@/components/ui/AlertStack";
import { api, type ComplianceState } from "@/lib/api";

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
  const compliance = useQuery({
    queryKey: ["compliance"],
    queryFn: () => api.compliance(),
  });

  if (compliance.isPending) return <Skeleton className="h-[400px]" />;
  const state = compliance.data as ComplianceState;

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
      <PageHeader title="Compliance" description="What is about to lapse." />

      <AlertStack alerts={state.alerts} className="mb-5" />

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
    </>
  );
}
