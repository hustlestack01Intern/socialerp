/**
 * The one place the dashboard talks to the API.
 *
 * Rules this file follows (M1.5):
 * - the base URL comes from the environment, never from the user's input;
 * - errors are read from the API's `detail` field, and an API that cannot be
 *   reached is reported as "cannot reach the server" - never as a fake
 *   success;
 * - nothing here computes money, stock or entitlements: the server decides,
 *   this file only carries the answer.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

export type SubscriptionView = {
  plan: string;
  status: string;
  period_start: string | null;
  period_end: string | null;
  days_remaining: number | null;
  entitled: boolean;
  entitlement_reason: string;
  extension_count: number;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: string };
    if (typeof data.detail === "string") {
      return data.detail;
    }
    if (Array.isArray(data.detail)) {
      // FastAPI validation errors: a list of {loc, msg}.
      const first = data.detail[0] as { msg?: string } | undefined;
      if (first && typeof first.msg === "string") {
        return first.msg;
      }
    }
  } catch {
    // Fall through: the body was not JSON.
  }
  return `The server refused the request (HTTP ${response.status}).`;
}

/** Log in with email + password (the API takes OAuth2 form fields). */
export async function login(
  email: string,
  password: string,
): Promise<TokenResponse> {
  const body = new URLSearchParams({ username: email, password });
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
  } catch {
    throw new ApiError("We could not reach the server. Check your connection.", 0);
  }
  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  return (await response.json()) as TokenResponse;
}

export type RegisterInput = {
  business_name: string;
  phone: string;
  email: string;
  owner_name: string;
  owner_email: string;
  owner_password: string;
  invite_code?: string;
};

/** Create a business account. The trial clock starts when a channel connects. */
export async function register(
  input: RegisterInput,
): Promise<TokenResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  } catch {
    throw new ApiError("We could not reach the server. Check your connection.", 0);
  }
  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  return (await response.json()) as TokenResponse;
}

/** The server's own trial decision. The client never computes it. */
export async function getSubscription(
  accessToken: string,
): Promise<SubscriptionView> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/billing/subscription`, {
      method: "GET",
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store",
    });
  } catch {
    throw new ApiError("We could not reach the server. Check your connection.", 0);
  }
  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  return (await response.json()) as SubscriptionView;
}
