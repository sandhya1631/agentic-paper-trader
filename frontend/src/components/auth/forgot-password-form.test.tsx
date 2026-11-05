// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/auth/auth-api", () => ({ forgotPassword: vi.fn() }));

import { forgotPassword } from "@/lib/auth/auth-api";
import { ForgotPasswordForm } from "./forgot-password-form";
import { ApiError } from "@/lib/api/errors";

const mockForgotPassword = vi.mocked(forgotPassword);

afterEach(() => {
  cleanup();
  mockForgotPassword.mockReset();
});

describe("ForgotPasswordForm", () => {
  it("shows the same generic success message after a valid submit", async () => {
    mockForgotPassword.mockResolvedValue({ detail: "sent" });
    render(<ForgotPasswordForm />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "operator@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByText(/check your email/i)).toBeTruthy();
    expect(mockForgotPassword).toHaveBeenCalledWith("operator@example.com");
  });

  it("shows an error message when the request fails", async () => {
    mockForgotPassword.mockRejectedValue(new ApiError(422, "Unprocessable", null, "/auth/forgot-password"));
    render(<ForgotPasswordForm />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "not-an-email" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/check your email/i)).toBeNull();
  });
});
