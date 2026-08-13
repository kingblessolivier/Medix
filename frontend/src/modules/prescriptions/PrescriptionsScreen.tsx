/* Prescriptions, and the pharmacist who authorizes them.
 *
 * The rule this screen exists to hold: **OCR extracts, a registered
 * pharmacist authorizes.** Anything read off an image is advisory and is
 * shown as such; verification is a deliberate act by a named person
 * whose council number is captured at that moment, so the record stays
 * truthful even if their registration later lapses.
 *
 * There is no "verify all". A control you can apply to a page of
 * prescriptions in one click is not a control.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus } from "lucide-react";
import {
  DataTable,
  TableTabs,
  type Column,
  type TableTab,
} from "@/components/data/DataTable";
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
import { DetailList, Modal } from "@/components/ui/Modal";
import { ApiFailure, api, type Prescription } from "@/lib/api";

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const STATUS: Record<string, { tone: Tone; label: string }> = {
  PENDING: { tone: "warn", label: "Pending" },
  VERIFIED: { tone: "ok", label: "Verified" },
  REJECTED: { tone: "bad", label: "Rejected" },
  PARTIALLY_DISPENSED: { tone: "info", label: "Part dispensed" },
  DISPENSED: { tone: "neutral", label: "Dispensed" },
};

const VIEWS: { id: string; label: string; match?: string[] }[] = [
  { id: "pending", label: "To verify", match: ["PENDING"] },
  { id: "verified", label: "Verified", match: ["VERIFIED", "PARTIALLY_DISPENSED"] },
  { id: "closed", label: "Closed", match: ["DISPENSED", "REJECTED"] },
  { id: "all", label: "All" },
];

export function PrescriptionsScreen() {
  const [view, setView] = useState("pending");
  const [selected, setSelected] = useState<Prescription | null>(null);
  const [raising, setRaising] = useState(false);

  const prescriptions = useQuery({
    queryKey: ["prescriptions"],
    queryFn: () => api.prescriptions(),
  });

  if (prescriptions.isPending) return <Skeleton className="h-[400px]" />;

  const all = prescriptions.data?.results ?? [];
  const chosen = VIEWS.find((v) => v.id === view);
  const rows = chosen?.match ? all.filter((p) => chosen.match!.includes(p.status)) : all;

  const tabs: TableTab[] = VIEWS.map((v) => ({
    id: v.id,
    label: v.label,
    count: v.match ? all.filter((p) => v.match!.includes(p.status)).length : all.length,
  }));

  const columns: Column<Prescription>[] = [
    { key: "number", header: "Number", mono: true, render: (p) => p.number || "—" },
    { key: "patient", header: "Patient", render: (p) => p.patient?.full_name ?? "—" },
    {
      key: "issued",
      header: "Issued",
      render: (p) => (p.issued_on ? DAY.format(new Date(p.issued_on)) : "—"),
    },
    {
      key: "verified",
      header: "Verified by",
      render: (p) => p.verified_by_council_number || "—",
    },
    {
      key: "status",
      header: "Status",
      render: (p) => {
        const status = STATUS[p.status] ?? STATUS.PENDING;
        return <StatusPill tone={status.tone}>{status.label}</StatusPill>;
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="Prescriptions"
        description="A pharmacist verifies. OCR never does."
        actions={
          <Button
            variant="primary"
            icon={<Plus size={16} strokeWidth={1.9} aria-hidden />}
            onClick={() => setRaising(true)}
          >
            Raise prescription
          </Button>
        }
      />

      <TableTabs tabs={tabs} active={view} onChange={setView} />
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(p) => p.id}
        density="compact"
        onRowClick={setSelected}
        caption="Prescriptions"
        emptyHeading="Nothing here"
        emptyBody="Prescriptions raised at the counter appear here."
      />

      <PrescriptionModal
        prescription={selected}
        onClose={() => setSelected(null)}
      />
      <RaiseModal open={raising} onClose={() => setRaising(false)} />
    </>
  );
}

function PrescriptionModal({
  prescription,
  onClose,
}: {
  prescription: Prescription | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [failure, setFailure] = useState("");

  const verify = useMutation({
    mutationFn: () => api.verifyPrescription(prescription!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prescriptions"] });
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Not verified.",
      ),
  });

  if (!prescription) return null;
  const status = STATUS[prescription.status] ?? STATUS.PENDING;

  return (
    <Modal
      open
      title={prescription.number || "Prescription"}
      subtitle={prescription.patient?.full_name}
      onClose={onClose}
      footer={
        prescription.status === "PENDING" ? (
          <Button
            variant="primary"
            className="w-full"
            icon={<Check size={16} strokeWidth={1.9} aria-hidden />}
            loading={verify.isPending}
            onClick={() => verify.mutate()}
          >
            Verify
          </Button>
        ) : undefined
      }
    >
      <div className="mb-4">
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
      </div>

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {prescription.status === "PENDING" && (
        /* Says whose act this is. The council number captured here is
           what the record will carry for ever. */
        <Banner tone="warn" className="mb-4">
          Verifying attaches your council registration to this dispensing.
        </Banner>
      )}

      <DetailList
        rows={[
          ["Patient", prescription.patient?.full_name ?? "—"],
          ["Phone", prescription.patient?.phone || "—"],
          [
            "Issued",
            prescription.issued_on
              ? DAY.format(new Date(prescription.issued_on))
              : "—",
          ],
          [
            "Verified",
            prescription.verified_at
              ? DAY.format(new Date(prescription.verified_at))
              : "—",
          ],
          ["Council number", prescription.verified_by_council_number || "—"],
        ]}
      />

      {prescription.patient?.allergies?.length ? (
        <>
          <h3 className="mb-2 mt-6 text-section font-semibold">Recorded allergies</h3>
          <ul className="flex flex-col gap-1">
            {prescription.patient.allergies.map((allergy) => (
              <li key={allergy.id} className="text-body text-text">
                {allergy.allergen}
                <span className="ml-2 text-help text-text-3">
                  {allergy.severity_label}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Modal>
  );
}

function RaiseModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [patient, setPatient] = useState("");
  const [prescriber, setPrescriber] = useState("");
  const [issuedOn, setIssuedOn] = useState("");
  const [number, setNumber] = useState("");
  const [failure, setFailure] = useState("");

  const patients = useQuery({
    queryKey: ["patients"],
    queryFn: () => api.patients(),
    enabled: open,
  });
  const prescribers = useQuery({
    queryKey: ["prescribers"],
    queryFn: () => api.prescribers(),
    enabled: open,
  });

  const raise = useMutation({
    mutationFn: () =>
      api.createPrescription({
        patient,
        prescriber: prescriber || null,
        issued_on: issuedOn || null,
        number,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prescriptions"] });
      setPatient("");
      setNumber("");
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not raised."),
  });

  if (!open) return null;

  return (
    <Modal
      open
      title="Raise prescription"
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!patient}
          loading={raise.isPending}
          onClick={() => raise.mutate()}
        >
          Raise
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {/* It is raised pending and stays there. Only a pharmacist moves
          it, and the button that does is on the other modal. */}
      <Banner tone="info" className="mb-4">
        Raised pending. A pharmacist verifies it before anything dispenses.
      </Banner>

      <div className="flex flex-col gap-4">
        <Field label="Patient" required>
          {(id) => (
            <Select id={id} value={patient} onChange={(e) => setPatient(e.target.value)}>
              <option value="">Choose a patient</option>
              {(patients.data?.results ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.full_name}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="Prescriber">
          {(id) => (
            <Select
              id={id}
              value={prescriber}
              onChange={(e) => setPrescriber(e.target.value)}
            >
              <option value="">Not recorded</option>
              {(prescribers.data?.results ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.full_name}
                  {row.council_number ? ` · ${row.council_number}` : ""}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="Issued on">
          {(id) => (
            <Input
              id={id}
              type="date"
              value={issuedOn}
              onChange={(e) => setIssuedOn(e.target.value)}
            />
          )}
        </Field>

        <Field label="Reference" help="The number on the paper, if any.">
          {(id) => (
            <Input id={id} value={number} onChange={(e) => setNumber(e.target.value)} />
          )}
        </Field>
      </div>
    </Modal>
  );
}
