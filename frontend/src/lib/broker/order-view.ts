/**
 * React-free display helpers for broker orders.
 */
import { formatMoney } from "@/lib/format";
import type { AlpacaOrder } from "./broker-api";

/**
 * An order's size: its share quantity, or e.g. `$100.00 notional` for a notional
 * (dollar-amount) order, which Alpaca returns with `qty: null` (#177).
 */
export function formatOrderQuantity(order: Pick<AlpacaOrder, "qty" | "notional">): string {
  if (order.qty !== null) return order.qty;
  if (order.notional !== null) return `${formatMoney(order.notional)} notional`;
  return "—";
}
