"use client";

import { useState, useRef, DragEvent } from "react";
import { Upload, X, CheckCircle, AlertTriangle, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface ImportResult {
  total_rows: number;
  imported: number;
  skipped: number;
  success: boolean;
  errors: { row: number; column: string; value: string; reason: string }[];
  warnings: string[];
  created_ids: string[];
}

interface Props {
  onClose: () => void;
  onSuccess: () => void;
}

export function CalendarImportModal({ onClose, onSuccess }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [campaignId, setCampaignId] = useState("");
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f?.name.endsWith(".csv")) setFile(f);
  };

  const handleImport = async () => {
    if (!file) return;
    setImporting(true);
    try {
      const form = new FormData();
      form.append("file", file);
      if (campaignId) form.append("campaign_id", campaignId);

      const res = await fetch(`${API_BASE}/api/v1/calendar/import`, {
        method: "POST",
        body: form,
      });
      const data: ImportResult = await res.json();
      setResult(data);
    } catch {
      setResult({
        total_rows: 0,
        imported: 0,
        skipped: 0,
        success: false,
        errors: [
          {
            row: 0,
            column: "",
            value: "",
            reason: "Network error — is the API running?",
          },
        ],
        warnings: [],
        created_ids: [],
      });
    } finally {
      setImporting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && !importing && onClose()}
    >
      <div className="w-full max-w-lg rounded-xl border border-[var(--border-color)] bg-white p-6 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">
            Import Content Calendar
          </h2>
          <button
            onClick={onClose}
            className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            disabled={importing}
          >
            <X size={20} />
          </button>
        </div>

        {!result ? (
          <>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => inputRef.current?.click()}
              className={cn(
                "mt-4 flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 transition-colors",
                dragOver
                  ? "border-[var(--brand-gold)] bg-[var(--brand-gold)]/10"
                  : "border-[var(--border-color)] hover:border-[var(--border-color)]",
                file && "border-green-600 bg-green-900/10"
              )}
            >
              <Upload
                size={24}
                className={file ? "text-green-600" : "text-[var(--text-secondary)]"}
              />
              {file ? (
                <p className="text-sm text-green-600">{file.name}</p>
              ) : (
                <>
                  <p className="text-sm text-[var(--text-primary)]">
                    Drop your CSV here or click to browse
                  </p>
                  <p className="text-xs text-[var(--text-secondary)]">
                    Columns: date, time, type, title, platform, caption,
                    asset_ref, cta
                  </p>
                </>
              )}
              <input
                ref={inputRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </div>

            <div className="mt-4">
              <label className="text-xs font-medium text-[var(--text-secondary)]">
                Campaign ID (optional)
              </label>
              <input
                type="text"
                value={campaignId}
                onChange={(e) => setCampaignId(e.target.value)}
                placeholder="Link all items to a campaign..."
                className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:border-[var(--brand-gold)] focus:outline-none"
              />
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                Cancel
              </button>
              <button
                onClick={handleImport}
                disabled={!file || importing}
                className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {importing ? "Importing..." : "Import"}
              </button>
            </div>
          </>
        ) : (
          <div className="mt-4 space-y-4">
            {/* Summary banner */}
            <div
              className={cn(
                "flex items-center gap-2 rounded-lg p-3 text-sm",
                result.success
                  ? "bg-green-900/30 text-green-600"
                  : result.imported > 0
                    ? "bg-yellow-900/30 text-yellow-600"
                    : "bg-red-900/30 text-red-600"
              )}
            >
              {result.success ? (
                <CheckCircle size={16} />
              ) : result.imported > 0 ? (
                <AlertTriangle size={16} />
              ) : (
                <XCircle size={16} />
              )}
              Imported {result.imported} of {result.total_rows} rows
              {result.skipped > 0 && ` (${result.skipped} skipped)`}
            </div>

            {/* Warnings */}
            {result.warnings.length > 0 && (
              <div className="rounded-lg bg-yellow-900/20 p-3">
                <p className="text-xs font-medium text-yellow-600">Warnings</p>
                <ul className="mt-1 space-y-1">
                  {result.warnings.map((w, i) => (
                    <li key={i} className="text-xs text-yellow-300">
                      {w}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Errors table */}
            {result.errors.length > 0 && (
              <div className="rounded-lg bg-red-900/20 p-3">
                <p className="text-xs font-medium text-red-600">Errors</p>
                <div className="mt-2 max-h-40 overflow-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-red-600">
                        <th className="pb-1 pr-2">Row</th>
                        <th className="pb-1 pr-2">Column</th>
                        <th className="pb-1">Reason</th>
                      </tr>
                    </thead>
                    <tbody className="text-red-300">
                      {result.errors.map((e, i) => (
                        <tr key={i}>
                          <td className="py-0.5 pr-2">{e.row}</td>
                          <td className="py-0.5 pr-2">{e.column}</td>
                          <td className="py-0.5">{e.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-3">
              {result.imported > 0 && (
                <button
                  onClick={onSuccess}
                  className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white"
                >
                  View Calendar
                </button>
              )}
              <button
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
