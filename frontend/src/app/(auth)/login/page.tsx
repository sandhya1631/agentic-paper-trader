import { LoginForm } from "@/components/auth/login-form";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ registered?: string; reset?: string }>;
}) {
  const params = await searchParams;
  return (
    <LoginForm
      registered={params.registered === "1"}
      passwordReset={params.reset === "1"}
    />
  );
}
