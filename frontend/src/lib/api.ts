/* API client.
 *
 * One error shape everywhere — clients branch on `code`, never on
 * `message`. See docs/07-api.md.
 */

const BASE = "/api/v1";

export type ApiError = {
  code: string;
  message: string;
  detail?: string;
  field?: string | null;
  meta?: Record<string, unknown>;
  errors?: { field: string | null; code: string; message: string }[];
};

export class ApiFailure extends Error {
  readonly status: number;
  readonly error: ApiError;

  constructor(status: number, error: ApiError) {
    super(error.message);
    this.name = "ApiFailure";
    this.status = status;
    this.error = error;
  }
}

const TOKEN_KEY = "medix.access";
const REFRESH_KEY = "medix.refresh";

export const tokens = {
  get access() {
    return localStorage.getItem(TOKEN_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh?: string) {
    localStorage.setItem(TOKEN_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

async function parse(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { error: { code: "bad_response", message: "Unexpected response." } };
  }
}

let refreshing: Promise<boolean> | null = null;

async function refreshAccess(): Promise<boolean> {
  // Collapse concurrent 401s into one refresh, so a page loading four
  // queries does not fire four refreshes and rotate the token from under
  // itself.
  if (refreshing) return refreshing;
  const token = tokens.refresh;
  if (!token) return false;

  refreshing = (async () => {
    try {
      const response = await fetch(`${BASE}/auth/token/refresh/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh: token }),
      });
      if (!response.ok) {
        tokens.clear();
        return false;
      }
      const data = (await response.json()) as { access: string; refresh?: string };
      tokens.set(data.access, data.refresh);
      return true;
    } finally {
      refreshing = null;
    }
  })();

  return refreshing;
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  /** Required on anything with a financial or stock effect. */
  idempotencyKey?: string;
  signal?: AbortSignal;
};

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const access = tokens.access;
    if (access) headers.Authorization = `Bearer ${access}`;
    if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

    return fetch(`${BASE}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  };

  let response = await send();

  if (response.status === 401 && tokens.refresh) {
    if (await refreshAccess()) response = await send();
  }

  const payload = await parse(response);

  if (!response.ok) {
    const error =
      payload && typeof payload === "object" && "error" in payload
        ? ((payload as { error: ApiError }).error)
        : { code: "unknown", message: "Something went wrong. Try again." };
    throw new ApiFailure(response.status, error);
  }

  return payload as T;
}

export async function login(username: string, password: string): Promise<void> {
  const response = await fetch(`${BASE}/auth/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new ApiFailure(response.status, {
      code: "invalid_credentials",
      message: "Username or password is incorrect.",
    });
  }
  const data = (await response.json()) as { access: string; refresh: string };
  tokens.set(data.access, data.refresh);
}

export function logout(): void {
  tokens.clear();
}

/* -- resource types --------------------------------------------------- */

export type Paginated<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type Cursored<T> = {
  next: string | null;
  previous: string | null;
  results: T[];
};

export type Me = {
  id: string;
  username: string;
  name: string;
  organization: { id: string; name: string; primary_kind: string } | null;
};

export type StockRow = {
  id: string;
  product: string;
  product_name: string;
  batch: string;
  batch_number: string;
  location: string;
  location_name: string;
  status: string;
  quantity_base: number;
  expiry_date: string;
  days_to_expiry: number;
};

export type Movement = {
  id: string;
  kind: string;
  product_name: string;
  batch_number: string;
  location_name: string;
  quantity_base: number;
  balance_after_base: number;
  reason: string;
  reference: string;
  performed_by_name: string | null;
  occurred_at: string;
};

export type ProductRow = {
  id: string;
  name: string;
  generic_name: string;
  brand: string;
  product_type_code: string;
  category_name: string | null;
  legal_status: string;
  requires_prescription: boolean;
  tax_treatment: string;
  cold_chain: boolean;
  gtin: string;
  is_active: boolean;
};

export type UnitOfMeasureRow = {
  id: string;
  code: string;
  name: string;
  factor_to_base: number;
  is_base: boolean;
  is_purchase_default: boolean;
  is_dispense_default: boolean;
  is_sellable: boolean;
};

/** Product detail — carries the packaging chain the list view omits. */
export type ProductDetail = ProductRow & {
  units: UnitOfMeasureRow[];
  strength: string;
  dosage_form: string;
  route: string;
};

export type SaleLine = {
  id: string;
  product_name: string;
  batch_number: string;
  expiry_date: string;
  uom_code: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  tax_treatment: string;
  tax_amount: number;
  legal_status: string;
  requires_prescription: boolean;
};

export type SalePayment = {
  id: string;
  method: string;
  amount: number;
  status: string;
  provider_reference: string;
};

export type Sale = {
  id: string;
  number: string;
  status: string;
  subtotal: number;
  tax_total: number;
  total: number;
  outstanding: number;
  /** What stops this sale completing, in the words the POS shows. */
  blocked_reason: string | null;
  lines: SaleLine[];
  payments: SalePayment[];
};

export type Location = { id: string; name: string; code: string };

/** A level the depot will sell at, with the price restated for it. */
export type SellableUnit = {
  code: string;
  name: string;
  factor_to_base: number;
  price: number;
  /** The level the depot actually listed; the rest are derived. */
  is_priced: boolean;
};

export type MarketplaceRow = {
  id: string;
  product: string;
  product_name: string;
  generic_name: string;
  brand: string;
  category_name: string | null;
  product_type_code: string;
  /** Tablet, bottle, vial, pair — the base unit's name. */
  dosage_form: string;
  vendor: string;
  vendor_name: string;
  availability: string;
  is_orderable: boolean;
  price: number;
  currency: string;
  uom_code: string;
  uom_name: string;
  moq: number;
  lead_time_days: number;
  legal_status: string;
  requires_prescription: boolean;
  cold_chain: boolean;
  /** What the depot published, less what is committed. Not its stock. */
  available_base: number;
  earliest_expiry: string | null;
  units: SellableUnit[];
  /** Suggested retail price per `uom_code`. A starting point, not a rule. */
  srp: number | null;
  /** Volume breaks, thresholds in `uom_code`. Ascending. */
  tiers: { min_quantity: number; price: number }[];
};


/** Counts over the whole result set, not the page the screen holds. */
export type MarketplaceFacets = {
  total: number;
  types: { code: string; count: number }[];
  categories: { name: string; count: number }[];
};

export type OrderLine = {
  id: string;
  product: string;
  product_name: string;
  uom_code: string;
  quantity: number;
  quantity_base: number;
  unit_price: number;
  line_total: number;
  received_base: number;
  outstanding_base: number;
  dispatched_base: number;
  undispatched_base: number;
};

/* -- documents --------------------------------------------------------- */

/* Named MedixDocument because `Document` is the DOM global, and shadowing
   it in a file this widely imported is how someone loses an afternoon. */
export type MedixDocument = {
  id: string;
  kind: string;
  kind_label: string;
  number: string;
  version: number;
  subject_type: string;
  subject_id: string;
  issued_at: string;
  issued_by_name: string;
  sha256: string;
  has_pdf: boolean;
  supersedes: string | null;
};

/* -- finance ----------------------------------------------------------- */

export type CategoryTotal = { code: string; name: string; amount: number };

/* Money in minor units, ratios in basis points. No `net_profit` field —
   see docs/28 §12.3; the estimate carries its own basis instead. */
export type PeriodReport = {
  organization_id: string;
  tier: "DEPOT" | "RETAIL";
  start: string;
  end: string;
  currency: string;
  capital_invested: number;
  revenue: number;
  cogs: number;
  gross_profit: number;
  /** Null when there was no revenue — not the same as a zero margin. */
  gross_margin_bp: number | null;
  expenses_total: number;
  expenses: CategoryTotal[];
  estimated_operating_result: number;
  estimated_basis: string;
  write_offs: number;
  stock_at_risk: number;
  roi_bp: number | null;
  cash_revenue: number;
  insurance_revenue: number;
};

export type DashboardPayload = {
  report: PeriodReport;
  trend: { period: string; invested: number; revenue: number }[];
  inventory_health: { band: string; stable: number; slow: number; expiring: number }[];
  revenue_by_category: { category: string; amount: number }[];
  cash: { period: string; invoiced: number; collected: number }[];
};

export type ReceivablesAgeing = {
  as_of: string;
  buckets: Record<string, number>;
  total: number;
  customers: (Record<string, number | string> & { customer: string; total: number })[];
};

export type ExpenseCategory = {
  id: string;
  code: string;
  name: string;
  is_operating: boolean;
  is_active: boolean;
};

export type Expense = {
  id: string;
  category: string;
  category_name: string;
  incurred_on: string;
  amount: number;
  currency: string;
  description: string;
  payee: string;
  reference: string;
};

export type WriteOff = {
  id: string;
  number: string;
  batch: string;
  batch_number: string;
  product_name: string;
  reason: string;
  reason_label: string;
  quantity_base: number;
  unit_cost_base: number;
  value: number;
  currency: string;
  written_off_on: string;
  witness_name: string;
  witness_role: string;
};

/* Severity is behaviour, not decoration: CRITICAL means the request was
   refused, WARNING means it will be refused until the code comes back in
   `acknowledged`, INFO never interrupts. See docs/29-alerts.md. */
export type Alert = {
  code: string;
  severity: "CRITICAL" | "WARNING" | "INFO";
  title: string;
  detail: string;
  subject_type: string;
  subject_id: string;
  meta: Record<string, unknown>;
};

export type AlertSummary = {
  visible: Alert[];
  /** How many the fatigue rule folded away. */
  collapsed: number;
  counts: Record<string, number>;
};

/* One step of the order's history. Both parties read the same rows —
   this is the sanitised half of the audit trail, not the internal one. */
export type OrderEvent = {
  id: string;
  from_status: string;
  to_status: string;
  to_status_label: string;
  actor_name: string;
  actor_organization: string | null;
  actor_organization_name: string;
  occurred_at: string;
  note: string;
  document_number: string;
};

export type PurchaseOrder = {
  id: string;
  number: string;
  status: string;
  supplier: string;
  supplier_name: string;
  buyer_name: string;
  deliver_to_name: string;
  required_by: string | null;
  subtotal: number;
  currency: string;
  payment_terms_days: number;
  submitted_at: string | null;
  confirmed_at: string | null;
  created_at: string;
  lines: OrderLine[];
  events: OrderEvent[];
};

export type ShipmentLine = {
  id: string;
  order_line: string;
  product: string;
  product_name: string;
  uom_code: string;
  quantity_base: number;
  batch_number: string;
  expiry_date: string;
};

/** The supplier's delivery note — what the receiver checks cartons against. */
export type Shipment = {
  id: string;
  number: string;
  status: string;
  order: string;
  order_number: string;
  from_location: string;
  from_location_name: string;
  carrier: string;
  dispatched_at: string | null;
  lines: ShipmentLine[];
};

export type ReceiptLine = {
  id: string;
  product: string;
  product_name: string;
  uom_code: string;
  ordered: number;
  received: number;
  accepted: number;
  rejected: number;
  rejection_reason: string;
  is_short: boolean;
  batch_number: string;
  expiry_date: string;
  unit_cost_base: number;
  landed_cost_share: number;
  gtin: string;
  /** The order line this fulfils, when the receipt was raised against one. */
  order_line: string | null;
};

/** One rung of a mixed-unit count: "2 cartons", "5 packs". */
export type QuantityEntry = { uom_code: string; count: number };

export type LandedCost = {
  invoice_number?: string;
  invoice_currency?: string;
  /** RWF per one unit of invoice_currency, x10,000. Never a float. */
  fx_rate_scaled?: number;
  fx_rate_date?: string | null;
  fx_rate_is_official?: boolean;
  freight?: number;
  customs_duty?: number;
  clearing_fees?: number;
  other_charges?: number;
};

export type GoodsReceipt = {
  id: string;
  number: string;
  status: string;
  order: string | null;
  supplier: string | null;
  supplier_name: string | null;
  location: string;
  location_name: string;
  received_on: string;
  posted_at: string | null;
  transport_temperature_ok: boolean;
  has_discrepancy: boolean;
  invoice_number: string;
  invoice_currency: string;
  fx_rate_scaled: number;
  fx_rate_date: string | null;
  fx_rate_is_official: boolean;
  freight: number;
  customs_duty: number;
  clearing_fees: number;
  other_charges: number;
  landed_charges: number;
  /** Delivery note this receipt was seeded from, if the supplier sent one. */
  transfer_id: string;
  lines: ReceiptLine[];
};

/** What differed from the order — the reason the document exists. */
export type Discrepancy = {
  product: string;
  ordered: number;
  received: number;
  accepted: number;
  rejected: number;
  short_by: number;
  reason: string;
};

/** Derived from held licences — decides which navigation to show. */
export type Capabilities = {
  capabilities: string[];
  licences: {
    kind: string;
    number: string;
    expiry: string;
    status: string;
    is_valid: boolean;
  }[];
};

export const api = {
  me: () => request<Me>("/auth/me/"),
  stock: (params = "") => request<Paginated<StockRow>>(`/stock/${params}`),
  movements: (params = "") => request<Cursored<Movement>>(`/stock-movements/${params}`),
  products: (params = "") => request<Paginated<ProductRow>>(`/products/${params}`),
  product: (id: string) => request<ProductDetail>(`/products/${id}/`),
  locations: () => request<Paginated<Location>>("/locations/"),

  sale: (id: string) => request<Sale>(`/sales/${id}/`),
  startSale: (location: string) =>
    request<Sale>("/sales/", { method: "POST", body: { location } }),
  addLine: (
    id: string,
    line: { product: string; quantity: number; uom_code: string; unit_price: number },
  ) => request<Sale>(`/sales/${id}/lines/`, { method: "POST", body: line }),
  completeSale: (id: string) =>
    request<Sale>(`/sales/${id}/complete/`, {
      method: "POST",
      body: {},
      // Goods and money both move here, so a retry must not double-apply.
      idempotencyKey: crypto.randomUUID(),
    }),
  takePayment: (id: string, body: { method: string; amount: number }) =>
    request<Sale>(`/sales/${id}/payments/`, { method: "POST", body }),

  capabilities: () => request<Capabilities>("/capabilities/"),
  marketplace: (params = "") =>
    request<Paginated<MarketplaceRow>>(`/marketplace/${params}`),

  marketplaceFacets: (params = "") =>
    request<MarketplaceFacets>(`/marketplace/facets/${params}`),

  orders: () => request<Paginated<PurchaseOrder>>("/purchase-orders/"),
  order: (id: string) => request<PurchaseOrder>(`/purchase-orders/${id}/`),
  fulfilment: () => request<Paginated<PurchaseOrder>>("/purchase-orders/fulfilment/"),
  startOrder: (body: { supplier: string; deliver_to: string }) =>
    request<PurchaseOrder>("/purchase-orders/", { method: "POST", body }),
  /* The open draft for this supplier, opened if there isn't one — so
     adding from the marketplace builds one order, not one per click. */
  openDraft: (body: { supplier: string; deliver_to: string }) =>
    request<PurchaseOrder>("/purchase-orders/draft/", { method: "POST", body }),
  addOrderLine: (
    id: string,
    body: { listing: string; quantity: number; uom_code?: string },
  ) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/lines/`, { method: "POST", body }),
  submitOrder: (id: string) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/submit/`, { method: "POST", body: {} }),
  /* Warnings come back 422 with their codes. Present them, collect the
     acknowledgement, and retry naming the codes accepted — never a bare
     "yes", so a check added tomorrow is not pre-accepted by today's
     client. */
  confirmOrder: (id: string, body: { acknowledged?: string[]; reason?: string } = {}) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/confirm/`, { method: "POST", body }),

  alerts: (scope = "inventory") =>
    request<AlertSummary>(`/alerts/?scope=${encodeURIComponent(scope)}`),

  dispatchOrder: (id: string, body: { from_location: string; carrier?: string }) =>
    request<Shipment>(`/purchase-orders/${id}/dispatch_order/`, { method: "POST", body }),
  shipments: (id: string) => request<Shipment[]>(`/purchase-orders/${id}/shipments/`),

  receipts: () => request<Paginated<GoodsReceipt>>("/goods-receipts/"),
  /** The draft a supplier's advance notice already seeded, if any. */
  draftReceiptFor: (orderId: string) =>
    request<Paginated<GoodsReceipt>>(
      `/goods-receipts/?order=${orderId}&status=DRAFT`,
    ),
  startReceipt: (body: { location: string; order?: string; supplier?: string }) =>
    request<GoodsReceipt>("/goods-receipts/", { method: "POST", body }),
  addReceiptLine: (
    id: string,
    body: {
      product: string;
      uom_code: string;
      received?: number;
      /** Instead of `received`: a count across several levels at once. */
      entries?: QuantityEntry[];
      accepted?: number;
      rejected?: number;
      rejection_reason?: string;
      batch_number: string;
      expiry_date: string;
      unit_cost_base?: number;
      order_line?: string;
    },
  ) => request<GoodsReceipt>(`/goods-receipts/${id}/lines/`, { method: "POST", body }),
  /** Clears a draft's seeded lines so the receiver's count replaces them. */
  resetReceiptLines: (id: string) =>
    request<GoodsReceipt>(`/goods-receipts/${id}/reset-lines/`, {
      method: "POST",
      body: {},
    }),
  setLandedCost: (id: string, body: LandedCost) =>
    request<GoodsReceipt>(`/goods-receipts/${id}/landed-cost/`, { method: "POST", body }),
  landedCostPreview: (id: string) =>
    request<Record<string, number>>(`/goods-receipts/${id}/landed-cost-preview/`),
  postReceipt: (id: string) =>
    request<GoodsReceipt>(`/goods-receipts/${id}/post_receipt/`, { method: "POST", body: {} }),
  discrepancies: (id: string) =>
    request<Discrepancy[]>(`/goods-receipts/${id}/discrepancies/`),

  /* -- documents ------------------------------------------------------- */

  documents: (params = "") => request<Paginated<MedixDocument>>(`/documents/${params}`),
  documentsFor: (subjectId: string) =>
    request<Paginated<MedixDocument>>(`/documents/?subject=${subjectId}`),
  /** The stored HTML, not a re-render — preview and print cannot diverge. */
  documentPreviewUrl: (id: string) => `${BASE}/documents/${id}/preview/`,
  documentPdfUrl: (id: string) => `${BASE}/documents/${id}/pdf/`,

  /* -- finance --------------------------------------------------------- */

  financeDashboard: (params: { start: string; end: string; tier: string }) =>
    request<DashboardPayload>(
      `/finance/dashboard/?start=${params.start}&end=${params.end}&tier=${params.tier}`,
    ),
  financePeriod: (params: { start: string; end: string; tier: string }) =>
    request<PeriodReport>(
      `/finance/period/?start=${params.start}&end=${params.end}&tier=${params.tier}`,
    ),
  receivables: () => request<ReceivablesAgeing>("/finance/receivables/"),

  expenseCategories: () => request<Paginated<ExpenseCategory>>("/expense-categories/"),
  expenses: (params = "") => request<Paginated<Expense>>(`/expenses/${params}`),
  recordExpense: (body: {
    category: string;
    amount: number;
    incurred_on?: string;
    description?: string;
    payee?: string;
    reference?: string;
  }) => request<Expense>("/expenses/", { method: "POST", body }),

  writeOffs: () => request<Paginated<WriteOff>>("/write-offs/"),
  recordWriteOff: (body: {
    batch: string;
    location: string;
    quantity: number;
    uom_code?: string;
    reason: string;
    witness_name?: string;
    witness_role?: string;
    written_off_on?: string;
  }) => request<WriteOff>("/write-offs/", { method: "POST", body }),
};
