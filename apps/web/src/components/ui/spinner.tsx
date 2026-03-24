/**
 * Reusable loading spinner component.
 *
 * Usage:
 *   <Spinner />                  — default 20px indigo spinner
 *   <Spinner size={16} />        — small inline spinner
 *   <Spinner className="text-white" /> — white spinner for buttons
 *   <LoadingOverlay />           — full-area centered spinner with "Loading..." text
 *   <ButtonSpinner label="Creating..." /> — inline button content with spinner
 */

export function Spinner({
  size = 20,
  className = "text-[var(--brand-gold)]",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={`animate-spin ${className}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      width={size}
      height={size}
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

export function LoadingOverlay({ text = "Loading..." }: { text?: string }) {
  return (
    <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 flex flex-col items-center justify-center gap-3">
      <Spinner size={32} />
      <span className="text-sm text-[var(--text-secondary)]">{text}</span>
    </div>
  );
}

export function ButtonSpinner({
  label,
  spinnerClass = "text-white",
}: {
  label: string;
  spinnerClass?: string;
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <Spinner size={14} className={spinnerClass} />
      {label}
    </span>
  );
}
