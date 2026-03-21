"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { getAccessToken, clearAuth, fetchCurrentUser } from "@/lib/api";

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
      .catch(() => clearAuth())
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
