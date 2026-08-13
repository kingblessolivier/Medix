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

/** A document's own bytes — HTML or PDF, never JSON.
 *
 * Separate from `request` because that helper parses every response as
 * JSON and would mangle both. Retries once through the refresh flow for
 * the same reason `request` does: a preview opened on a stale token
 * should not read as a missing document.
 */
async function fetchDocument(path: string, as: "text"): Promise<string>;
async function fetchDocument(path: string, as: "blob"): Promise<Blob>;
async function fetchDocument(path: string, as: "text" | "blob"): Promise<string | Blob> {
  const send = () => {
    const headers: Record<string, string> = {};
    const access = tokens.access;
    if (access) headers.Authorization = `Bearer ${access}`;
    return fetch(`${BASE}${path}`, { headers });
  };

  let response = await send();
  if (response.status === 401 && tokens.refresh) {
    if (await refreshAccess()) response = await send();
  }

  if (!response.ok) {
    throw new ApiFailure(response.status, {
      code: response.status === 404 ? "not_rendered" : "unavailable",
      message:
        response.status === 404
          ? "This document has no PDF on this deployment."
          : "Could not open the document.",
    });
  }

  return as === "text" ? response.text() : response.blob();
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
  /** On the shelf now, in base units. Available only — not quarantined. */
  on_hand_base: number;
  /** Names the unit the figure is in. "0" is not an answer on its own. */
  base_uom_name: string;
};

