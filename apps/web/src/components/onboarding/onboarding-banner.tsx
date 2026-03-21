"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import Link from "next/link";

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

const STEP_LINKS: Record<string, string> = {
  create_artist: "/artists",
  connect_channel: "/channels",
  add_release: "/releases",
  create_campaign: "/campaigns",
  choose_plan: "/billing",
};

export function OnboardingBanner() {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    apiGet<OnboardingStatus>("/api/v1/onboarding/status")
      .then(setStatus)
      .catch(() => {});
  }, []);

  if (!status || status.completed || dismissed) return null;

  const remaining = status.steps.filter((s) => !s.is_completed);

  async function handleDismiss() {
    await apiPost("/api/v1/onboarding/dismiss", {}).catch(() => {});
    setDismissed(true);
  }

  return (
    <div className="mb-8 rounded-xl border border-[var(--brand-gold)]/30 bg-[var(--brand-gold)]/5 p-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[var(--brand-gold)]">
            Welcome to Amplify OS
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Complete these steps to get your account set up.
          </p>
        </div>
        <button
          onClick={handleDismiss}
          className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
        >
          Dismiss
        </button>
      </div>

      {/* Progress bar */}
      <div className="mt-4 flex items-center gap-3">
        <div className="h-2 flex-1 rounded-full bg-[var(--bg-primary)]">
          <div
            className="h-2 rounded-full bg-[var(--brand-gold)] transition-all duration-500"
            style={{ width: `${status.progress_pct}%` }}
          />
        </div>
        <span className="text-xs text-[var(--text-secondary)]">
          {status.progress_pct}%
        </span>
      </div>

      {/* Steps */}
      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {status.steps.map((step) => (
          <Link
            key={step.key}
            href={STEP_LINKS[step.key] || "/"}
            className={`flex items-center gap-3 rounded-lg border px-4 py-3 transition-colors ${
              step.is_completed
                ? "border-green-500/30 bg-green-500/5"
                : "border-[var(--border-color)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-hover)]"
            }`}
          >
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                step.is_completed
                  ? "bg-green-500/20 text-green-400"
                  : "bg-[var(--bg-primary)] text-[var(--text-secondary)]"
              }`}
            >
              {step.is_completed ? "\u2713" : "\u2022"}
            </span>
            <div className="min-w-0">
              <p
                className={`text-sm font-medium ${
                  step.is_completed
                    ? "text-green-400"
                    : "text-[var(--text-primary)]"
                }`}
              >
                {step.label}
                {step.is_required && !step.is_completed && (
                  <span className="ml-1 text-[10px] text-[var(--brand-gold)]">
                    Required
                  </span>
                )}
              </p>
              <p className="text-xs text-[var(--text-secondary)] truncate">
                {step.description}
              </p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
