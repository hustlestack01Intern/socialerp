"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import TrialStatusBanner from "@/components/TrialStatusBanner";
import { ApiError, register } from "@/lib/api";
import { saveSession } from "@/lib/session";

// The API's own contract (RegisterRequest): the browser checks the same
// shape so the prospect gets a clear message, but the server checks it again
// and the server wins.
const PHONE_PATTERN = /^\+?254[0-9]{9}$/;
const MIN_PASSWORD_LENGTH = 12;

export default function SignupPage() {
  const [businessName, setBusinessName] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [email, setEmail] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [accessToken, setAccessToken] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!PHONE_PATTERN.test(phone)) {
      setError("The phone number must be a Kenyan number, like 254712000111.");
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(
        `The password must be at least ${MIN_PASSWORD_LENGTH} characters long.`,
      );
      return;
    }

    setBusy(true);
    try {
      const tokens = await register({
        business_name: businessName,
        phone,
        email,
        owner_name: ownerName,
        owner_email: ownerEmail,
        owner_password: password,
        invite_code: inviteCode.trim() || undefined,
      });
      saveSession(tokens);
      setAccessToken(tokens.access_token);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError(
          "That invite code is not valid. Ask us for a fresh one - each code works once.",
        );
      } else if (err instanceof ApiError && err.status === 409) {
        setError(
          "That email or business address is already in use. Log in instead, or use a different email.",
        );
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
          <div className="card">
            <h1 className="text-2xl font-bold text-gray-900">
              Your business is set up
            </h1>
            <p className="mt-2 text-sm text-gray-600">
              Nothing is ticking yet: the trial starts when you connect your
              WhatsApp number, not when you sign up. We will walk you through
              the connection - it is a QR scan and it keeps the number you
              already use with the WhatsApp Business app.
            </p>
          </div>
          <TrialStatusBanner accessToken={accessToken} />
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4 py-12">
      <div className="w-full max-w-md">
        <h1 className="text-2xl font-bold text-gray-900">Start your trial</h1>
        <p className="mt-2 text-sm text-gray-600">
          The first cohort is invite-only: enter the code we gave you. Each
          code works once.
        </p>

        <form className="mt-6 space-y-4" onSubmit={onSubmit}>
          <div>
            <label
              htmlFor="business_name"
              className="block text-sm font-medium text-gray-700"
            >
              Business name
            </label>
            <input
              id="business_name"
              required
              value={businessName}
              onChange={(e) => setBusinessName(e.target.value)}
              className="input-field mt-1"
            />
          </div>
          <div>
            <label
              htmlFor="owner_name"
              className="block text-sm font-medium text-gray-700"
            >
              Your name
            </label>
            <input
              id="owner_name"
              required
              value={ownerName}
              onChange={(e) => setOwnerName(e.target.value)}
              className="input-field mt-1"
            />
          </div>
          <div>
            <label
              htmlFor="owner_email"
              className="block text-sm font-medium text-gray-700"
            >
              Your email (you will log in with this)
            </label>
            <input
              id="owner_email"
              type="email"
              required
              value={ownerEmail}
              onChange={(e) => setOwnerEmail(e.target.value)}
              className="input-field mt-1"
            />
          </div>
          <div>
            <label
              htmlFor="email"
              className="block text-sm font-medium text-gray-700"
            >
              Business email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input-field mt-1"
            />
          </div>
          <div>
            <label
              htmlFor="phone"
              className="block text-sm font-medium text-gray-700"
            >
              M-Pesa number (254712000111)
            </label>
            <input
              id="phone"
              required
              placeholder="254712000111"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              className="input-field mt-1"
            />
          </div>
          <div>
            <label
              htmlFor="owner_password"
              className="block text-sm font-medium text-gray-700"
            >
              Password (at least {MIN_PASSWORD_LENGTH} characters)
            </label>
            <input
              id="owner_password"
              type="password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field mt-1"
            />
          </div>
          <div>
            <label
              htmlFor="invite_code"
              className="block text-sm font-medium text-gray-700"
            >
              Invite code
            </label>
            <input
              id="invite_code"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              className="input-field mt-1"
            />
          </div>

          {error ? (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          ) : null}

          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? "Setting up..." : "Create my business"}
          </button>
        </form>

        <p className="mt-6 text-sm text-gray-600">
          Already have an account?{" "}
          <Link href="/login" className="text-primary-600 hover:underline">
            Log in
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
