/* The pharmacies this depot supplies, and how one is admitted.
 *
 * This is a closed distribution network: nobody signs themselves up. A
 * depot enters a pharmacy's licence, and that act creates the
 * organization, its branch, its licence, an administrator who can sign
 * in, and the trading relationship — in one transaction, because half a
 * pharmacy is worse than none.
 *
 * The temporary password is shown **once**, on the response. It is not
 * stored readable and cannot be retrieved again, so the screen says so
 * and makes it copyable rather than pretending it can be looked up.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus } from "lucide-react";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  StatusPill,
} from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import { ApiFailure, api, type Pharmacy, type RegisteredPharmacy } from "@/lib/api";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const KINDS = [
  { value: "RETAIL", label: "Retail pharmacy" },
  { value: "WHOLESALE", label: "Wholesale pharmacy" },
  { value: "IMPORTER", label: "Importer" },
  { value: "DISTRIBUTOR", label: "Distributor" },
];

export function PharmaciesScreen() {
  const [registering, setRegistering] = useState(false);
  const [registered, setRegistered] = useState<RegisteredPharmacy | null>(null);

  const pharmacies = useQuery({
    queryKey: ["pharmacies"],
    queryFn: () => api.pharmacies(),
  });

  const columns: Column<Pharmacy>[] = [
    { key: "name", header: "Pharmacy", render: (p) => p.name },
    { key: "licence", header: "Licence", mono: true, render: (p) => p.licence_number || "—" },
    {
      key: "expiry",
      header: "Expires",
      render: (p) =>
        p.licence_expiry ? DAY.format(new Date(p.licence_expiry)) : "—",
    },
    {
      key: "state",
      header: "Licence",
      render: (p) =>
        p.licence_valid ? (
          <StatusPill tone="ok">Current</StatusPill>
        ) : (
          /* A lapsed licence blocks supply on the server too. Showing it
             here is what stops a depot picking an order it cannot ship. */
          <StatusPill tone="bad">Lapsed</StatusPill>
        ),
    },
    {
      key: "terms",
      header: "Terms",
      render: (p) =>
        p.payment_terms_days === 0 ? "On receipt" : `Net ${p.payment_terms_days}`,
    },
    {
      key: "limit",
      header: "Credit limit",
      numeric: true,
      render: (p) => (p.credit_limit ? MONEY.format(p.credit_limit) : "—"),
    },
    {
      key: "outstanding",
      header: "Outstanding",
      numeric: true,
      render: (p) => (
        <span className={p.outstanding > p.credit_limit ? "text-bad-text" : undefined}>
          {MONEY.format(p.outstanding)}
        </span>
      ),
    },
    {
      key: "active",
      header: "",
      render: (p) => (p.is_active ? null : <Badge tone="neutral">Suspended</Badge>),
    },
  ];

  if (pharmacies.isPending) return <Skeleton className="h-[400px]" />;

  return (
    <>
      <PageHeader
        title="Pharmacies"
        description="Pharmacies this depot supplies."
        actions={
          <Button
            variant="primary"
            icon={<Plus size={16} strokeWidth={1.9} />}
            onClick={() => setRegistering(true)}
          >
            Register pharmacy
          </Button>
        }
      />

      <DataTable
        columns={columns}
        rows={pharmacies.data ?? []}
        rowKey={(p) => p.id}
        density="compact"
        caption="Registered pharmacies"
        emptyHeading="No pharmacies yet"
        emptyBody="Register one to let it order from this depot."
      />

      <RegisterModal
        open={registering}
        onClose={() => setRegistering(false)}
        onRegistered={(result) => {
          setRegistering(false);
          setRegistered(result);
        }}
      />
      <CredentialsModal
        result={registered}
        onClose={() => setRegistered(null)}
      />
    </>
  );
}

