/* Everything a product carries, in one place.
 *
 * Four things had APIs and no way to reach them: the packaging chain,
 * pack photography, the Rwanda FDA registration, and clinical
 * attributes. Each is load-bearing somewhere else in the system, and
 * each was previously editable only in a database.
 *
 * The packaging chain is the one to be careful with. Every ledger
 * quantity is stored in base units, so a chain with two base units or
 * two levels sharing a factor corrupts every quantity recorded against
 * the product — silently. The server validates the whole chain after
 * every write; this screen shows what it computed rather than trusting
 * what was typed.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlus, Plus, Search } from "lucide-react";
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
  Select,
  Skeleton,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import {
  ApiFailure,
  api,
  type ClinicalAttribute,
  type ProductImage,
  type ProductRegistration,
  type ProductRow,
  type UnitOfMeasureRow,
} from "@/lib/api";

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const TABS: TableTab[] = [
  { id: "units", label: "Packaging" },
  { id: "images", label: "Images" },
  { id: "registration", label: "Registration" },
  { id: "clinical", label: "Clinical" },
];

export function ProductEditor() {
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState<ProductRow | null>(null);

  const products = useQuery({
    queryKey: ["products", search],
    queryFn: () => api.products(`?search=${encodeURIComponent(search)}`),
  });

  const columns: Column<ProductRow>[] = [
    { key: "name", header: "Product", render: (p) => p.name },
    { key: "generic", header: "Generic", render: (p) => p.generic_name || "—" },
    { key: "type", header: "Type", render: (p) => p.product_type_code.toLowerCase() },
    { key: "category", header: "Category", render: (p) => p.category_name ?? "—" },
    {
      key: "legal",
      header: "Legal",
      render: (p) =>
        p.requires_prescription ? (
          <Badge tone="warn">{p.legal_status}</Badge>
        ) : (
          <Badge tone="neutral">{p.legal_status}</Badge>
        ),
    },
    {
      key: "cold",
      header: "",
      render: (p) => (p.cold_chain ? <Badge tone="brand">Cold chain</Badge> : null),
    },
  ];

  if (products.isPending) return <Skeleton className="h-[400px]" />;

  return (
    <>
      <PageHeader title="Catalogue" description="What each product carries." />

      <div className="mb-3 max-w-md">
        <Field label="Find a product">
          {(id) => (
            <Input
              id={id}
              icon={Search}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Name or generic"
            />
          )}
        </Field>
      </div>

      <DataTable
        columns={columns}
        rows={products.data?.results ?? []}
        rowKey={(p) => p.id}
        density="compact"
        onRowClick={setChosen}
        caption="Products"
        emptyHeading="No products"
      />

      <ProductModal product={chosen} onClose={() => setChosen(null)} />
    </>
  );
}

function ProductModal({
  product,
  onClose,
}: {
  product: ProductRow | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState("units");
  if (!product) return null;

  return (
    <Modal
      open
      title={product.name}
      subtitle={product.generic_name || product.product_type_code.toLowerCase()}
      onClose={onClose}
      size="lg"
    >
      <TableTabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "units" && <Units product={product} />}
      {tab === "images" && <Images product={product} />}
      {tab === "registration" && <Registration product={product} />}
      {tab === "clinical" && <Clinical product={product} />}
    </Modal>
  );
}

/* -- packaging chain ---------------------------------------------------- */

