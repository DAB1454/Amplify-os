import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/components/auth/auth-provider";
import { AuthLayout } from "@/components/auth/auth-layout";
import { ToastProvider } from "@/components/ui/toast";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "AmplifyMe",
  description: "Music marketing command center",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.variable} antialiased`}>
        <AuthProvider>
          <ToastProvider>
            <AuthLayout>{children}</AuthLayout>
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
