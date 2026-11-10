"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { resetPassword } from "@/lib/auth/auth-api";
import { getErrorMessage } from "@/lib/api/error-message";

const MIN_PASSWORD_LENGTH = 8;

export function ResetPasswordForm({ token }: { token: string | null }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!token) {
    return (
      <div className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold">Invalid reset link</h2>
          <p className="mt-1 text-sm text-muted">
            This password reset link is missing its token. Request a new one.
          </p>
        </div>
        <Link
          href="/forgot-password"
          className="block w-full rounded-lg bg-brand px-3 py-2 text-center text-sm font-medium text-brand-foreground transition-opacity hover:opacity-90"
        >
          Request a new link
        </Link>
      </div>
    );
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await resetPassword({ token: token as string, new_password: password });
      router.replace("/login?reset=1");
    } catch (err) {
      // A 400 here means the token is invalid, already used, or expired
      // (the backend's generic reset-link error) — surfaced as-is.
      setError(getErrorMessage(err, "Could not reset the password."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      <div>
        <h2 className="text-lg font-semibold">Reset password</h2>
        <p className="mt-1 text-sm text-muted">Choose a new password for your account.</p>
      </div>

      {error && (
        <p role="alert" className="rounded-lg border border-negative/30 bg-negative/10 px-3 py-2 text-sm text-negative">
          {error}
          {error.toLowerCase().includes("invalid or expired") && (
            <>
              {" "}
              <Link href="/forgot-password" className="font-medium underline">
                Request a new link
              </Link>
              .
            </>
          )}
        </p>
      )}

      <div className="space-y-1.5">
        <label htmlFor="password" className="text-sm font-medium">
          New password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="confirmPassword" className="text-sm font-medium">
          Confirm new password
        </label>
        <input
          id="confirmPassword"
          name="confirmPassword"
          type="password"
          autoComplete="new-password"
          required
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {submitting ? "Resetting…" : "Reset password"}
      </button>
    </form>
  );
}
