"use client";

import { usePathname } from "next/navigation";
import { useAuth } from "./auth-provider";
import { Sidebar, useSidebarState } from "@/components/layout/sidebar";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const PUBLIC_PATHS = ["/login", "/privacy", "/terms", "/welcome"];

export function AuthLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, role } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(pathname);
  const { collapsed, toggle } = useSidebarState();
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

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

  // On mobile: no left margin (sidebar is an overlay).
  // On desktop: margin matches sidebar width (collapsed or expanded).
  const mainMargin = isMobile ? 0 : collapsed ? 68 : 240;

  return (
    <div className="flex min-h-screen">
      <Sidebar collapsed={collapsed} onToggle={toggle} isAdmin={role === "admin" || role === "owner"} />
      <main
        className="flex-1 p-4 pt-16 md:pt-8 md:p-8 transition-[margin-left] duration-200"
        style={{ marginLeft: mainMargin }}
      >
        {children}
      </main>
    </div>
  );
}
