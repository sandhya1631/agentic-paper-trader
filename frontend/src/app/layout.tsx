import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Agentic Paper Trader",
  description:
    "Operator console for the autonomous LLM paper-trading agent (Alpaca sandbox).",
};

/**
 * Root layout — fonts/global styles only. The dashboard chrome (sidebar,
 * header, auth guard) lives in `(app)/layout.tsx`; the `(auth)` route group
 * (login/register, #39) intentionally renders without it.
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        {children}
      </body>
    </html>
  );
}