function Units({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [failure, setFailure] = useState("");

  const units = useQuery({
    queryKey: ["units", product.id],
    queryFn: () => api.units(product.id),
  });

  const columns: Column<UnitOfMeasureRow>[] = [
    { key: "code", header: "Level", mono: true, render: (u) => u.code },
    { key: "name", header: "Name", render: (u) => u.name },
    {
      key: "factor",
      header: "Base units",
      numeric: true,
      render: (u) => u.factor_to_base.toLocaleString(),
    },
    {
      key: "flags",
      header: "",
      render: (u) => (
        <span className="flex gap-1">
          {u.is_base && <Badge tone="brand">Base</Badge>}
          {u.is_purchase_default && <Badge tone="neutral">Buys in</Badge>}
          {u.is_dispense_default && <Badge tone="neutral">Dispenses in</Badge>}
          {!u.is_sellable && <Badge tone="warn">Not sold</Badge>}
        </span>
      ),
    },
  ];

  if (units.isPending) return <Skeleton className="h-[200px]" />;

  return (
    <>
      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}
      {/* Says what the chain is for, because the consequence of getting
          it wrong is invisible until a quantity is already wrong. */}
      <Banner tone="info" className="mb-3">
        Every stored quantity is in base units. Changing a factor changes them all.
      </Banner>

      <DataTable
        columns={columns}
        rows={units.data?.results ?? []}
        rowKey={(u) => u.id}
        density="compact"
        caption="Packaging chain"
        emptyHeading="No units"
      />

      <Button
        className="mt-3"
        icon={<Plus size={16} strokeWidth={1.9} aria-hidden />}
        onClick={() => setAdding(true)}
      >
        Add a level
      </Button>

      {adding && (
        <UnitModal
          product={product}
          onClose={() => setAdding(false)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ["units", product.id] });
            setAdding(false);
          }}
          onFailure={setFailure}
        />
      )}
    </>
  );
}

