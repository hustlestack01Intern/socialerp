"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import TrialStatusBanner from "@/components/TrialStatusBanner";
import { ApiError, login } from "@/lib/api";
import { saveSession } from "@/lib/session";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [accessToken, setAccessToken] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const tokens = await login(email, password);
      saveSession(tokens);
      setAccessToken(tokens.access_token);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(
          "That email is used by more than one business. Log in with the email of the business you want, or ask us to merge them.",
        );
      } else if (err instanceof ApiError && err.status === 401) {
        setError("That email and password do not match an account.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (accessToken) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
        <div className="w-full max-w-lg space-y-6">
          <TrialStatusBanner accessToken={accessToken} />
          <p className="text-sm text-gray-500">
            You are signed in. The owner console is being built; for now this
            page shows the state of your subscription.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md">
        <h1 className="text-2xl font-bold text-gray-900">Log in</h1>
        <p className="mt-2 text-sm text-gray-600">
          The email you signed up with.
        </p>

        <form className="mt-6 space-y-4" onSubmit={onSubmit}>
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input-field mt-1"
            />
          </div>
          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-gray-700"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field mt-1"
            />
          </div>

          {error ? (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          ) : null}

          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? "Signing in..." : "Log in"}
          </button>
        </form>

        <p className="mt-6 text-sm text-gray-600">
          No account yet?{" "}
          <Link href="/signup" className="text-primary-600 hover:underline">
            Sign up with your invite code
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
