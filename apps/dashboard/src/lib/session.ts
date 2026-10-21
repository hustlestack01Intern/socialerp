/**
 * Where the browser keeps the session (M1.5).
 *
 * The dashboard is the owner's browser, not the customer's: this is the
 * owner-console session, and it holds the owner's own tokens only. The server
 * still decides everything (plan, trial, entitlement) - the browser only
 * carries the token that lets it ask.
 */

const ACCESS_TOKEN_KEY = "socialerp.access_token";
const REFRESH_TOKEN_KEY = "socialerp.refresh_token";

export type StoredSession = {
  accessToken: string;
  refreshToken: string;
};

export function saveSession(tokens: {
  access_token: string;
  refresh_token: string;
}): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function clearSession(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}
