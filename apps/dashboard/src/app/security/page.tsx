import Link from "next/link";

export const metadata = {
  title: "Security - SocialERP",
  description: "What is actually enforced: tenant isolation, audit, and what is not done yet.",
};

export default function SecurityPage() {
  return (
    <main className="min-h-screen bg-white py-16">
      <div className="mx-auto max-w-3xl px-4 space-y-8">
        <h1 className="text-2xl font-bold text-gray-900">Security</h1>
        <p className="text-gray-700">
          This page lists what the system enforces today, and what it does not
          yet. It is written from the code, not from a marketing plan: each
          line below has a test behind it.
        </p>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Tenant isolation (enforced)
          </h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              Every tenant table carries a tenant key, and PostgreSQL row-level
              security is enabled on it: a query without the right tenant
              context returns nothing.
            </li>
            <li>
              The API fails closed. With tenant isolation switched on, a request
              without a valid token is rejected before it reaches your data,
              and the tenant context comes from the signed token - never from
              the request body.
            </li>
            <li>
              The isolation tests attempt to read another tenant&rsquo;s rows
              on every run of the test suite. They must fail closed.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            The agent (enforced)
          </h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              The model never does the arithmetic. It extracts what the
              customer asked for; tested code checks it against your catalogue,
              computes the total, checks the stock and creates the order.
            </li>
            <li>
              Your customers&rsquo; messages are treated as data, never as
              instructions. An attempt to make the agent change its rules or
              reveal another customer&rsquo;s data is rejected and logged.
            </li>
            <li>
              Every action the agent takes is written to an append-only audit
              log with the rule that allowed it.
            </li>
            <li>
              When the agent is unsure, the model is down, or the request is
              unusual, it hands over to a human instead of improvising.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Money and stock (enforced)
          </h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              Stock is a ledger of movements, never an overwritten number, so
              the history of every item is auditable and a race for the last
              item cannot oversell it.
            </li>
            <li>
              Payment callbacks are checked against the order they claim to
              pay, and the same callback replayed twice cannot credit you
              twice.
            </li>
            <li>
              Refunds, discounts beyond your limit, price changes and bulk
              sends are always approved by a person.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Not done yet (we would rather say so)
          </h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              An independent penetration test. We have not had one yet; we will
              publish the report when we do.
            </li>
            <li>
              A published, complete erasure path: two records (the deletion
              request itself, and the delivery log that carries a phone
              number) still survive erasure today. See the privacy notice.
            </li>
            <li>
              Multi-factor authentication for owner accounts. Today a
              password is enough.
            </li>
          </ul>
        </section>

        <p className="text-sm text-gray-600">
          See also our{" "}
          <Link href="/privacy" className="text-primary-600 hover:underline">
            privacy notice
          </Link>
          ,{" "}
          <Link href="/terms" className="text-primary-600 hover:underline">
            terms
          </Link>{" "}
          and{" "}
          <Link href="/dpa" className="text-primary-600 hover:underline">
            data protection
          </Link>{" "}
          pages.
        </p>
      </div>
    </main>
  );
}
