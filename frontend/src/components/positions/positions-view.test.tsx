// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/broker/broker-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/broker/broker-api")>();
  return { ...actual, getPositions: vi.fn(), getOrders: vi.fn() };
});

import { getOrders, getPositions } from "@/lib/broker/broker-api";
import { PositionsView } from "./positions-view";

const mockPositions = vi.mocked(getPositions);
const mockOrders = vi.mocked(getOrders);

beforeEach(() => {
  mockPositions.mockReset();
  mockOrders.mockReset();
  mockOrders.mockResolvedValue([]);
});

afterEach(() => cleanup());

describe("PositionsView", () => {
  it("renders a positions row with an unrealized-P/L badge", async () => {
    mockPositions.mockResolvedValue([
      {
        symbol: "AAPL",
        qty: "10",
        side: "long",
        market_value: "3350",
        cost_basis: "3000",
        unrealized_pl: "350",
        unrealized_plpc: "0.1167",
        current_price: "335",
        avg_entry_price: "300",
      },
    ]);

    render(<PositionsView />);

    expect(await screen.findByText("AAPL")).toBeTruthy();
    expect(screen.getByText(/\+\$350\.00/)).toBeTruthy();
    expect(screen.getByText(/\+11\.67%/)).toBeTruthy();
  });

  it("shows empty states for no positions and no orders", async () => {
    mockPositions.mockResolvedValue([]);

    render(<PositionsView />);

    expect(await screen.findByText("No open positions.")).toBeTruthy();
    expect(screen.getByText("No open orders.")).toBeTruthy();
  });

  it("shows the connect notice when the account is not connected", async () => {
    mockPositions.mockRejectedValue(
      new ApiError(503, "Service Unavailable", { detail: "No Alpaca account connected" }, "http://x"),
    );

    render(<PositionsView />);

    expect(await screen.findByText(/no alpaca paper account connected/i)).toBeTruthy();
  });

  it("surfaces a load error distinctly", async () => {
    mockPositions.mockRejectedValue(new ApiError(500, "Server Error", { detail: "kaboom" }, "http://x"));

    render(<PositionsView />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("kaboom");
  });

  it("renders an open order row", async () => {
    mockPositions.mockResolvedValue([]);
    mockOrders.mockResolvedValue([
      {
        id: "o1",
        client_order_id: "cid-1",
        symbol: "NVDA",
        qty: "5",
        filled_qty: "0",
        average_fill_price: null,
        side: "buy",
        type: "market",
        time_in_force: "day",
        status: "new",
        submitted_at: "2026-11-02T01:00:00Z",
        filled_at: null,
      },
    ]);

    render(<PositionsView />);

    expect(await screen.findByText("NVDA")).toBeTruthy();
    // Scope to the orders table so the manual-order ticket's BUY option (added in
    // #143) doesn't collide with the order row's side badge.
    const ordersTable = screen.getByRole("table");
    expect(within(ordersTable).getByText("BUY")).toBeTruthy();
  });
});
