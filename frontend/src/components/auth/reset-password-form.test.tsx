// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/auth/auth-api", () => ({ resetPassword: vi.fn() }));

import { resetPassword } from "@/lib/auth/auth-api";
import { ResetPasswordForm } from "./reset-password-form";
import { ApiError } from "@/lib/api/errors";

const mockResetPassword = vi.mocked(resetPassword);

afterEach(() => {
  cleanup();
  mockResetPassword.mockReset();
  replace.mockReset();
});

describe("ResetPasswordForm", () => {
  it("shows an invalid-link state and no form when there is no token", () => {
    render(<ResetPasswordForm token={null} />);

    expect(screen.getByText(/invalid reset link/i)).toBeTruthy();
    expect(screen.queryByLabelText(/new password/i)).toBeNull();
    expect(screen.getByRole("link", { name: /request a new link/i })).toBeTruthy();
  });

  it("rejects a too-short password before calling the API", () => {
    render(<ResetPasswordForm token="abc123" />);

    fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: "short" } });
    fireEvent.change(screen.getByLabelText(/confirm new password/i), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    expect(screen.getByRole("alert").textContent).toMatch(/at least 8 characters/i);
    expect(mockResetPassword).not.toHaveBeenCalled();
  });

  it("rejects mismatched passwords before calling the API", () => {
    render(<ResetPasswordForm token="abc123" />);

    fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: "N3w-Password!!" } });
    fireEvent.change(screen.getByLabelText(/confirm new password/i), { target: { value: "different" } });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    expect(screen.getByRole("alert").textContent).toMatch(/do not match/i);
    expect(mockResetPassword).not.toHaveBeenCalled();
  });

  it("submits the token and new password, then redirects to login on success", async () => {
    mockResetPassword.mockResolvedValue({ detail: "ok" });
    render(<ResetPasswordForm token="abc123" />);

    fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: "N3w-Password!!" } });
    fireEvent.change(screen.getByLabelText(/confirm new password/i), { target: { value: "N3w-Password!!" } });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/login?reset=1"));
    expect(mockResetPassword).toHaveBeenCalledWith({ token: "abc123", new_password: "N3w-Password!!" });
  });

  it("shows a link to request a new token when the link is invalid or expired", async () => {
    mockResetPassword.mockRejectedValue(
      new ApiError(400, "Bad Request", { detail: "Invalid or expired reset link" }, "/auth/reset-password"),
    );
    render(<ResetPasswordForm token="stale-token" />);

    fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: "N3w-Password!!" } });
    fireEvent.change(screen.getByLabelText(/confirm new password/i), { target: { value: "N3w-Password!!" } });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("link", { name: /request a new link/i })).toBeTruthy();
    expect(replace).not.toHaveBeenCalled();
  });
});
