"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";

interface OnboardingStep {
  key: string;
  label: string;
  description: string;
  is_completed: boolean;
  is_required: boolean;
}

interface OnboardingStatus {
  completed: boolean;
  steps: OnboardingStep[];
  progress_pct: number;
}

interface Tenant {
  id: string;
  name: string;
  slug: string;
  plan_tier: string;
  onboarding_completed: boolean;
  settings: Record<string, unknown>;
}

const stepLinks: Record<string, string> = {
  create_artist: "/artists",
  connect_channel: "/channels",
  add_release: "/releases",
  create_campaign: "/campaigns",
  choose_plan: "/billing",
};

export default function TenantsPage() {
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [onboarding, setOnboarding] = useState<OnboardingStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [t, o] = await Promise.all([
        apiGet<Tenant>("/api/v1/tenants/me"),
        apiGet<OnboardingStatus>("/api/v1/onboarding/status"),
      ]);
      setTenant(t);
      setOnboarding(o);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleDismiss = async () => {
    try {
      await apiPost("/api/v1/onboarding/dismiss", {});
      fetchAll();
    } catch { /* ignore */ }
  };

  return (
    <>
      <Header title="Workspace Settings" />

      {loading ? (
        <div className="mt-6 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
          Loading...
        </div>
      ) : (
        <>
          {/* Workspace info */}
          {tenant && (
            <div className="mt-8 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-bold text-[var(--text-primary)]">{tenant.name}</h3>
                  <p className="text-sm text-[var(--text-secondary)]">@{tenant.slug}</p>
                </div>
                <span className="rounded-full bg-[var(--brand-gold)]/20 px-3 py-1 text-sm font-medium text-[var(--brand-gold)]">
                  {tenant.plan_tier}
                </span>
              </div>
            </div>
          )}

          {/* Onboarding wizard */}
          {onboarding && !onboarding.completed && (
            <div className="mt-6">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                  Getting Started
                </h3>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-[var(--text-secondary)]">
                    {onboarding.progress_pct}% complete
                  </span>
                  <button
                    onClick={handleDismiss}
                    className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  >
                    Dismiss
                  </button>
                </div>
              </div>

              {/* Progress bar */}
              <div className="mt-3 h-2 w-full rounded-full bg-[var(--bg-surface)]">
                <div
                  className="h-2 rounded-full bg-[var(--brand-gold)] transition-all"
                  style={{ width: `${onboarding.progress_pct}%` }}
                />
              </div>

              {/* Steps */}
              <div className="mt-4 space-y-2">
                {onboarding.steps.map((step) => (
                  <a
                    key={step.key}
                    href={stepLinks[step.key] || "#"}
                    className={cn(
                      "flex items-center gap-4 rounded-xl border bg-[var(--bg-surface)] px-5 py-4 transition-colors",
                      step.is_completed
                        ? "border-green-500/30 opacity-60"
                        : "border-[var(--border-color)] hover:border-[var(--brand-gold)]/50"
                    )}
                  >
                    <div
                      className={cn(
                        "flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold shrink-0",
                        step.is_completed
                          ? "bg-green-500/20 text-green-400"
                          : "bg-[var(--bg-primary)] text-[var(--text-secondary)]"
                      )}
                    >
                      {step.is_completed ? "✓" : "○"}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={cn(
                          "text-sm font-medium",
                          step.is_completed ? "text-[var(--text-secondary)] line-through" : "text-[var(--text-primary)]"
                        )}>
                          {step.label}
                        </span>
                        {step.is_required && !step.is_completed && (
                          <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-[10px] text-red-400">
                            required
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-[var(--text-secondary)]">{step.description}</p>
                    </div>
                    {!step.is_completed && (
                      <span className="text-xs text-[var(--brand-gold)] shrink-0">
                        Start →
                      </span>
                    )}
                  </a>
                ))}
              </div>
            </div>
          )}

          {onboarding?.completed && (
            <div className="mt-6 rounded-xl border border-green-500/30 bg-green-500/5 p-6 text-center">
              <p className="text-sm text-green-400">
                Onboarding complete! Your workspace is ready to go.
              </p>
            </div>
          )}
        </>
      )}
    </>
  );
}
