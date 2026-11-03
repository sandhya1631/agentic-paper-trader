import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

import { api } from "@/lib/api/client";
import { approveApproval, listPendingApprovals, rejectApproval } from "./approvals-api";

const mockedApi = vi.mocked(api);

beforeEach(() => {
  mockedApi.get.mockReset();
  mockedApi.post.mockReset();
});

describe("listPendingApprovals", () => {
  it("fetches the pending approvals from the backend", async () => {
    const pending = [{ approval: { id: "a1" }, proposal: { symbol: "AAPL" } }];
    mockedApi.get.mockResolvedValue(pending);

    const result = await listPendingApprovals();

    expect(mockedApi.get).toHaveBeenCalledWith("/agent/approvals/pending");
    expect(result).toBe(pending);
  });
});

describe("approveApproval", () => {
  it("posts to the approve endpoint with the comment", async () => {
    mockedApi.post.mockResolvedValue({ id: "c1", status: "COMPLETED" });

    await approveApproval("abc-123", "  looks fine  ");

    expect(mockedApi.post).toHaveBeenCalledWith("/agent/approvals/abc-123/approve", {
      comment: "looks fine",
    });
  });

  it("sends an empty body when the comment is blank", async () => {
    mockedApi.post.mockResolvedValue({ id: "c1", status: "COMPLETED" });

    await approveApproval("abc-123", "   ");

    expect(mockedApi.post).toHaveBeenCalledWith("/agent/approvals/abc-123/approve", {});
  });
});

describe("rejectApproval", () => {
  it("posts to the reject endpoint with the comment", async () => {
    mockedApi.post.mockResolvedValue({ id: "c1", status: "REJECTED" });

    await rejectApproval("abc-123", "not now");

    expect(mockedApi.post).toHaveBeenCalledWith("/agent/approvals/abc-123/reject", {
      comment: "not now",
    });
  });

  it("passes backend errors through to the caller", async () => {
    mockedApi.post.mockRejectedValue(new Error("boom"));

    await expect(rejectApproval("abc-123")).rejects.toThrow("boom");
  });
});