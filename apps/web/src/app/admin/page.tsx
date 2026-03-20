"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";

interface PlatformStats {
  tenants: number;
  active_tenants: number;
  artists: number;
  campaigns: number;
  posts: number;
  channels: number;
  tier_breakdown: Record<string, number>;
}

interface TenantSummary {
  id: string;
  name: string;
  slug: string;
  plan_tier: string;
  is_active: boolean;
  onboarding_completed: boolean;
  artists: number;
  campaigns: number;
  channels: number;
  members: number;
  created_at: string | null;
}

const tierBadge: Record<string, string> = {
  solo: "bg-gray-500/20 text-gray-400",
  pro: "bg-[var(--brand-gold)]/20 text-[var(--brand-gold)]",
  agency: "bg-purple-500/20 text-purple-400",
};

export default function AdminPage() {
  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [tierFilter, setTierFilter] = useState<string>("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const filter = tierFilter ? `?plan_tier=${tierFilter}` : "";
      const [s, t] = await Promise.all([
        apiGet<PlatformStats>("/api/v1/admin/stats"),
        apiGet<TenantSummary[]>(`/api/v1/admin/tenants${filter}`),
      ]);
      setStats(s);
      setTenants(t);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [tierFilter]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleToggleActive = async (tenantId: string) => {
    try {
      await apiPost(`/api/v1/admin/tenants/${tenantId}/toggle-active`, {});
      fetchAll();
    } catch { /* ignore */ }
  };

  const handleChangePlan = async (tenantId: string, tier: string) => {
    try {
      await apiPost(`/api/v1/admin/tenants/${tenantId}/change-plan?plan_tier=${tier}`, {});
      fetchAll();
    } catch { /* ignore */ }
  };

  return (
    <>
      <Header title="Admin Console" />

      {loading ? (
        <div className="mt-6 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
          Loading...
        </div>
      ) : (
        <>
          {/* Platform stats */}
          {stats && (
            <div className="mt-8 grid grid-cols-3 gap-4 lg:grid-cols-6">
              {[
                { label: "Tenants", value: stats.tenants },
                { label: "Active", value: stats.active_tenants },
                { label: "Artists", value: stats.artists },
                { label: "Campaigns", value: stats.campaigns },
                { label: "Posts", value: stats.posts },
                { label: "Channels", value: stats.channels },
              ].map((s) => (
                <div key={s.label} className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4">
                  <p className="text-xs text-[var(--text-secondary)]">{s.label}</p>
                  <p className="mt-1 text-2xl font-bold text-[var(--text-primary)]">{s.value}</p>
                </div>
              ))}
            </div>
          )}

          {/* Tier breakdown */}
          {stats && Object.keys(stats.tier_breakdown).length > 0 && (
            <div className="mt-4 flex gap-3">
              {Object.entries(stats.tier_breakdown).map(([tier, count]) => (
                <div key={tier} className="flex items-center gap-2">
                  <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", tierBadge[tier] || "")}>
                    {tier}
                  </span>
                  <span className="text-sm text-[var(--text-secondary)]">{count}</span>
                </div>
              ))}
            </div>
          )}

          {/* Filter */}
          <div className="mt-8 flex items-center gap-3">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Tenants</h3>
            <div className="ml-auto flex gap-2">
              {["", "solo", "pro", "agency"].map((t) => (
                <button
                  key={t}
                  onClick={() => setTierFilter(t)}
                  className={cn(
                    "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                    tierFilter === t
                      ? "bg-[var(--brand-gold)] text-black"
                      : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
                  )}
                >
                  {t || "All"}
                </button>
              ))}
            </div>
          </div>

          {/* Tenant list */}
          <div className="mt-4 space-y-2">
            {tenants.map((t) => (
              <div
                key={t.id}
                className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-3"
              >
                <div className="flex items-center gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-primary)]">
                        {t.name}
                      </span>
                      <span className="text-xs text-[var(--text-secondary)]">
                        ({t.slug})
                      </span>
                      <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", tierBadge[t.plan_tier] || "")}>
                        {t.plan_tier}
                      </span>
                      {!t.is_active && (
                        <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-xs text-red-400">
                          disabled
                        </span>
                      )}
                      {!t.onboarding_completed && (
                        <span className="rounded-full bg-yellow-500/20 px-2 py-0.5 text-xs text-yellow-400">
                          onboarding
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex gap-4 text-xs text-[var(--text-secondary)]">
                      <span>{t.artists} artists</span>
                      <span>{t.campaigns} campaigns</span>
                      <span>{t.channels} channels</span>
                      <span>{t.members} members</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <select
                      value={t.plan_tier}
                      onChange={(e) => handleChangePlan(t.id, e.target.value)}
                      className="rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-xs text-[var(--text-primary)]"
                    >
                      <option value="solo">Solo</option>
                      <option value="pro">Pro</option>
                      <option value="agency">Agency</option>
                    </select>
                    <button
                      onClick={() => handleToggleActive(t.id)}
                      className={cn(
                        "rounded px-2 py-1 text-xs font-medium transition-colors",
                        t.is_active
                          ? "bg-red-500/20 text-red-400 hover:bg-red-500/30"
                          : "bg-green-500/20 text-green-400 hover:bg-green-500/30"
                      )}
                    >
                      {t.is_active ? "Disable" : "Enable"}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {tenants.length === 0 && (
            <div className="mt-4 rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
              No tenants found.
            </div>
          )}
        </>
      )}
    </>
  );
}
