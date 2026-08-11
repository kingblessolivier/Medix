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

export const api = {
  me: () => request<Me>("/auth/me/"),
  stock: (params = "") => request<Paginated<StockRow>>(`/stock/${params}`),
  movements: (params = "") => request<Cursored<Movement>>(`/stock-movements/${params}`),
  products: (params = "") => request<Paginated<ProductRow>>(`/products/${params}`),
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
};