export type ProductType = {
  id: string;
  code: string;
  name: string;
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

export type Location = {
  id: string;
  name: string;
  code: string;
  kind?: string;
  /** AMBIENT, COLD, FROZEN. What may be stored here at all. */
  temperature_class?: string;
  is_cold_capable?: boolean;
};

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
  /** The pack picture. Verification, never identification. */
  image: string | null;
  image_alt: string;
  manufacturer_name: string | null;
  gtin: string;
  registration_number: string | null;
  /** "Pack of 100 capsules — 100 capsule". How the pack is spoken about. */
  pack_size: string;
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

/* -- pharmacies, catalogue admin, clinical ----------------------------- */

/** A pharmacy this depot registered, and where it stands. */
export type Pharmacy = {
  id: string;
  name: string;
  tin: string;
  primary_kind: string;
  licence_number: string;
  licence_expiry: string | null;
  licence_valid: boolean;
  is_verified: boolean;
  is_active: boolean;
  credit_limit: number;
  payment_terms_days: number;
  outstanding: number;
};

/** Returned once, on registration. Never retrievable again. */
export type RegisteredPharmacy = {
  organization: { id: string; name: string; primary_kind: string; tin: string };
  licence: { number: string; kind: string; expiry: string };
  administrator: { username: string; email: string };
  temporary_password: string;
  relationship: string | null;
};

export type Manufacturer = {
  id: string;
  name: string;
  country_of_origin: string;
  gmp_certified: boolean;
  gmp_expiry: string | null;
  is_active: boolean;
  product_count: number;
};

export type Category = { id: string; name: string };

export type PatientAllergy = {
  id: string;
  patient: string;
  allergen: string;
  severity: string;
  severity_label: string;
  note: string;
  recorded_on: string;
};

export type Patient = {
  id: string;
  full_name: string;
  address: string;
  phone: string;
  national_id: string;
  date_of_birth: string | null;
  age_years: number | null;
  sex: string;
  is_pregnant: boolean | null;
  allergies: PatientAllergy[];
};

export type Prescriber = {
  id: string;
  full_name: string;
  council_number: string;
  facility: string;
};

export type Prescription = {
  id: string;
  number: string;
  patient: Patient;
  prescriber: string | null;
  issued_on: string | null;
  status: string;
  is_verified: boolean;
  verified_at: string | null;
  verified_by_council_number: string;
};

/** A threshold, with the dates it applied between. */
export type AlertRule = {
  id: string;
  code: string;
  severity: string;
  threshold: Record<string, number>;
  is_active: boolean;
  effective_from: string;
  effective_to: string | null;
};

export type ControlledQuota = {
  id: string;
  schedule: string;
  period: string;
  limit_base: number;
  authority_reference: string;
  effective_from: string;
  effective_to: string | null;
};

export type TaxRule = {
  id: string;
  treatment: string;
  rate_basis_points: number;
  effective_from: string;
  effective_to: string | null;
};

/** Everywhere a batch went — the question a recall actually asks. */
export type BatchTrace = {
  batch: string;
  product: string;
  expiry_date: string;
  /** Where the rest of it is sitting. A total says how much, not where. */
  locations: {
    location: string;
    branch: string;
    status: string;
    quantity_base: number;
  }[];
  patients: {
    sale: string;
    occurred_at: string;
    patient: string;
    phone: string;
    quantity_base: number;
  }[];
  customers: {
    delivery_note: string;
    dispatched_at: string | null;
    customer: string;
    quantity_base: number;
  }[];
  dispensed_base: number;
  dispatched_base: number;
  on_hand_base: number;
};

export type SchemeContract = {
  id: string;
  scheme: string;
  scheme_name: string;
  reference: string;
  /** FEE_FOR_SERVICE claims per sale; CAPITATION is paid per period. */
  model: string;
  model_label: string;
  claims_per_sale: boolean;
  is_contracted: boolean;
  claim_window_days: number;
  payment_terms_days: number;
  capitation_amount: number | null;
  capitation_period: string;
  effective_from: string;
  effective_to: string | null;
};

export type CoverageRule = {
  id: string;
  contract: string;
  /** What the rule applies to: everything, a category, one product. */
  scope: string;
  scope_label: string;
  product: string | null;
  product_name: string;
  category: string | null;
  category_name: string;
  legal_status: string;
  /** Basis points, never a float. 8000 is 80%. */
  coverage_basis_points: number;
  maximum_amount: number | null;
  is_excluded: boolean;
  requires_prescription: boolean;
  effective_from: string;
  effective_to: string | null;
};

export type Member = {
  id: string;
  patient: string;
  patient_name: string;
  scheme: string;
  scheme_name: string;
  member_number: string;
  principal_name: string;
  valid_from: string | null;
  valid_to: string | null;
  is_active: boolean;
  is_currently_valid: boolean;
};

export type ImportDocument = {
  id: string;
  kind: string;
  kind_label: string;
  receipt: string;
  batch: string | null;
  batch_number: string;
  number: string;
  issued_by: string;
  issued_on: string | null;
  expires_on: string | null;
  file: string | null;
  /** Somebody looked at it and says it is what it claims to be. */
  is_verified: boolean;
  verified_at: string | null;
  min_temperature_c: string | null;
  max_temperature_c: string | null;
  /** A recorded breach quarantines the consignment rather than warning. */
  breach: boolean;
};

/* -- tills, shifts and day end ------------------------------------------ */

export type Till = {
  id: string;
  name: string;
  code: string;
  branch: string;
  is_active: boolean;
};

export type Shift = {
  id: string;
  till: string;
  till_name: string;
  status: string;
  opening_float: number;
  counted_cash: number | null;
  variance: number | null;
  opened_at: string;
  closed_at: string | null;
};

/** The X report while open, the Z report once closed. */
export type DayEnd = {
  sales_total: number;
  transactions: number;
  items_sold: number;
  discounts: number;
  tax_total: number;
  /** Settled money only. A pending request-to-pay is not in the till. */
  by_method: Record<string, number>;
  expected_cash: number;
  counted_cash: number | null;
  variance: number | null;
  pending_payments: number;
};

/* -- licences and registrations ----------------------------------------- */

export type Licence = {
  id: string;
  branch: string;
  branch_name: string;
  kind: string;
  number: string;
  issued_on: string;
  expiry: string;
  status: string;
  issuing_authority: string;
  /** Active and not past its expiry. Capability follows this. */
  is_valid: boolean;
  days_to_expiry: number;
};

export type Registration = {
  id: string;
  user: string;
  user_name: string;
  council_number: string;
  issued_on: string;
  expiry: string;
  status: string;
  /** Without one, nobody in this pharmacy can verify a prescription. */
  is_valid: boolean;
  days_to_expiry: number;
};

export type Colleague = {
  id: string;
  username: string;
  name: string;
};

export type Branch = {
  id: string;
  name: string;
  code: string;
  is_active: boolean;
};

/* -- the Assistant ------------------------------------------------------ */

export type Answer = {
  intent: string;
  /** One line, stating the finding. */
  headline: string;
  columns: string[];
  rows: Record<string, string>[];
  /** The screen that can act on this. */
  screen: string;
  /** Never performed. A person confirms it, or it lapses. */
  proposal: {
    id: string;
    action: string;
    effect: string;
    expires_at: string;
  } | null;
  /** Where a figure needs qualifying — an estimate, a partial period. */
  note: string;
};

export type Proposal = {
  id: string;
  question: string;
  action: string;
  effect: string;
  status: string;
  result: Record<string, unknown> | null;
  error: string;
  expires_at: string;
  decided_at: string | null;
  is_open: boolean;
  created_at: string;
};

/* -- cold chain --------------------------------------------------------- */

export type Excursion = {
  id: string;
  sensor: string;
  sensor_name: string;
  location_name: string;
  started_at: string;
  ended_at: string | null;
  is_open: boolean;
  duration_minutes: number;
  peak_celsius: string;
  minimum_celsius: string;
  reading_count: number;
  /** Base units held automatically when this opened. */
  quarantined_base: number;
  batches_affected: number;
  resolved_at: string | null;
  resolution: string;
};

export type Sensor = {
  id: string;
  location: string;
  location_name: string;
  device_code: string;
  name: string;
  minimum_c: string;
  maximum_c: string;
  is_active: boolean;
  last_seen_at: string | null;
};

/** One thing found, and the screen that opens it. */
export type SearchHit = {
  kind: string;
  id: string;
  title: string;
  subtitle: string;
  screen: string;
};

export type ProductImage = {
  id: string;
  product: string;
  image: string;
  alt: string;
  is_primary: boolean;
  position: number;
};

export type ProductRegistration = {
  id: string;
  product: string;
  registration_number: string;
  holder: string;
  manufacturer: string;
  manufacturer_country: string;
  registration_expiry: string | null;
  status: string;
};

export type ClinicalAttribute = {
  id: string;
  product: string;
  kind: string;
  kind_label: string;
  value_number: number | null;
  value_text: string;
  source: string;
  source_reference: string;
  effective_from: string;
  effective_to: string | null;
};

/** Licences and registrations, read live rather than from a status column. */
export type ComplianceState = {
  as_of: string;
  licences: {
    id: string;
    kind: string;
    kind_label: string;
    number: string;
    expiry: string;
    days_remaining: number;
    status: string;
    is_valid: boolean;
  }[];
  registrations: {
    id: string;
    name: string;
    council_number: string;
    expiry: string;
    days_remaining: number;
    status: string;
    is_valid: boolean;
  }[];
  alerts: Alert[];
};

export type MarginRow = {
  key: string;
  label: string;
  revenue: number;
  cogs: number;
  gross_profit: number;
  /** Null when there was no revenue — not the same as a zero margin. */
  margin_bp: number | null;
};

export type IntelligenceReport = {
  start: string;
  end: string;
  by_category: MarginRow[];
  by_product: MarginRow[];
  best_sellers: {
    product: string;
    name: string;
    units: number;
    revenue: number;
    sales: number;
  }[];
  slow_movers: {
    product: string;
    name: string;
    on_hand: number;
    sold: number;
    value: number;
    /** Null means it did not sell at all in the period. */
    cover_days: number | null;
  }[];
  stock_outs: { product: string; name: string; sold: number; on_hand: number }[];
};

/* -- insurance --------------------------------------------------------- */

/** Whether cover applies, and if not, exactly why. The reason is three
    different conversations at the counter, not one refusal. */
export type Eligibility = {
  covered: boolean;
  reason: string;
  member_number: string;
  scheme: string;
  /** FEE_FOR_SERVICE raises a claim per sale; CAPITATION raises none. */
  model: string;
  contract: string | null;
};

export type SaleCover = {
  covered: boolean;
  reason: string;
  model: string;
  gross: number;
  scheme_amount: number;
  patient_amount: number;
  eligibility: Eligibility;
  lines: {
    sale_line: string;
    product: string;
    gross: number;
    covered: number;
    patient: number;
    coverage_basis_points: number;
    note: string;
  }[];
};

export type ClaimLine = {
  id: string;
  sale_line: string;
  product_name: string;
  gross_amount: number;
  covered_amount: number;
  patient_amount: number;
  coverage_basis_points: number;
  allowed_amount: number;
  is_rejected: boolean;
  rejection_reason: string;
};

export type Claim = {
  id: string;
  number: string;
  scheme: string;
  scheme_name: string;
  member_number: string;
  patient_name: string;
  sale: string;
  sale_number: string;
  status: string;
  status_label: string;
  claimed_amount: number;
  allowed_amount: number;
  patient_paid: number;
  settled: number;
  outstanding: number;
  currency: string;
  dispensed_on: string;
  submit_by: string | null;
  submitted_at: string | null;
  responded_at: string | null;
  rejection_reason: string;
  scheme_reference: string;
  lines: ClaimLine[];
  payments: { id: string; amount: number; received_on: string; remittance_reference: string }[];
};

export type SchemeReceivables = {
  as_of: string;
  buckets: Record<string, number>;
  total: number;
  /** Counted apart from the ageing: refused is not late. */
  rejected_total: number;
  schemes: (Record<string, number | string> & {
    scheme: string;
    outstanding: number;
    rejected: number;
  })[];
};

export type Scheme = { id: string; name: string; code: string; is_active: boolean };

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

/* An interaction check that did not run is not a clean one. `state` is
   NOT_AVAILABLE when no clinical dataset is licensed, and the counter
   prints `notice` rather than showing nothing. */
export type ClinicalReview = AlertSummary & {
  patient_known: boolean;
  interactions: {
    state: "CLEAR" | "FOUND" | "NOT_AVAILABLE";
    provider: string;
    dataset_version: string;
    alerts: Alert[];
  };
  interaction_notice: string;
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

  sales: () => request<Paginated<Sale>>("/sales/"),
  sale: (id: string) => request<Sale>(`/sales/${id}/`),
  /* The till matters: the server resolves the open shift from it, and a
     sale started without one belongs to no day. */
  startSale: (location: string, till?: string | null) =>
    request<Sale>("/sales/", {
      method: "POST",
      body: till ? { location, till } : { location },
    }),
  addLine: (
    id: string,
    line: { product: string; quantity: number; uom_code: string; unit_price: number },
  ) => request<Sale>(`/sales/${id}/lines/`, { method: "POST", body: line }),
  /* What the pharmacist must see before completing. Interaction state is
     reported separately from the alerts, because NOT_AVAILABLE and an
     empty list are different answers. */
  saleClinical: (id: string) => request<ClinicalReview>(`/sales/${id}/clinical/`),

  completeSale: (
    id: string,
    body: { acknowledged?: string[]; clinical_reason?: string } = {},
  ) =>
    request<Sale>(`/sales/${id}/complete/`, {
      method: "POST",
      body,
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
  openDraft: (body: { supplier: string; deliver_to: string }) =>
    request<PurchaseOrder>("/purchase-orders/draft/", { method: "POST", body }),
  addOrderLine: (
    id: string,
    body: { listing: string; quantity: number; uom_code?: string },
  ) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/lines/`, { method: "POST", body }),
  /* The buyer's own two steps. A pharmacist raises and sends; somebody
     who can commit money releases. Both are the buying pharmacy — the
     depot sees nothing until the second one happens. */
  requestApproval: (id: string) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/request-approval/`, {
      method: "POST",
      body: {},
    }),
  submitOrder: (id: string) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/submit/`, { method: "POST", body: {} }),
  rejectOrder: (id: string, reason: string) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/reject/`, {
      method: "POST",
      body: { reason },
    }),
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
  postReceipt: (id: string) =>
    request<GoodsReceipt>(`/goods-receipts/${id}/post_receipt/`, { method: "POST", body: {} }),
  discrepancies: (id: string) =>
    request<Discrepancy[]>(`/goods-receipts/${id}/discrepancies/`),

  /* -- documents ------------------------------------------------------- */

  documents: (params = "") => request<Paginated<MedixDocument>>(`/documents/${params}`),
  documentsAbout: (transactionId: string) =>
    request<Paginated<MedixDocument>>(`/documents/?related=${transactionId}`),
  /** The stored HTML, not a re-render — preview and print cannot diverge. */
  /* Fetched with the token rather than navigated to. A plain
     `window.open` carries no Authorization header, so the API answered
     401 and the tab showed a JSON error instead of the document. */
  documentHtml: (id: string) => fetchDocument(`/documents/${id}/preview/`, "text"),
  documentPdf: (id: string) => fetchDocument(`/documents/${id}/pdf/`, "blob"),

  /* -- finance --------------------------------------------------------- */

  financeDashboard: (params: { start: string; end: string; tier: string }) =>
    request<DashboardPayload>(
      `/finance/dashboard/?start=${params.start}&end=${params.end}&tier=${params.tier}`,
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

  /* -- pharmacies ------------------------------------------------------ */

  pharmacies: () => request<Pharmacy[]>("/pharmacies/"),

  compliance: () => request<ComplianceState>("/compliance/"),

  /* -- insurance ------------------------------------------------------- */

  schemes: () => request<Paginated<Scheme>>("/schemes/"),
  eligibility: (patientId: string) =>
    request<Eligibility>(`/eligibility/?patient=${patientId}`),
  saleCover: (saleId: string) => request<SaleCover>(`/sales/${saleId}/cover/`),
  claims: () => request<Paginated<Claim>>("/claims/"),
  submitClaim: (id: string) =>
    request<Claim>(`/claims/${id}/submit/`, { method: "POST", body: {} }),
  respondToClaim: (
    id: string,
    body: {
      allowed?: Record<string, number>;
      rejections?: Record<string, string>;
      reason?: string;
      scheme_reference?: string;
    },
  ) => request<Claim>(`/claims/${id}/respond/`, { method: "POST", body }),
  recordClaimPayment: (
    id: string,
    body: { amount: number; received_on?: string; remittance_reference?: string },
  ) => request<Claim>(`/claims/${id}/payments/`, { method: "POST", body }),
  schemeReceivables: () => request<SchemeReceivables>("/insurance/receivables/"),

  search: (term: string) =>
    request<{ term: string; results: SearchHit[] }>(
      `/search/?q=${encodeURIComponent(term)}`,
    ),

  /* -- product depth --------------------------------------------------- */

  units: (productId: string) =>
    request<Paginated<UnitOfMeasureRow>>(`/units/?product=${productId}`),
  saveUnit: (body: Record<string, unknown>) =>
    request<UnitOfMeasureRow>("/units/", { method: "POST", body }),

  productImages: (productId: string) =>
    request<Paginated<ProductImage>>(`/product-images/?product=${productId}`),
  /* multipart, so it bypasses `request` — that helper JSON-encodes every
     body and would send "[object File]". */
  uploadProductImage: async (body: FormData) => {
    const headers: Record<string, string> = {};
    const access = tokens.access;
    if (access) headers.Authorization = `Bearer ${access}`;
    const response = await fetch(`${BASE}/product-images/`, {
      method: "POST",
      headers,
      body,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new ApiFailure(
        response.status,
        (payload as { error: ApiError } | null)?.error ?? {
          code: "upload_failed",
          message: "Not uploaded.",
        },
      );
    }
    return (await response.json()) as ProductImage;
  },

  productRegistrations: (productId: string) =>
    request<Paginated<ProductRegistration>>(
      `/product-registrations/?product=${productId}`,
    ),
  /* Creating and editing the product itself, which nothing could do.
     A base unit is created alongside it — a product without one cannot
     be received, priced, sold or counted. */
  saveProduct: (body: Record<string, unknown>, id?: string) =>
    request<ProductRow>(id ? `/products/${id}/` : "/products/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  productTypes: () => request<Paginated<ProductType>>("/product-types/"),

  saveProductRegistration: (body: Record<string, unknown>) =>
    request<ProductRegistration>("/product-registrations/", { method: "POST", body }),

  clinicalAttributes: (productId: string) =>
    request<Paginated<ClinicalAttribute>>(
      `/clinical-attributes/?product=${productId}`,
    ),
  saveClinicalAttribute: (body: Record<string, unknown>) =>
    request<ClinicalAttribute>("/clinical-attributes/", { method: "POST", body }),
  intelligence: (params: { start?: string; end?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    const suffix = query.toString();
    return request<IntelligenceReport>(
      `/finance/intelligence/${suffix ? `?${suffix}` : ""}`,
    );
  },
  registerPharmacy: (body: Record<string, unknown>) =>
    request<RegisteredPharmacy>("/pharmacies/register/", { method: "POST", body }),

  /* -- stock movements ------------------------------------------------- */

  transferStock: (body: {
    batch: string;
    from_location: string;
    to_location: string;
    quantity: number;
    uom_code?: string;
    reason?: string;
  }) =>
    request<{ reference: string }>("/stock/transfer/", {
      method: "POST",
      body,
      // Stock moves here, so a retry must not move it twice.
      idempotencyKey: crypto.randomUUID(),
    }),
  quarantineStock: (body: {
    batch: string;
    location: string;
    quantity: number;
    uom_code?: string;
    reason: string;
  }) =>
    request<{ quarantined: boolean }>("/stock/quarantine/", { method: "POST", body }),
  returnToSupplier: (body: {
    batch: string;
    location: string;
    quantity: number;
    uom_code?: string;
    reason: string;
    status?: string;
  }) =>
    request<{ returned: boolean }>("/stock/supplier-return/", { method: "POST", body }),
  recallBatch: (body: { batch: string; reason: string; authority_reference?: string }) =>
    request<{
      reference: string;
      quantity_base: number;
      locations: number;
      trace: BatchTrace;
    }>("/stock/recall/", { method: "POST", body }),
  /* -- licences and registrations ---------------------------------------

     Renewal adds a record rather than editing the old one: a licence is
     evidence of what was permitted between two dates, and rewriting last
     year's expiry erases the fact that there was ever a gap. */
  saveLicence: (body: Partial<Licence>, id?: string) =>
    request<Licence>(id ? `/licences/${id}/` : "/licences/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  saveRegistration: (body: Partial<Registration>, id?: string) =>
    request<Registration>(
      id ? `/pharmacist-registrations/${id}/` : "/pharmacist-registrations/",
      { method: id ? "PATCH" : "POST", body },
    ),
  colleagues: () => request<Paginated<Colleague>>("/colleagues/"),
  /* A licence is issued to a branch, not to an organization. */
  branches: () => request<Paginated<Branch>>("/branches/"),

  /* -- insurance setup ----------------------------------------------------

     Without a contract no cover is ever found, so every insured patient
     is charged in full — silently. See insurance/services.py. */
  saveScheme: (body: Partial<Scheme>, id?: string) =>
    request<Scheme>(id ? `/schemes/${id}/` : "/schemes/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  schemeContracts: () => request<Paginated<SchemeContract>>("/scheme-contracts/"),
  saveSchemeContract: (body: Partial<SchemeContract>, id?: string) =>
    request<SchemeContract>(
      id ? `/scheme-contracts/${id}/` : "/scheme-contracts/",
      { method: id ? "PATCH" : "POST", body },
    ),
  coverageRules: (contractId?: string) =>
    request<Paginated<CoverageRule>>(
      contractId ? `/coverage-rules/?contract=${contractId}` : "/coverage-rules/",
    ),
  saveCoverageRule: (body: Partial<CoverageRule>, id?: string) =>
    request<CoverageRule>(id ? `/coverage-rules/${id}/` : "/coverage-rules/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  members: (search = "") =>
    request<Paginated<Member>>(`/members/?search=${encodeURIComponent(search)}`),
  saveMember: (body: Partial<Member>, id?: string) =>
    request<Member>(id ? `/members/${id}/` : "/members/", {
      method: id ? "PATCH" : "POST",
      body,
    }),

  /* -- import paper -------------------------------------------------------

     Two of these are gates rather than filing: a Certificate of Analysis
     releases a batch, and a cold-chain log with a breach holds one. See
     commerce.services._quarantine_reason. */
  importDocuments: (receiptId: string) =>
    request<Paginated<ImportDocument>>(`/import-documents/?receipt=${receiptId}`),
  saveImportDocument: (body: {
    receipt: string;
    kind: string;
    number?: string;
    issued_by?: string;
    issued_on?: string | null;
    batch?: string | null;
    min_temperature_c?: string | null;
    max_temperature_c?: string | null;
    breach?: boolean;
  }) => request<ImportDocument>("/import-documents/", { method: "POST", body }),
  verifyImportDocument: (id: string) =>
    request<ImportDocument>(`/import-documents/${id}/verify/`, {
      method: "POST",
      body: {},
    }),

  /* -- a depot's own listings --------------------------------------------

     `offered` is an allocation out of the depot's own stock, in the unit
     it prices in — not a view of the stock. A depot holding 500 packs
     may offer 200 and keep the rest for its branches. */
  myListings: () => request<Paginated<MarketplaceRow>>("/listings/"),
  publishListing: (body: {
    product: string;
    price: number;
    uom_code: string;
    offered?: number;
    moq?: number;
    lead_time_days?: number;
    srp?: number | null;
    availability?: string;
  }) => request<MarketplaceRow>("/listings/", { method: "POST", body }),
  /* Withdrawn, not deleted: the row leaves the marketplace and stays as
     the record that the offer was once made. */
  withdrawListing: (id: string) =>
    request<void>(`/listings/${id}/`, { method: "DELETE" }),
  setPriceTiers: (id: string, tiers: { min_quantity: number; price: number }[]) =>
    request<MarketplaceRow>(`/listings/${id}/tiers/`, {
      method: "POST",
      body: { tiers },
    }),

  /* -- tills, shifts and day end -----------------------------------------

     A sale belongs to the shift that was open on its till. Without one
     the sale is still recorded, but it belongs to no day, and day end
     has nothing to reconcile. */
  tills: () => request<Paginated<Till>>("/tills/"),
  saveTill: (body: { name: string; code: string; branch: string }) =>
    request<Till>("/tills/", { method: "POST", body }),
  shifts: () => request<Paginated<Shift>>("/shifts/"),
  openShift: (till: string, openingFloat: number) =>
    request<Shift>("/shifts/", {
      method: "POST",
      body: { till, opening_float: openingFloat },
    }),
  xReport: (shift: string) => request<DayEnd>(`/shifts/${shift}/x-report/`),
  closeShift: (
    shift: string,
    body: { counted_cash: number; variance_reason?: string; allow_pending?: boolean },
  ) => request<DayEnd>(`/shifts/${shift}/close/`, { method: "POST", body }),

  /* -- the Assistant -----------------------------------------------------

     `ask` reads and can suggest; it has no path to a service that
     writes. `decide` is the only thing that acts, and only on a
     proposal the server itself wrote. See backend/assistant/services.py. */
  ask: (question: string) =>
    request<Answer>("/assistant/ask/", { method: "POST", body: { question } }),
  decide: (proposalId: string, accepted: boolean, reason = "") =>
    request<Proposal>(`/assistant/proposals/${proposalId}/decide/`, {
      method: "POST",
      body: { accepted, reason },
    }),
  proposals: () => request<Paginated<Proposal>>("/proposals/"),

  /* -- cold chain -------------------------------------------------------

     An excursion quarantines stock on its own, which makes it the one
     alert in the system that acts rather than warns. That also makes it
     the one a pharmacist most needs to see: stock has gone unsellable
     and something has to say why, and let somebody decide about it. */
  /* A pharmacy gets whatever onboarding created and could add nothing:
     no cold room, no back store, no second counter. */
  saveLocation: (body: {
    name: string;
    code: string;
    kind: string;
    temperature_class: string;
    branch?: string | null;
  }) => request<Location>("/locations/", { method: "POST", body }),

  /* Without this the cold-chain screen lists nothing on every
     deployment — a probe could not be registered at all. */
  saveSensor: (body: {
    location: string;
    device_code: string;
    name: string;
    minimum_c: string;
    maximum_c: string;
  }) => request<Sensor>("/sensors/", { method: "POST", body }),

  excursions: (openOnly = false) =>
    request<Paginated<Excursion>>(
      `/excursions/${openOnly ? "?open=true" : ""}`,
    ),
  resolveExcursion: (id: string, resolution: string) =>
    request<Excursion>(`/excursions/${id}/resolve/`, {
      method: "POST",
      body: { resolution },
    }),
  sensors: () => request<Paginated<Sensor>>("/sensors/"),

  releaseBatch: (body: { batch: string; location: string; reason: string }) =>
    request<{ released: boolean }>("/batches/release/", { method: "POST", body }),
  batchTrace: (id: string) => request<BatchTrace>(`/batches/${id}/trace/`),
  returnSaleLine: (body: {
    sale_line: string;
    quantity: number;
    uom_code?: string;
    reason: string;
    restock: boolean;
  }) => request<{ returned: boolean }>("/sales/returns/", { method: "POST", body }),

  /* -- catalogue admin ------------------------------------------------- */

  manufacturers: () => request<Paginated<Manufacturer>>("/manufacturers/"),
  saveManufacturer: (body: Partial<Manufacturer>, id?: string) =>
    request<Manufacturer>(id ? `/manufacturers/${id}/` : "/manufacturers/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  categories: () => request<Paginated<Category>>("/categories/"),
  saveCategory: (body: { name: string }, id?: string) =>
    request<Category>(id ? `/categories/${id}/` : "/categories/", {
      method: id ? "PATCH" : "POST",
      body,
    }),

  /* -- patients and prescriptions -------------------------------------- */

  patients: (search = "") =>
    request<Paginated<Patient>>(`/patients/?search=${encodeURIComponent(search)}`),
  savePatient: (body: Partial<Patient>, id?: string) =>
    request<Patient>(id ? `/patients/${id}/` : "/patients/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  recordAllergy: (body: {
    patient: string;
    allergen: string;
    severity: string;
    note?: string;
  }) => request<PatientAllergy>("/allergies/", { method: "POST", body }),
  prescribers: () => request<Paginated<Prescriber>>("/prescribers/"),
  savePrescriber: (body: Partial<Prescriber>, id?: string) =>
    request<Prescriber>(id ? `/prescribers/${id}/` : "/prescribers/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  prescriptions: () => request<Paginated<Prescription>>("/prescriptions/"),
  createPrescription: (body: {
    patient: string;
    prescriber?: string | null;
    issued_on?: string | null;
    number?: string;
  }) => request<Prescription>("/prescriptions/", { method: "POST", body }),
  verifyPrescription: (id: string) =>
    request<Prescription>(`/prescriptions/${id}/verify/`, { method: "POST", body: {} }),

  /* -- thresholds and rules -------------------------------------------- */

  alertRules: () => request<Paginated<AlertRule>>("/alert-rules/"),
  saveAlertRule: (body: Partial<AlertRule>, id?: string) =>
    request<AlertRule>(id ? `/alert-rules/${id}/` : "/alert-rules/", {
      method: id ? "PATCH" : "POST",
      body,
    }),
  controlledQuotas: () => request<Paginated<ControlledQuota>>("/controlled-quotas/"),
  saveControlledQuota: (body: Partial<ControlledQuota>, id?: string) =>
    request<ControlledQuota>(
      id ? `/controlled-quotas/${id}/` : "/controlled-quotas/",
      { method: id ? "PATCH" : "POST", body },
    ),
  taxRules: () => request<Paginated<TaxRule>>("/tax-rules/"),
  saveTaxRule: (body: Partial<TaxRule>, id?: string) =>
    request<TaxRule>(id ? `/tax-rules/${id}/` : "/tax-rules/", {
      method: id ? "PATCH" : "POST",
      body,
    }),

  /* -- fulfilment ------------------------------------------------------ */

  prepareOrder: (id: string) =>
    request<PurchaseOrder>(`/purchase-orders/${id}/prepare/`, {
      method: "POST",
      body: {},
    }),
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
