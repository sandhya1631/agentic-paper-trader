"use client";

import { useState } from "react";
import Link from "next/link";
import { forgotPassword } from "@/lib/auth/auth-api";
import { getErrorMessage } from "@/lib/api/error-message";

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await forgotPassword(email);
      // The backend returns the same generic message whether or not the email
      // exists, so the UI always shows the same success state too — it must
      // never reveal which emails are registered.
      setSubmitted(true);
    } catch (err) {
      setError(getErrorMessage(err, "Could not request a reset link."));
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <div className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold">Check your email</h2>
          <p className="mt-1 text-sm text-muted">
            If <span className="font-medium text-foreground">{email}</span> is
            registered, a password reset link has been sent. The link expires in
            30 minutes.
          </p>
        </div>
        <Link
          href="/login"
          className="block w-full rounded-lg border border-border px-3 py-2 text-center text-sm font-medium transition-colors hover:bg-surface-muted"
        >
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      <div>
        <h2 className="text-lg font-semibold">Forgot password</h2>
        <p className="mt-1 text-sm text-muted">
          Enter your account email and we&apos;ll send you a reset link.
        </p>
      </div>

      {error && (
        <p role="alert" className="rounded-lg border border-negative/30 bg-negative/10 px-3 py-2 text-sm text-negative">
          {error}
        </p>
      )}

      <div className="space-y-1.5">
        <label htmlFor="email" className="text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {submitting ? "Sending…" : "Send reset link"}
      </button>

      <p className="text-center text-sm text-muted">
        <Link href="/login" className="font-medium text-brand hover:underline">
          Back to sign in
        </Link>
      </p>
    </form>
  );
}
