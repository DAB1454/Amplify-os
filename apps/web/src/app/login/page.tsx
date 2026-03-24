"use client";

import Image from "next/image";
import { useState } from "react";
import { login, register } from "@/lib/api";

export default function LoginPage() {
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      if (isRegister) {
        await register(email, password, tenantName, displayName || undefined);
      } else {
        await login(email, password);
      }
      // Full reload so AuthProvider picks up the new token
      window.location.href = "/";
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Something went wrong";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-white">
      <div className="w-full max-w-md p-8 rounded-xl border border-[var(--border-color)] bg-white shadow-lg">
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4">
            <Image src="/logo.png" alt="AmplifyMe" width={80} height={80} className="rounded-2xl" />
          </div>
          <h1 className="text-3xl font-bold mb-2 bg-gradient-to-r from-blue-500 via-violet-500 to-pink-500 bg-clip-text text-transparent">
            AmplifyMe
          </h1>
          <p className="text-[var(--text-secondary)]">
            {isRegister ? "Create your account" : "Sign in to your account"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {isRegister && (
            <>
              <div>
                <label className="block text-sm font-medium mb-1 text-[var(--text-secondary)]">
                  Organization Name
                </label>
                <input
                  type="text"
                  value={tenantName}
                  onChange={(e) => setTenantName(e.target.value)}
                  required
                  placeholder="e.g. Drew Baird Music"
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] text-[var(--text-primary)] text-sm outline-none focus:ring-2 focus:ring-[var(--brand-gold)]/30 focus:border-[var(--brand-gold)]"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1 text-[var(--text-secondary)]">
                  Display Name
                </label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="e.g. Drew Baird"
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] text-[var(--text-primary)] text-sm outline-none focus:ring-2 focus:ring-[var(--brand-gold)]/30 focus:border-[var(--brand-gold)]"
                />
              </div>
            </>
          )}

          <div>
            <label className="block text-sm font-medium mb-1 text-[var(--text-secondary)]">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="you@example.com"
              className="w-full px-3 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] text-[var(--text-primary)] text-sm outline-none focus:ring-2 focus:ring-[var(--brand-gold)]/30 focus:border-[var(--brand-gold)]"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1 text-[var(--text-secondary)]">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              placeholder="Min 8 characters"
              className="w-full px-3 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] text-[var(--text-primary)] text-sm outline-none focus:ring-2 focus:ring-[var(--brand-gold)]/30 focus:border-[var(--brand-gold)]"
            />
          </div>

          {error && (
            <p className="text-sm text-red-500">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg font-semibold text-sm text-white transition-opacity disabled:opacity-50"
            style={{ background: "var(--brand-gradient)" }}
          >
            {loading
              ? "..."
              : isRegister
                ? "Create Account"
                : "Sign In"}
          </button>
        </form>

        <p className="text-center text-sm mt-6 text-[var(--text-secondary)]">
          {isRegister ? "Already have an account?" : "Don't have an account?"}{" "}
          <button
            onClick={() => {
              setIsRegister(!isRegister);
              setError("");
            }}
            className="underline font-medium text-[var(--brand-gold)]"
          >
            {isRegister ? "Sign In" : "Register"}
          </button>
        </p>
      </div>
    </div>
  );
}
