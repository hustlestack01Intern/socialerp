import { ReactNode } from "react";

/**
 * The DRAFT banner for the legal pages (M1.5).
 *
 * These pages are written by the founders, not by a Kenyan data-protection
 * lawyer, and they are published as DRAFT so that nobody mistakes them for
 * reviewed legal text. While the open items (U51, U52) are unresolved the
 * pages must not claim things the system does not do - see the pages
 * themselves.
 */
export default function DraftBanner({ children }: { children: ReactNode }) {
  return (
    <div
      role="note"
      className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
    >
      <p className="font-semibold">
        DRAFT - pending review by a Kenyan data-protection lawyer.
      </p>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}
