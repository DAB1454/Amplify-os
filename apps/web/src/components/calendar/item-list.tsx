import type { CalendarItem } from "@/types";
import { cn } from "@/lib/utils";

// Color map for item types
const typeColors: Record<string, string> = {
  post: "bg-blue-100 text-blue-600",
  story: "bg-purple-100 text-purple-600",
  reel: "bg-pink-500/20 text-pink-600",
  email: "bg-green-100 text-green-600",
  ad: "bg-orange-100 text-orange-600",
  milestone: "bg-[var(--brand-gold)]/20 text-[var(--brand-gold)]",
  release: "bg-red-100 text-red-600",
  deadline: "bg-yellow-100 text-yellow-600",
  reminder: "bg-gray-100 text-gray-500",
};

interface Props {
  items: CalendarItem[];
}

export function CalendarItemList({ items }: Props) {
  // Group by date
  const grouped = items.reduce<Record<string, CalendarItem[]>>((acc, item) => {
    const dateKey = item.scheduled_date;
    if (!acc[dateKey]) acc[dateKey] = [];
    acc[dateKey].push(item);
    return acc;
  }, {});

  const sortedDates = Object.keys(grouped).sort();

  return (
    <div className="mt-6 space-y-6">
      {sortedDates.map((dateStr) => {
        const dateObj = new Date(dateStr + "T00:00:00");
        const formatted = dateObj.toLocaleDateString("en-US", {
          weekday: "short",
          month: "short",
          day: "numeric",
          year: "numeric",
        });
        const isToday = dateStr === new Date().toISOString().split("T")[0];

        return (
          <div key={dateStr}>
            <div className="flex items-center gap-3">
              <h3
                className={cn(
                  "text-sm font-semibold",
                  isToday ? "text-[var(--brand-gold)]" : "text-[var(--text-primary)]"
                )}
              >
                {formatted}
              </h3>
              {isToday && (
                <span className="rounded bg-[var(--brand-gold)]/20 px-2 py-0.5 text-xs text-[var(--brand-gold)]">
                  Today
                </span>
              )}
              <div className="h-px flex-1 bg-[var(--bg-surface)]" />
            </div>

            <div className="mt-2 space-y-2">
              {grouped[dateStr].map((item) => {
                // Extract platform from description [PLATFORM] prefix
                const platformMatch = item.description?.match(/^\[([A-Z]+)\]/);
                const platform = platformMatch?.[1];
                const descWithoutPlatform = item.description
                  ?.replace(/^\[[A-Z]+\]\n?/, "")
                  .trim();

                return (
                  <div
                    key={item.id}
                    className={cn(
                      "flex items-start gap-3 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] p-3 transition-colors hover:border-[var(--border-color)]",
                      item.is_completed && "opacity-50"
                    )}
                  >
                    {/* Time */}
                    <div className="w-16 flex-shrink-0 text-xs text-[var(--text-secondary)]">
                      {item.scheduled_time?.slice(0, 5) || "\u2014"}
                    </div>

                    {/* Type badge */}
                    <span
                      className={cn(
                        "flex-shrink-0 rounded px-2 py-0.5 text-xs font-medium",
                        typeColors[item.item_type] || typeColors.reminder
                      )}
                    >
                      {item.item_type}
                    </span>

                    {/* Content */}
                    <div className="min-w-0 flex-1">
                      <p
                        className={cn(
                          "text-sm font-medium text-white",
                          item.is_completed && "line-through"
                        )}
                      >
                        {item.title}
                      </p>
                      {descWithoutPlatform && (
                        <p className="mt-1 line-clamp-2 text-xs text-[var(--text-secondary)]">
                          {descWithoutPlatform}
                        </p>
                      )}
                    </div>

                    {/* Platform tag */}
                    {platform && (
                      <span className="flex-shrink-0 rounded bg-[var(--bg-surface)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
                        {platform}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
