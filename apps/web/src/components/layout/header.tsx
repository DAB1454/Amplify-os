"use client";

import { User } from "lucide-react";

import { NotificationsBell } from "./notifications-bell";

interface HeaderProps {
  title: string;
}

export function Header({ title }: HeaderProps) {
  return (
    <header className="flex items-center justify-between">
      <h1 className="text-2xl font-bold">{title}</h1>
      <div className="flex items-center gap-4">
        <NotificationsBell />
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--brand-gold)]/10 text-[var(--brand-gold)]">
          <User className="h-5 w-5" />
        </div>
      </div>
    </header>
  );
}
