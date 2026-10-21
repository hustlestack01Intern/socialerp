/** @type {import('next').NextConfig} */

// The browser's own protections (U79).
//
// The refresh token now lives in an HttpOnly cookie, so script cannot read
// it. These headers are the other half of that: they say which scripts may
// run, so an injected script has nowhere to send what it steals.
//
// The CSP is deliberately not `unsafe-eval` in production: Next.js does not
// need it outside development, and it is the difference between "an injected
// string is inert" and "an injected string is code".
const isDev = process.env.NODE_ENV !== "production";

const contentSecurityPolicy = [
  "default-src 'self'",
  // Next.js injects its bootstrap as an inline script, so 'unsafe-inline' is
  // needed for the styles and the bootstrap. The nonce-based version is the
  // next step (recorded in the uncertainty ledger).
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  // The dashboard talks to the API through the Next rewrite, so it is
  // same-origin: 'self' is enough.
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // The owner console may use the camera (catalogue photos) and the
  // location (delivery), and nothing else.
  {
    key: "Permissions-Policy",
    value: "camera=(self), geolocation=(self), microphone=(), payment=()",
  },
  ...(isDev
    ? []
    : [
        {
          key: "Strict-Transport-Security",
          value: "max-age=31536000; includeSubDomains",
        },
      ]),
];

const nextConfig = {
  reactStrictMode: true,
  images: {
    domains: ["localhost"],
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

module.exports = nextConfig;
