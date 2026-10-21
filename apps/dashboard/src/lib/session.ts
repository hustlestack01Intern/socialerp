/**
 * Where the browser keeps the session (M1.5, changed by U79).
 *
 * The dashboard is the owner's browser, not the customer's: this is the
 * owner-console session, and it holds the owner's own tokens only. The server
 * still decides everything (plan, trial, entitlement) - the browser only
 * carries the token that lets it ask.
 *
 * **The refresh token is not kept here any more.** It used to live in
 * `localStorage`, where any script on the page could read it and where it
 * stayed after the tab was closed. The API now sets it in an `HttpOnly`
 * cookie, which script cannot read at all, and the browser sends it back
 * only to the auth endpoints. The dashboard never sees it.
 *
 * The access token is still kept in `localStorage`: it is short-lived (15
 * minutes) and the dashboard needs it for the requests it makes. That is the
 * residual risk of this design, and it is in the uncertainty ledger: the
 * next step is to move the access token into the cookie as well and let the
 * API authenticate the browser from the cookie alone.
 */

const ACCESS_TOKEN_KEY = "socialerp.access_token";

export type StoredSession = {
  accessToken: string;
};

export function saveSession(tokens: {
  access_token: string;
  refresh_token?: string;
}): void {
  if (typeof window === "undefined") {
    return;
  }
  // The refresh token is deliberately ignored: the API has put it in an
  // HttpOnly cookie, and the page must not keep a copy it could not read
  // back anyway.
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
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
}
