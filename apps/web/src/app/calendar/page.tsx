"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { CalendarImportModal } from "@/components/calendar/import-modal";
import { CalendarItemList } from "@/components/calendar/item-list";
import { cn } from "@/lib/utils";
import { apiGet } from "@/lib/api";
import type { CalendarItem } from "@/types";
import { Upload } from "lucide-react";

const views = ["Week", "Month"] as const;

export default function CalendarPage() {
  const [view, setView] = useState<(typeof views)[number]>("Month");
  const [showImport, setShowImport] = useState(false);
  const [items, setItems] = useState<CalendarItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<CalendarItem[]>("/api/v1/calendar");
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load calendar");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  return (
    <>
      <Header title="Calendar" />
      <div className="mt-8 flex items-center justify-between">
        <div className="flex gap-2">
          {views.map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn(
                "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                view === v
                  ? "bg-[var(--brand-gold)] text-white"
                  : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
              )}
            >
              {v}
            </button>
          ))}
        </div>
        <button
          onClick={() => setShowImport(true)}
          className="flex items-center gap-2 rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
        >
          <Upload size={16} />
          Import CSV
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {loading ? (
        <div className="mt-8 text-center text-[var(--text-secondary)]">Loading...</div>
      ) : items.length === 0 ? (
        <div className="mt-8 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-12 text-center">
          <p className="text-[var(--text-secondary)]">No calendar items yet.</p>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            Import a CSV to populate your content calendar.
          </p>
        </div>
      ) : (
        <CalendarItemList items={items} />
      )}

      {showImport && (
        <CalendarImportModal
          onClose={() => setShowImport(false)}
          onSuccess={() => {
            setShowImport(false);
            fetchItems();
          }}
        />
      )}
    </>
  );
}
