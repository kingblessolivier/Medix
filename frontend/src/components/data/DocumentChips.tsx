/* Documents, where the work is.
 *
 * There is no document centre. A pharmacist looking for the delivery note
 * for order SO-00042 is looking at SO-00042 — sending them to a separate
 * screen, filtering by date and finding it again is a detour the system
 * invented for its own convenience.
 *
 * So documents appear as chips on the row that produced them, and the
 * chip opens the document. Nothing is filed; everything is attached.
 *
 * The chips are also a status signal. An order with `PO · INV · DN` is
 * further along than one with `PO`, and that reads at a glance from the
 * table without opening anything.
 */

import { useQuery } from "@tanstack/react-query";
import { Download, ExternalLink, FileText } from "lucide-react";

import { Badge, Button } from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import { api, type MedixDocument } from "@/lib/api";
import { useState } from "react";

const WHEN = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Africa/Kigali",
});

/* Short forms the trade already uses. docs/23: abbreviations pharmacists
 * say out loud are fine; never invent one. Anything unlisted falls back
 * to the server's own label rather than an initialism nobody knows. */
const SHORT: Record<string, string> = {
  PROFORMA: "Proforma",
  TAX_INVOICE: "Invoice",
  CREDIT_NOTE: "Credit note",
  PICKING_TICKET: "Pick list",
  DELIVERY_NOTE: "Delivery note",
  GOODS_RECEIPT: "GRN",
  CONTROLLED_TRANSFER: "Controlled",
  WRITE_OFF: "Write-off",
  PURCHASE_ORDER: "PO",
};

const short = (doc: MedixDocument) => SHORT[doc.kind] ?? doc.kind_label;

/** The chips for one transaction. Renders nothing until there is one. */
export function DocumentChips({
  subject,
  label,
}: {
  /** The order, receipt, invoice or sale the documents belong to. */
  subject: string;
  /** Names the transaction in the drawer title. */
  label?: string;
}) {
  const [open, setOpen] = useState(false);

  const documents = useQuery({
    queryKey: ["documents", subject],
    queryFn: () => api.documentsAbout(subject),
    staleTime: 30_000,
  });

  const rows = documents.data?.results ?? [];
  if (documents.isPending) return <span className="text-text-3">·</span>;
  if (rows.length === 0) return <span className="text-text-3">—</span>;

  return (
    <>
      <button
        type="button"
        onClick={(event) => {
          // The row underneath opens the transaction. This opens the paper.
          event.stopPropagation();
          setOpen(true);
        }}
        className="flex flex-wrap items-center gap-1 rounded-sm text-left"
        aria-label={`${rows.length} documents`}
      >
        {rows.slice(0, 3).map((doc) => (
          <span
            key={doc.id}
            className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 text-label text-text-2"
          >
            <FileText size={11} strokeWidth={1.9} aria-hidden />
            {short(doc)}
          </span>
        ))}
        {rows.length > 3 && (
          <span className="text-label text-text-3">+{rows.length - 3}</span>
        )}
      </button>

      {open && (
        <DocumentDrawer
          documents={rows}
          label={label ?? "Documents"}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

/** Every document one transaction produced, newest first. */
export function DocumentDrawer({
  documents,
  label,
  onClose,
}: {
  documents: MedixDocument[];
  label: string;
  onClose: () => void;
}) {
  const [chosen, setChosen] = useState<MedixDocument | null>(null);

  if (chosen) {
    return (
      <DocumentDetail document={chosen} onClose={() => setChosen(null)} />
    );
  }

  return (
    <Modal open title="Documents" subtitle={label} onClose={onClose}>
      <ul className="flex flex-col gap-1">
        {documents.map((doc) => (
          <li key={doc.id}>
            <button
              type="button"
              onClick={() => setChosen(doc)}
              className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-hover"
            >
              <FileText size={16} strokeWidth={1.8} className="text-text-3" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body text-text">{doc.kind_label}</span>
                <span className="block truncate font-mono text-help text-text-2">
                  {doc.number}
                  {doc.version > 1 && ` v${doc.version}`}
                </span>
              </span>
              {doc.supersedes && <Badge tone="warn">Amended</Badge>}
              {!doc.has_pdf && <Badge tone="neutral">HTML only</Badge>}
            </button>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

function DocumentDetail({
  document: doc,
  onClose,
}: {
  document: MedixDocument;
  onClose: () => void;
}) {
  return (
    <Modal
      open
      title={doc.number}
      subtitle={doc.kind_label}
      onClose={onClose}
      footer={
        <div className="flex gap-2">
          <Button
            variant="primary"
            className="flex-1"
            icon={<ExternalLink size={16} strokeWidth={1.9} aria-hidden />}
            onClick={() => window.open(api.documentPreviewUrl(doc.id), "_blank")}
          >
            Open
          </Button>
          {doc.has_pdf && (
            <Button
              variant="secondary"
              icon={<Download size={16} strokeWidth={1.9} aria-hidden />}
              onClick={() => window.open(api.documentPdfUrl(doc.id), "_blank")}
            >
              PDF
            </Button>
          )}
        </div>
      }
    >
      <DetailList
        rows={[
          ["Version", String(doc.version)],
          ["Issued", WHEN.format(new Date(doc.issued_at))],
          ["By", doc.issued_by_name || "—"],
          /* The hash is the proof a reprint is the same document. */
          ["Content hash", doc.sha256.slice(0, 16)],
        ]}
      />

      {doc.supersedes && (
        <p className="mt-4 text-body text-warn-text">
          This version supersedes an earlier one. The original remains readable.
        </p>
      )}

      {!doc.has_pdf && (
        <p className="mt-4 flex items-start gap-2 text-help text-text-2">
          <FileText size={14} strokeWidth={1.9} className="mt-0.5 shrink-0" aria-hidden />
          Rendered as HTML. PDF output is not configured on this deployment.
        </p>
      )}
    </Modal>
  );
}

/** The column, ready to drop into any transaction table. */
export function documentColumn<T extends { id: string; number?: string }>() {
  return {
    key: "documents",
    header: "Documents",
    width: "12rem",
    render: (row: T) => <DocumentChips subject={row.id} label={row.number} />,
  };
}
