"use client";

import { usePathname } from "next/navigation";
import { useAuth } from "./auth-provider";
import { Sidebar } from "@/components/layout/sidebar";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

const PUBLIC_PATHS = ["/login", "/privacy", "/terms", "/welcome"];

export function AuthLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(pathname);

  useEffect(() => {
    if (!isLoading && !isAuthenticated && !isPublic) {
      router.push("/login");
    }
    if (!isLoading && isAuthenticated && pathname === "/login") {
      router.push("/");
    }
  }, [isLoading, isAuthenticated, isPublic, router]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p style={{ color: "var(--text-secondary)" }}>Loading...</p>
      </div>
    );
  }

  if (isPublic) {
    return <>{children}</>;
  }

  if (!isAuthenticated) {
    return null;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 ml-[240px] p-8">{children}</main>
    </div>
  );
}
