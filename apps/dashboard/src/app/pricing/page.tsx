import Link from "next/link";
import pricing from "@/lib/pricing.json";

export const metadata = {
  title: "Pricing - SocialERP",
  description:
    "What each plan costs and what it includes - and, separately, what the system enforces today.",
};

// What each level means, in the words the code uses. L3 is a ceiling that is
// enforced; the optimisation actions behind it are not built yet, and the page
// must not say otherwise.
const AGENT_LEVEL_TEXT: Record<string, string> = {
  L0: "shadow mode - you approve every reply",
  L1: "answers questions, you approve the orders",
  L2: "takes orders and asks for payment",
  L3: "ceiling of L3 - the optimisation actions are not switched on yet",
};

// "api" means the read-only developer API (catalogue, stock, orders). No plan
// sells writes or inbound webhooks, so the label must not imply them.
const API_ACCESS_TEXT: Record<string, string> = {
  none: "not included",
  api: "read-only API (your catalogue, stock and orders)",
};

function PlanCard({ plan }: { plan: (typeof pricing.plans)[number] }) {
  return (
    <div className="card flex flex-col">
      <h2 className="text-xl font-semibold text-gray-900">{plan.name}</h2>
      <p className="mt-2 text-3xl font-bold text-gray-900">{plan.price_label}</p>
      <p className="text-sm text-gray-500">{plan.period}</p>

      <ul className="mt-6 space-y-2 text-sm text-gray-700">
        <li>
          <span className="font-medium">Channels:</span>{" "}
          {plan.channels.join(", ")}
        </li>
        <li>
          <span className="font-medium">Agent:</span> {plan.agent_level} - {AGENT_LEVEL_TEXT[plan.agent_level] ?? "not included"}
        </li>
        <li>
          <span className="font-medium">Insights:</span>{" "}
          {plan.insights ? "included" : "not included"}
        </li>
        <li>
          <span className="font-medium">API access:</span>{" "}
          {API_ACCESS_TEXT[plan.api_access] ?? plan.api_access}
        </li>
        <li>
          <span className="font-medium">Seats:</span>{" "}
          {plan.seats === null ? "unlimited" : plan.seats}
        </li>
      </ul>

      <p className="mt-4 text-sm text-gray-600">{plan.summary}</p>
      <p className="mt-2 text-sm text-gray-500">{plan.highlight}</p>

      <div className="mt-6 flex-1" />
      <Link href="/signup" className="btn-primary text-center">
        Start with {plan.name}
      </Link>
    </div>
  );
}

export default function PricingPage() {
  return (
    <main className="min-h-screen bg-white">
      <section className="bg-gray-50 py-16">
        <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
          <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
            Pricing
          </h1>
          <p className="mt-4 max-w-3xl text-lg text-gray-600">
            Each plan below lists what it includes: the channels you can connect,
            how much the agent may do on its own, whether you get API access and
            how many people can log in. What the software enforces today - and
            what it only records for now - is stated plainly underneath, so you
            can tell the difference between a promise and a switch.
          </p>
          <p className="mt-4 max-w-3xl text-sm text-gray-500">{pricing.trial_note}</p>
        </div>
      </section>

      <section className="py-16">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
            {pricing.plans.map((plan) => (
              <PlanCard key={plan.id} plan={plan} />
            ))}
          </div>
        </div>
      </section>

      <section className="bg-gray-50 py-16">
        <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
          <h2 className="text-2xl font-bold text-gray-900">In every plan</h2>
          <ul className="mt-4 space-y-2 text-gray-700">
            {pricing.included_in_every_plan.map((item) => (
              <li key={item} className="flex gap-2">
                <span aria-hidden="true" className="text-primary-600">
                  &#10003;
                </span>
                {item}
              </li>
            ))}
          </ul>

          <h2 className="mt-12 text-2xl font-bold text-gray-900">
            What the system enforces today
          </h2>
          <ul className="mt-4 space-y-2 text-gray-700">
            {pricing.enforced_today.map((item) => (
              <li key={item.feature} className="flex gap-2">
                <span aria-hidden="true" className="text-primary-600">
                  &#10003;
                </span>
                {item.statement}
              </li>
            ))}
          </ul>

          <h2 className="mt-12 text-2xl font-bold text-gray-900">
            Recorded on your plan, not switched on yet
          </h2>
          <ul className="mt-4 space-y-2 text-gray-700">
            {pricing.not_enforced_yet.map((item) => (
              <li key={item} className="flex gap-2">
                <span aria-hidden="true" className="text-gray-400">
                  &#8226;
                </span>
                {item}
              </li>
            ))}
          </ul>

          <h2 className="mt-12 text-2xl font-bold text-gray-900">
            Sold separately (not part of the subscription)
          </h2>
          <ul className="mt-4 space-y-4 text-gray-700">
            {pricing.sold_separately.map((item) => (
              <li key={item.label}>
                <span className="font-medium text-gray-900">{item.label}</span>
                <span className="block text-sm text-gray-600">{item.note}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </main>
  );
}
