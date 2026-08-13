/* Everything this pharmacy has issued.
 *
 * There is no "create document" action, deliberately. Documents are
 * raised by the workflow that owns the event — dispatching raises the
 * delivery note, posting a receipt raises the GRN — because a document
 * nobody's workflow produced is a document nobody is accountable for.
 *
 * The preview serves the **stored** HTML, the same bytes the PDF was
 * rendered from. docs/18 treats a divergence between preview and print
 * as a bug rather than a variation, and re-rendering here would
 * guarantee one the moment a product is renamed.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, ExternalLink, FileText } from "lucide-react";
import {
  DataTable,
  TableTabs,
  type Column,
  type TableTab,
} from "@/components/data/DataTable";
import { Badge, Button, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import { api, type MedixDocument } from "@/lib/api";

const WHEN = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Africa/Kigali",
});

const VIEWS: { id: string; label: string; kinds?: string[] }[] = [
  { id: "all", label: "All" },
  { id: "outbound", label: "Dispatch", kinds: ["DELIVERY_NOTE", "PICKING_TICKET"] },
  { id: "money", label: "Invoices", kinds: ["TAX_INVOICE", "PROFORMA", "CREDIT_NOTE"] },
  { id: "inbound", label: "Receiving", kinds: ["GOODS_RECEIPT"] },
  {
    id: "regulated",
    label: "Regulated",
    kinds: ["CONTROLLED_TRANSFER", "WRITE_OFF"],
  },
];

const COLUMNS: Column<MedixDocument>[] = [
  {
    key: "number",
    header: "Number",
    render: (d) => (
      <span className="font-mono text-body">
        {d.number}
        {d.version > 1 && <span className="ml-1 text-text-3">v{d.version}</span>}
      </span>
    ),
  },
  { key: "kind", header: "Type", render: (d) => d.kind_label },
  {
    key: "issued",
    header: "Issued",
    render: (d) => WHEN.format(new Date(d.issued_at)),
  },
  { key: "by", header: "By", render: (d) => d.issued_by_name || "—" },
  {
    key: "state",
    header: "",
    render: (d) =>
      d.supersedes ? <Badge tone="warn">Amended</Badge> : d.has_pdf ? null : (
        <Badge tone="neutral">HTML only</Badge>
      ),
  },
];

export function DocumentsScreen() {
  const [view, setView] = useState("all");
  const [selected, setSelected] = useState<MedixDocument | null>(null);

  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.documents(),
  });

  if (documents.isPending) return <Skeleton className="h-[400px]" />;
  if (documents.isError) {
    return (
      <ErrorState
        message="Could not load documents."
        onRetry={() => documents.refetch()}
      />
    );
  }

  const all = documents.data.results;
  const chosen = VIEWS.find((v) => v.id === view);
  const rows = chosen?.kinds
    ? all.filter((d) => chosen.kinds!.includes(d.kind))
    : all;

  const tabs: TableTab[] = VIEWS.map((v) => ({
    id: v.id,
    label: v.label,
    count: v.kinds ? all.filter((d) => v.kinds!.includes(d.kind)).length : all.length,
  }));

  return (
    <>
      <PageHeader title="Documents" description="Issued by this pharmacy." />
      <TableTabs tabs={tabs} active={view} onChange={setView} />
      <DataTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(d) => d.id}
        density="compact"
        onRowClick={setSelected}
        caption="Issued documents"
        emptyHeading="No documents"
        emptyBody="Documents appear as orders are dispatched and received."
      />
      <DocumentModal document={selected} onClose={() => setSelected(null)} />
    </>
  );
}

function DocumentModal({
  document: doc,
  onClose,
}: {
  document: MedixDocument | null;
  onClose: () => void;
}) {
  if (!doc) return null;

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
            icon={<ExternalLink size={16} strokeWidth={1.9} />}
            onClick={() => window.open(api.documentPreviewUrl(doc.id), "_blank")}
          >
            Open
          </Button>
          {doc.has_pdf && (
            <Button
              variant="secondary"
              icon={<Download size={16} strokeWidth={1.9} />}
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
          ["About", doc.subject_type.split(".").pop() ?? "—"],
          /* The hash is the proof the reprint is the same document.
             Shown in full rather than truncated: a partial hash cannot
             be checked against anything. */
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
          <FileText size={14} strokeWidth={1.9} className="mt-0.5 shrink-0" />
          Rendered as HTML. PDF output is not configured on this deployment.
        </p>
      )}
    </Modal>
  );
}
