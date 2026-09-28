"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/auth/token-store";

/**
 * Blocks access to the dashboard for unauthenticated visitors (#39).
 *
 * The JWT lives in localStorage (see token-store.ts), so this check must run
 * client-side rather than in middleware. It also listens for the
 * `auth:unauthorized` event the API client dispatches on a 401 (client.ts,
 * #16), so an expired/invalid session bounces the user to /login immediately
 * instead of only on next navigation.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setChecked(true);
  }, [router]);

  useEffect(() => {
    function handleUnauthorized() {
      router.replace("/login");
    }
    window.addEventListener("auth:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", handleUnauthorized);
  }, [router]);

  // Avoid flashing protected content before the redirect check resolves.
  if (!checked) return null;

  return <>{children}</>;
}
