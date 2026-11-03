/**
 * Broker API seam — typed wrappers over the read-only `/api/v1/broker/*` endpoints.
 * The shared api client attaches the Bearer token and handles 401 globally; these
 * read the user's connected Alpaca paper account. Decimal fields arrive as strings.
 */
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

export interface AlpacaAccount {
  id: string;
  status: string;
  currency: string;
  cash: string;
  portfolio_value: string;
  buying_power: string;
  equity: string;
  long_market_value: string;
  short_market_value: string;
  initial_margin: string;
  maintenance_margin: string;
  daytrading_buying_power: string;
  regt_buying_power: string;
  last_equity: string;
  is_paper: boolean;
}

export interface AlpacaPosition {
  symbol: string;
  qty: string;
  side: string;
  market_value: string;
  cost_basis: string;
  unrealized_pl: string;
  unrealized_plpc: string;
  current_price: string;
  avg_entry_price: string;
}

export interface AlpacaOrder {
  id: string;
  client_order_id: string;
  symbol: string;
  qty: string;
  filled_qty: string;
  average_fill_price: string | null;
  side: string;
  type: string;
  time_in_force: string;
  status: string;
  submitted_at: string | null;
  filled_at: string | null;
}

export type OrderStatusFilter = "open" | "closed" | "all";

// --- Manual order submission (#143) ---
// NOTE: POST /api/v1/broker/orders is the MANUAL OVERRIDE path. It only checks the
// global kill switch and BYPASSES the risk guardrails (P-001…P-012) that the agent
// path enforces. The enums are UPPERCASE; lowercase values return 422.
export type OrderSide = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT";
export type TimeInForce = "DAY";

export interface OrderCreate {
  symbol: string;
  qty: number; // integer > 0
  side: OrderSide;
  type?: OrderType;
  time_in_force?: TimeInForce;
  limit_price?: number | string | null;
  client_order_id?: string | null;
}

/** Submit a manual paper order (bypasses the agent's risk policies). */
export function createOrder(order: OrderCreate): Promise<AlpacaOrder> {
  return api.post<AlpacaOrder>("/api/v1/broker/orders", order);
}

export function getAccount(): Promise<AlpacaAccount> {
  return api.get<AlpacaAccount>("/api/v1/broker/account");
}

export function getPositions(): Promise<AlpacaPosition[]> {
  return api.get<AlpacaPosition[]>("/api/v1/broker/positions");
}

export function getOrders(status: OrderStatusFilter = "open"): Promise<AlpacaOrder[]> {
  return api.get<AlpacaOrder[]>(`/api/v1/broker/orders?status=${status}`);
}

/**
 * True when an error means "this user has no usable Alpaca account connected"
 * (so the UI should prompt them to connect in Settings rather than show an error).
 * The backend returns 503 when no account is connected and 400 when stored
 * credentials cannot be decrypted; both carry a "connect/reconnect" detail.
 */
export function isAccountNotConnected(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 404 || error.status === 503) return true;
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && /connect|not connected|reconnect|decrypt/i.test(detail);
}
