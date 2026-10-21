"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getSubscription, type SubscriptionView } from "@/lib/api";

/**
 * The trial banner renders the SERVER's decision and nothing else (M1.5).
 *
 * - it never counts days itself: `days_remaining` comes from the API;
 * - on an API error it renders nothing at all (a banner that guesses is a
 *   lie, and a missing banner is not);
 * - it never blocks the page.
 */
export default function TrialStatusBanner({
  accessToken,
}: {
  accessToken: string;
}) {
  const [subscription, setSubscription] = useState<SubscriptionView | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSubscription(accessToken)
      .then((data) => {
        if (!cancelled) {
          setSubscription(data);
        }
      })
      .catch(() => {
        // The server could not be reached or refused: render nothing.
        if (!cancelled) {
          setSubscription(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  if (!subscription) {
    return null;
  }

  const { status, days_remaining, plan, entitled, entitlement_reason } =
    subscription;

  if (status === "pending") {
    return (
      <div className="card border-l-4 border-l-primary-600">
        <h2 className="text-lg font-semibold text-gray-900">
          Your trial has not started yet
        </h2>
        <p className="mt-2 text-sm text-gray-600">
          The {plan} trial starts when you connect your WhatsApp number, not
          when you sign up. Until then nothing is ticking.
        </p>
      </div>
    );
  }

  if (status === "active" && entitled) {
    return (
      <div className="card border-l-4 border-l-primary-600">
        <h2 className="text-lg font-semibold text-gray-900">
          {plan} is active
          {typeof days_remaining === "number"
            ? ` - ${days_remaining} day${days_remaining === 1 ? "" : "s"} left`
            : ""}
        </h2>
        <p className="mt-2 text-sm text-gray-600">
          {entitlement_reason || "Your plan is running."}
        </p>
      </div>
    );
  }

  return (
    <div className="card border-l-4 border-l-amber-500">
      <h2 className="text-lg font-semibold text-gray-900">
        Your plan is not running
      </h2>
      <p className="mt-2 text-sm text-gray-600">
        {entitlement_reason || "Talk to us and we will sort it out."}
      </p>
      <p className="mt-4">
        <Link href="/pricing" className="btn-primary">
          See the plans
        </Link>
      </p>
    </div>
  );
}
