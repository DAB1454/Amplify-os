"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { getAccessToken, clearAuth, fetchCurrentUser, ApiError } from "@/lib/api";

interface AuthState {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: { id: string; email: string; display_name: string | null } | null;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  isAuthenticated: false,
  isLoading: true,
  user: null,
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [user, setUser] = useState<AuthState["user"]>(null);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      setIsLoading(false);
      return;
    }
    fetchCurrentUser()
      .then((u) => setUser(u))
      .catch((err) => {
        console.error("[AuthProvider] fetchCurrentUser failed:", err);
        // Only clear auth on explicit 401 — other errors might be transient
        if (err instanceof ApiError && err.status === 401) {
          clearAuth();
        } else {
          // Non-auth error — still treat as authenticated since we have a token
          setUser({ id: "", email: "", display_name: null });
        }
      })
      .finally(() => setIsLoading(false));
  }, []);

  const logout = () => {
    clearAuth();
    setUser(null);
    window.location.href = "/login";
  };

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated: !!user,
        isLoading,
        user,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