function RegisterModal({
  open,
  onClose,
  onRegistered,
}: {
  open: boolean;
  onClose: () => void;
  onRegistered: (result: RegisteredPharmacy) => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    name: "",
    licence_kind: "RETAIL",
    licence_number: "",
    licence_expiry: "",
    tin: "",
    address: "",
    admin_full_name: "",
    admin_email: "",
    admin_phone: "",
    pharmacist_council_number: "",
    credit_limit: "0",
    payment_terms_days: "0",
  });
  const [failure, setFailure] = useState("");

  const set = (key: keyof typeof form) => (value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  const register = useMutation({
    mutationFn: () =>
      api.registerPharmacy({
        ...form,
        credit_limit: Number(form.credit_limit) || 0,
        payment_terms_days: Number(form.payment_terms_days) || 0,
      }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["pharmacies"] });
      setFailure("");
      onRegistered(result);
    },
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Not registered.",
      ),
  });

  if (!open) return null;
  const ready = form.name && form.licence_number && form.licence_expiry;

  return (
    <Modal
      open
      title="Register pharmacy"
      subtitle="Creates the pharmacy, its licence and its administrator."
      onClose={onClose}
      size="lg"
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={register.isPending}
          onClick={() => register.mutate()}
        >
          Register
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {/* Capability comes from the licence, so an expired one is refused
          rather than accepted and left inert. */}
      <Banner tone="info" className="mb-4">
        The licence grants capability. An expired one is refused.
      </Banner>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pharmacy name" required>
          {(id) => (
            <Input
              id={id}
              value={form.name}
              onChange={(e) => set("name")(e.target.value)}
            />
          )}
        </Field>

        <Field label="Licence type" required>
          {(id) => (
            <Select
              id={id}
              value={form.licence_kind}
              onChange={(e) => set("licence_kind")(e.target.value)}
            >
              {KINDS.map((kind) => (
                <option key={kind.value} value={kind.value}>
                  {kind.label}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="Licence number" required>
          {(id) => (
            <Input
              id={id}
              value={form.licence_number}
              onChange={(e) => set("licence_number")(e.target.value)}
            />
          )}
        </Field>

        <Field label="Licence expiry" required>
          {(id) => (
            <Input
              id={id}
              type="date"
              value={form.licence_expiry}
              onChange={(e) => set("licence_expiry")(e.target.value)}
            />
          )}
        </Field>

        <Field label="TIN">
          {(id) => (
            <Input id={id} value={form.tin} onChange={(e) => set("tin")(e.target.value)} />
          )}
        </Field>

        <Field label="Address">
          {(id) => (
            <Input
              id={id}
              value={form.address}
              onChange={(e) => set("address")(e.target.value)}
            />
          )}
        </Field>

        <Field label="Administrator" help="Who signs in for this pharmacy.">
          {(id) => (
            <Input
              id={id}
              value={form.admin_full_name}
              onChange={(e) => set("admin_full_name")(e.target.value)}
            />
          )}
        </Field>

        <Field label="Administrator email">
          {(id) => (
            <Input
              id={id}
              type="email"
              value={form.admin_email}
              onChange={(e) => set("admin_email")(e.target.value)}
            />
          )}
        </Field>

        <Field
          label="Pharmacist council number"
          help="Without one it can hold stock and not dispense."
        >
          {(id) => (
            <Input
              id={id}
              value={form.pharmacist_council_number}
              onChange={(e) => set("pharmacist_council_number")(e.target.value)}
            />
          )}
        </Field>

        <Field label="Phone">
          {(id) => (
            <Input
              id={id}
              value={form.admin_phone}
              onChange={(e) => set("admin_phone")(e.target.value)}
            />
          )}
        </Field>

        <Field label="Credit limit" help="RWF. Zero means immediate payment.">
          {(id) => (
            <Input
              id={id}
              type="number"
              min={0}
              value={form.credit_limit}
              onChange={(e) => set("credit_limit")(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>

        <Field label="Payment terms" help="Days. Zero is on receipt.">
          {(id) => (
            <Input
              id={id}
              type="number"
              min={0}
              value={form.payment_terms_days}
              onChange={(e) => set("payment_terms_days")(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}

function CredentialsModal({
  result,
  onClose,
}: {
  result: RegisteredPharmacy | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  if (!result) return null;

  return (
    <Modal
      open
      title={result.organization.name}
      subtitle="Registered"
      onClose={onClose}
      footer={
        <Button variant="primary" className="w-full" onClick={onClose}>
          Done
        </Button>
      }
    >
      {/* Shown once. Saying so is the whole point — a screen that implies
          it can be looked up later leads to a locked-out pharmacy. */}
      <Banner tone="warn" className="mb-4">
        This password is shown once. Hand it over now.
      </Banner>

      <DetailList
        rows={[
          ["Username", <span className="font-mono">{result.administrator.username}</span>],
          [
            "Temporary password",
            <span className="font-mono">{result.temporary_password}</span>,
          ],
          ["Licence", <span className="font-mono">{result.licence.number}</span>],
          ["Expires", DAY.format(new Date(result.licence.expiry))],
        ]}
      />

      <Button
        className="mt-4 w-full"
        icon={<Copy size={16} strokeWidth={1.9} />}
        onClick={() => {
          navigator.clipboard?.writeText(
            `${result.administrator.username} / ${result.temporary_password}`,
          );
          setCopied(true);
        }}
      >
        {copied ? "Copied" : "Copy credentials"}
      </Button>
    </Modal>
  );
}