function UnitModal({
  product,
  onClose,
  onSaved,
  onFailure,
}: {
  product: ProductRow;
  onClose: () => void;
  onSaved: () => void;
  onFailure: (message: string) => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [factor, setFactor] = useState("");
  const [sellable, setSellable] = useState("yes");

  const save = useMutation({
    mutationFn: () =>
      api.saveUnit({
        product: product.id,
        code: code.toUpperCase(),
        name,
        factor_to_base: Number(factor),
        is_base: false,
        is_sellable: sellable === "yes",
      }),
    onSuccess: onSaved,
    onError: (error) =>
      onFailure(
        error instanceof ApiFailure ? error.error.message : "Chain not valid.",
      ),
  });

  return (
    <Modal
      open
      title="Add packaging level"
      subtitle={product.name}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!code.trim() || Number(factor) < 1}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Add
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Code" help="CARTON, PACK, BLISTER." required>
          {(id) => (
            <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} />
          )}
        </Field>
        <Field label="Name" help='What a person calls it: "Box of 30".' required>
          {(id) => (
            <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />
          )}
        </Field>
        <Field label="Base units" help="No two levels may share a factor." required>
          {(id) => (
            <Input
              id={id}
              type="number"
              min={2}
              value={factor}
              onChange={(e) => setFactor(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>
        <Field label="Sellable" help="A depot that will not break a pack says no.">
          {(id) => (
            <Select id={id} value={sellable} onChange={(e) => setSellable(e.target.value)}>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </Select>
          )}
        </Field>
      </div>
    </Modal>
  );
}

/* -- images ------------------------------------------------------------- */

function Images({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [alt, setAlt] = useState("");
  const [failure, setFailure] = useState("");

  const images = useQuery({
    queryKey: ["product-images", product.id],
    queryFn: () => api.productImages(product.id),
  });

  const upload = useMutation({
    mutationFn: () => {
      const body = new FormData();
      body.append("product", product.id);
      body.append("image", file!);
      body.append("alt", alt);
      body.append("is_primary", images.data?.results.length ? "false" : "true");
      return api.uploadProductImage(body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-images", product.id] });
      setFile(null);
      setAlt("");
      setFailure("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not uploaded."),
  });

  if (images.isPending) return <Skeleton className="h-[200px]" />;
  const rows = images.data?.results ?? [];

  return (
    <>
      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}

      {rows.length === 0 ? (
        <p className="mb-3 text-body text-text-2">
          No image. A buyer ordering by carton cannot check the pack.
        </p>
      ) : (
        <div className="mb-4 grid grid-cols-3 gap-3">
          {rows.map((image: ProductImage) => (
            <figure key={image.id} className="rounded-md border border-border p-2">
              <img
                src={image.image}
                alt={image.alt}
                className="h-24 w-full rounded-sm object-contain"
              />
              <figcaption className="mt-1 truncate text-help text-text-3">
                {image.is_primary ? "Primary · " : ""}
                {image.alt}
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-4">
        <Field label="Image" required>
          {(id) => (
            <input
              id={id}
              type="file"
              accept="image/*"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-body text-text-2"
            />
          )}
        </Field>
        {/* Required, not optional: the description a screen reader speaks
            is the same information the picture carries, and a product
            with neither is one nobody can check. */}
        <Field label="What it shows" help="Spoken instead of the image. Required." required>
          {(id) => <Input id={id} value={alt} onChange={(e) => setAlt(e.target.value)} />}
        </Field>
        <Button
          variant="primary"
          icon={<ImagePlus size={16} strokeWidth={1.9} aria-hidden />}
          disabled={!file || !alt.trim()}
          loading={upload.isPending}
          onClick={() => upload.mutate()}
        >
          Upload
        </Button>
      </div>
    </>
  );
}

/* -- registration ------------------------------------------------------- */

function Registration({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    registration_number: "",
    holder: "",
    manufacturer: "",
    manufacturer_country: "",
    registration_expiry: "",
  });
  const [failure, setFailure] = useState("");

  const registrations = useQuery({
    queryKey: ["product-registrations", product.id],
    queryFn: () => api.productRegistrations(product.id),
  });

  const existing = registrations.data?.results?.[0] as ProductRegistration | undefined;

  const save = useMutation({
    mutationFn: () => api.saveProductRegistration({ ...form, product: product.id }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["product-registrations", product.id],
      });
      setFailure("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  if (registrations.isPending) return <Skeleton className="h-[200px]" />;

  if (existing) {
    return (
      <>
        {/* The registration is what makes a product listable and
            dispensable, so its expiry is stated in days rather than as a
            date somebody has to subtract from today. */}
        <Banner
          tone={
            existing.registration_expiry &&
            new Date(existing.registration_expiry) < new Date()
              ? "bad"
              : "ok"
          }
          className="mb-3"
        >
          {existing.registration_expiry
            ? `Registered until ${DAY.format(new Date(existing.registration_expiry))}`
            : "Registered, no expiry recorded"}
        </Banner>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2">
          <dt className="text-body text-text-2">Number</dt>
          <dd className="m-0 text-right font-mono text-body">
            {existing.registration_number}
          </dd>
          <dt className="text-body text-text-2">Holder</dt>
          <dd className="m-0 text-right text-body">{existing.holder || "—"}</dd>
          <dt className="text-body text-text-2">Manufacturer</dt>
          <dd className="m-0 text-right text-body">{existing.manufacturer || "—"}</dd>
          <dt className="text-body text-text-2">Country</dt>
          <dd className="m-0 text-right text-body">
            {existing.manufacturer_country || "—"}
          </dd>
        </dl>
      </>
    );
  }

  const set = (key: keyof typeof form) => (value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  return (
    <>
      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}
      <Banner tone="warn" className="mb-3">
        Not registered. It cannot be listed or dispensed as a medicine.
      </Banner>
      <div className="flex flex-col gap-4">
        <Field label="Registration number" required>
          {(id) => (
            <Input
              id={id}
              value={form.registration_number}
              onChange={(e) => set("registration_number")(e.target.value)}
            />
          )}
        </Field>
        <Field label="Holder">
          {(id) => (
            <Input
              id={id}
              value={form.holder}
              onChange={(e) => set("holder")(e.target.value)}
            />
          )}
        </Field>
        <Field label="Manufacturer">
          {(id) => (
            <Input
              id={id}
              value={form.manufacturer}
              onChange={(e) => set("manufacturer")(e.target.value)}
            />
          )}
        </Field>
        <Field label="Country">
          {(id) => (
            <Input
              id={id}
              value={form.manufacturer_country}
              onChange={(e) => set("manufacturer_country")(e.target.value)}
            />
          )}
        </Field>
        <Field label="Registered until">
          {(id) => (
            <Input
              id={id}
              type="date"
              value={form.registration_expiry}
              onChange={(e) => set("registration_expiry")(e.target.value)}
            />
          )}
        </Field>
        <Button
          variant="primary"
          disabled={!form.registration_number.trim()}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Save registration
        </Button>
      </div>
    </>
  );
}

/* -- clinical attributes ------------------------------------------------ */

const KINDS = [
  { value: "ACTIVE_INGREDIENT", label: "Active ingredient", numeric: false },
  { value: "MIN_AGE_YEARS", label: "Minimum age, years", numeric: true },
  { value: "MAX_AGE_YEARS", label: "Maximum age, years", numeric: true },
  { value: "MAX_DAILY_DOSE_BASE", label: "Maximum daily dose, base units", numeric: true },
  { value: "PREGNANCY_RESTRICTED", label: "Restricted in pregnancy", numeric: true },
];

function Clinical({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState(KINDS[0].value);
  const [value, setValue] = useState("");
  const [source, setSource] = useState("");
  const [failure, setFailure] = useState("");

  const attributes = useQuery({
    queryKey: ["clinical-attributes", product.id],
    queryFn: () => api.clinicalAttributes(product.id),
  });

  const numeric = KINDS.find((k) => k.value === kind)?.numeric ?? false;

  const save = useMutation({
    mutationFn: () =>
      api.saveClinicalAttribute({
        product: product.id,
        kind,
        value_number: numeric ? Number(value) : null,
        value_text: numeric ? "" : value,
        source,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["clinical-attributes", product.id] });
      setValue("");
      setSource("");
      setFailure("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  const columns: Column<ClinicalAttribute>[] = [
    { key: "kind", header: "Attribute", render: (a) => a.kind_label },
    {
      key: "value",
      header: "Value",
      render: (a) => a.value_text || a.value_number?.toLocaleString() || "—",
    },
    { key: "source", header: "Source", render: (a) => a.source },
    { key: "from", header: "From", render: (a) => DAY.format(new Date(a.effective_from)) },
    {
      key: "to",
      header: "Until",
      render: (a) => (a.effective_to ? DAY.format(new Date(a.effective_to)) : "In force"),
    },
  ];

  if (attributes.isPending) return <Skeleton className="h-[200px]" />;

  return (
    <>
      {failure && (
        <Banner tone="bad" className="mb-3">
          {failure}
        </Banner>
      )}
      {/* Says why a source is mandatory. A threshold with no cited origin
          is an opinion, and this system does not hold opinions about
          medicines. */}
      <Banner tone="info" className="mb-3">
        Every value needs a source. Superseded, never edited.
      </Banner>

      <DataTable
        columns={columns}
        rows={attributes.data?.results ?? []}
        rowKey={(a) => a.id}
        density="compact"
        caption="Clinical attributes"
        emptyHeading="Nothing recorded"
        emptyBody="Without these, the counter runs no check on this product."
      />

      <div className="mt-4 flex flex-col gap-4">
        <Field label="Attribute" required>
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Value" required>
          {(id) =>
            numeric ? (
              <Input
                id={id}
                type="number"
                min={0}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="tabular text-right"
              />
            ) : (
              <Input id={id} value={value} onChange={(e) => setValue(e.target.value)} />
            )
          }
        </Field>
        <Field label="Source" help="SmPC, Rwanda FDA, monograph." required>
          {(id) => (
            <Input id={id} value={source} onChange={(e) => setSource(e.target.value)} />
          )}
        </Field>
        <Button
          variant="primary"
          disabled={!value.trim() || !source.trim()}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Record
        </Button>
      </div>
    </>
  );
}
