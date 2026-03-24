"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Plan {
  id: string;
  name: string;
  tier: string;
  price_monthly: number;
  price_yearly: number;
  features: string[];
  limits: Record<string, number | boolean>;
}

interface UsageItem {
  label: string;
  current: number;
  limit: number;
  unlimited: boolean;
  at_limit: boolean;
  pct: number;
}

interface UsageSummary {
  tier: string;
  items: UsageItem[];
}

interface Subscription {
  plan_tier: string;
  plan_name: string;
}

const tierAccent: Record<string, string> = {
  solo: "text-gray-400",
  pro: "text-[var(--brand-gold)]",
  agency: "text-purple-400",
};

export default function BillingPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [sub, setSub] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [billing, setBilling] = useState<"monthly" | "yearly">("monthly");
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, u, s] = await Promise.all([
        apiGet<Plan[]>("/api/v1/billing/plans"),
        apiGet<UsageSummary>("/api/v1/billing/usage"),
        apiGet<Subscription>("/api/v1/billing/subscription"),
      ]);
      setPlans(p);
      setUsage(u);
      setSub(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load billing data");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleChangePlan = async (tier: string) => {
    try {
      await apiPost(`/api/v1/billing/change-plan?plan_tier=${tier}`, {});
      fetchAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change plan");
    }
  };

  return (
    <>
      <Header title="Billing & Plans" />

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {loading ? (
        <div className="mt-6 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
          Loading...
        </div>
      ) : (
        <>
          {/* Current usage */}
          {usage && (
            <div className="mt-8">
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                Current Usage
                <span className="ml-2 text-xs font-normal text-[var(--text-secondary)]">
                  — {usage.tier} plan
                </span>
              </h3>
              <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-5">
                {usage.items.map((item) => (
                  <div
                    key={item.label}
                    className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4"
                  >
                    <p className="text-xs text-[var(--text-secondary)]">{item.label}</p>
                    <p className="mt-1 text-lg font-bold text-[var(--text-primary)]">
                      {item.current}
                      <span className="text-sm font-normal text-[var(--text-secondary)]">
                        {item.unlimited ? " / ∞" : ` / ${item.limit}`}
                      </span>
                    </p>
                    {!item.unlimited && (
                      <div className="mt-2 h-1.5 w-full rounded-full bg-[var(--bg-primary)]">
                        <div
                          className={cn(
                            "h-1.5 rounded-full transition-all",
                            item.at_limit ? "bg-red-500" : item.pct > 80 ? "bg-yellow-500" : "bg-green-500"
                          )}
                          style={{ width: `${item.pct}%` }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Billing toggle */}
          <div className="mt-8 flex items-center gap-3">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Plans</h3>
            <div className="ml-auto flex rounded-lg bg-[var(--bg-surface)] p-0.5">
              {(["monthly", "yearly"] as const).map((b) => (
                <button
                  key={b}
                  onClick={() => setBilling(b)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                    billing === b
                      ? "bg-[var(--brand-gold)] text-black"
                      : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  )}
                >
                  {b === "monthly" ? "Monthly" : "Yearly (save ~17%)"}
                </button>
              ))}
            </div>
          </div>

          {/* Plan cards */}
          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
            {plans.map((plan) => {
              const isCurrent = sub?.plan_tier === plan.tier;
              const price = billing === "monthly" ? plan.price_monthly : plan.price_yearly;
              const period = billing === "monthly" ? "/mo" : "/yr";

              return (
                <div
                  key={plan.id}
                  className={cn(
                    "rounded-xl border-2 bg-[var(--bg-surface)] p-6",
                    isCurrent ? "border-[var(--brand-gold)]" : "border-[var(--border-color)]"
                  )}
                >
                  <div className="flex items-center justify-between">
                    <h4 className={cn("text-lg font-bold", tierAccent[plan.tier] || "text-[var(--text-primary)]")}>
                      {plan.name}
                    </h4>
                    {isCurrent && (
                      <span className="rounded-full bg-[var(--brand-gold)]/20 px-2 py-0.5 text-xs font-medium text-[var(--brand-gold)]">
                        Current
                      </span>
                    )}
                  </div>
                  <p className="mt-2">
                    <span className="text-3xl font-bold text-[var(--text-primary)]">
                      ${price === 0 ? "0" : price.toFixed(0)}
                    </span>
                    <span className="text-sm text-[var(--text-secondary)]">{period}</span>
                  </p>

                  <ul className="mt-4 space-y-2">
                    {plan.features.map((f) => (
                      <li key={f} className="flex items-start gap-2 text-sm text-[var(--text-secondary)]">
                        <span className="mt-0.5 text-green-400 shrink-0">✓</span>
                        {f}
                      </li>
                    ))}
                  </ul>

                  {!isCurrent && (
                    <button
                      onClick={() => handleChangePlan(plan.tier)}
                      className={cn(
                        "mt-6 w-full rounded-lg py-2 text-sm font-medium transition-colors",
                        plan.tier === "pro"
                          ? "bg-[var(--brand-gold)] text-black hover:bg-[var(--brand-gold)]/90"
                          : "bg-[var(--bg-surface-hover)] text-[var(--text-primary)] hover:bg-[var(--bg-primary)]"
                      )}
                    >
                      {price === 0 ? "Downgrade" : "Upgrade"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
