// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/broker/broker-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/broker/broker-api")>();
  return { ...actual, createOrder: vi.fn() };
});

import { createOrder } from "@/lib/broker/broker-api";
import { ManualOrderTicket, describeOrderError } from "./manual-order-ticket";

const mockCreate = vi.mocked(createOrder);

function placed(status = "accepted") {
  return {
    id: "o1",
    client_order_id: "cid-abc123",
    symbol: "AAPL",
    qty: "2",
    notional: null,
    filled_qty: "0",
    average_fill_price: null,
    side: "SELL",
    type: "MARKET",
    time_in_force: "DAY",
    status,
    submitted_at: "2026-11-02T15:00:00Z",
    filled_at: null,
  };
}

beforeEach(() => mockCreate.mockReset());
afterEach(() => cleanup());

describe("ManualOrderTicket", () => {
  it("shows the guardrail-bypass warning", () => {
    render(<ManualOrderTicket />);
    expect(screen.getByText(/bypasses the risk policies/i)).toBeTruthy();
  });

  it("submits UPPERCASE enums with an integer qty after confirmation", async () => {
    mockCreate.mockResolvedValue(placed());
    render(<ManualOrderTicket />);

    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "aapl" } });
    fireEvent.change(screen.getByLabelText("Quantity (shares)"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Side"), { target: { value: "SELL" } });
    fireEvent.click(screen.getByRole("button", { name: /review manual order/i }));
    fireEvent.click(await screen.findByRole("button", { name: /confirm override/i }));

    expect(mockCreate).toHaveBeenCalledWith({
      symbol: "AAPL",
      qty: 2,
      side: "SELL",
      type: "MARKET",
      time_in_force: "DAY",
    });
    expect(await screen.findByText(/submitted/i)).toBeTruthy();
  });
});

// The error mapping is tested directly (the component feeds res.reason straight to
// this helper), so 423/502 distinctness is covered without a rejected-promise render.
describe("describeOrderError", () => {
  it("maps the kill switch (423) distinctly", () => {
    expect(describeOrderError(new ApiError(423, "Locked", { detail: "ks" }, "http://x"))).toMatch(
      /kill switch/i,
    );
  });

  it("maps a broker failure (502) distinctly", () => {
    expect(describeOrderError(new ApiError(502, "Bad Gateway", { detail: "b" }, "http://x"))).toMatch(
      /broker did not confirm/i,
    );
  });

  it("falls back to the API detail for other errors", () => {
    expect(
      describeOrderError(new ApiError(400, "Bad Request", { detail: "qty must be > 0" }, "http://x")),
    ).toMatch(/qty must be > 0/);
  });
});
