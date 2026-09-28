"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { login } from "@/lib/auth/auth-api";
import { getErrorMessage } from "@/lib/api/error-message";

export function LoginForm({ registered }: { registered?: boolean }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login({ email, password });
      router.replace("/");
    } catch (err) {
      setError(getErrorMessage(err, "Invalid email or password."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      <div>
        <h2 className="text-lg font-semibold">Sign in</h2>
        <p className="mt-1 text-sm text-muted">
          Operator access to the paper-trading console.
        </p>
      </div>

      {registered && (
        <p className="rounded-lg border border-positive/30 bg-positive/10 px-3 py-2 text-sm text-positive">
          Account created — sign in below.
        </p>
      )}

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

      <div className="space-y-1.5">
        <label htmlFor="password" className="text-sm font-medium">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {submitting ? "Signing in…" : "Sign in"}
      </button>

      <p className="text-center text-sm text-muted">
        No account?{" "}
        <Link href="/register" className="font-medium text-brand hover:underline">
          Register
        </Link>
      </p>
    </form>
  );
}
