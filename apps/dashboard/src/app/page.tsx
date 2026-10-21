"use client";

import Link from "next/link";

export default function HomePage() {
  return (
    <main className="min-h-screen flex flex-col">
      {/* Hero Section */}
      <section className="relative overflow-hidden bg-gradient-to-b from-primary-50 to-white py-20 lg:py-32">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-3xl text-center">
            <h1 className="text-4xl font-bold tracking-tight text-gray-900 sm:text-6xl">
              Run Your Business <span className="text-primary-600">Inside WhatsApp</span>
            </h1>
            <p className="mt-6 text-lg leading-8 text-gray-600">
              SocialERP is an AI-powered ERP that lets Kenyan sellers manage inventory, orders,
              payments, and delivery — all inside the social channels your customers already use.
            </p>
            <div className="mt-10 flex items-center justify-center gap-4">
              <Link href="/signup" className="btn-primary text-lg px-8 py-3">
                Start Free Trial
              </Link>
              <Link href="/pricing" className="btn-secondary text-lg px-8 py-3">
                See Pricing
              </Link>
            </div>
            <p className="mt-4 text-sm text-gray-500">
              The first cohort is invite-only: you need a code from us. Your 14-day
              trial starts when you connect your WhatsApp number, not when you sign up.
            </p>
          </div>

          {/* What the system does (claims map to enforced behaviour) */}
          <div className="mt-16 grid grid-cols-2 gap-8 md:grid-cols-4">
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">14 days</div>
              <div className="text-sm text-gray-600">Free trial, from channel connect</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">Always on</div>
              <div className="text-sm text-gray-600">The agent never sleeps</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">M-Pesa</div>
              <div className="text-sm text-gray-600">STK Push, confirmed by callback</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">Per tenant</div>
              <div className="text-sm text-gray-600">
                Every request scoped to one business
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-20 lg:py-32 bg-white">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
              Everything You Need to Sell on Social
            </h2>
            <p className="mt-4 text-lg text-gray-600">
              One platform. Your customers never leave the chat.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-8 md:grid-cols-2 lg:grid-cols-3">
            {[
              {
                title: "Conversations",
                description:
                  "One inbox for WhatsApp and Instagram DMs. The agent answers, takes orders and asks for payment, and hands over to you when it is unsure.",
              },
              {
                title: "Inventory & Orders",
                description:
                  "Stock is a ledger, not a number that gets overwritten: every receipt, reservation and sale is recorded, so the last item cannot be oversold.",
              },
              {
                title: "M-Pesa Payments",
                description:
                  "STK Push at order time or at delivery. Callbacks are checked against the order, replayed callbacks cannot credit you twice, and every day is reconciled.",
              },
              {
                title: "Delivery & Riders",
                description:
                  "Riders work over WhatsApp: picked up, arrived, delivered. Customers get a status message at each step.",
              },
              {
                title: "Customer Memory",
                description:
                  "One profile per customer, with the evidence behind what we remember: what they bought, when they reorder, what they prefer.",
              },
              {
                title: "Owner Console",
                description:
                  "Ask your business in plain language: “how many orders today?”, “restock 20 of the black hoodie”.",
              },
            ].map((feature) => (
              <div key={feature.title} className="card">
                <div className="w-12 h-12 bg-primary-100 rounded-lg flex items-center justify-center mb-4 text-primary-600">
                  {/* Icon placeholder */}
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                  </svg>
                </div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">{feature.title}</h3>
                <p className="text-gray-600">{feature.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-20 bg-primary-600">
        <div className="mx-auto max-w-3xl px-4 text-center">
          <h2 className="text-3xl font-bold text-white sm:text-4xl">
            Ready to start selling smarter?
          </h2>
          <p className="mt-4 text-lg text-primary-100">
            We are onboarding the first cohort by hand, one business at a time. No
            credit card, and your trial only starts when you connect your number.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row gap-4 justify-center">
            <Link href="/signup" className="btn-secondary text-lg px-8 py-3">
              Start with your invite code
            </Link>
            <Link href="/pricing" className="text-lg px-8 py-3 text-white underline">
              What it costs
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-gray-900 text-gray-400 py-12">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
            <div>
              <h3 className="text-white font-semibold mb-4">SocialERP</h3>
              <p className="text-sm">Agentic ERP for Kenyan social commerce sellers.</p>
            </div>
            <div>
              <h4 className="text-white font-medium mb-4">Product</h4>
              <ul className="space-y-2 text-sm">
                <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
                <li><Link href="/signup" className="hover:text-white">Start your trial</Link></li>
                <li><Link href="/login" className="hover:text-white">Log in</Link></li>
              </ul>
            </div>
            <div>
              <h4 className="text-white font-medium mb-4">What we enforce</h4>
              <ul className="space-y-2 text-sm">
                <li><Link href="/security" className="hover:text-white">Security</Link></li>
              </ul>
            </div>
            <div>
              <h4 className="text-white font-medium mb-4">Legal (draft)</h4>
              <ul className="space-y-2 text-sm">
                <li><Link href="/privacy" className="hover:text-white">Privacy notice</Link></li>
                <li><Link href="/terms" className="hover:text-white">Terms of service</Link></li>
                <li><Link href="/dpa" className="hover:text-white">Data protection</Link></li>
              </ul>
            </div>
          </div>
          <div className="mt-8 border-t border-gray-800 pt-8 text-center text-sm">
            © 2026 SocialERP. Built for Kenyan sellers.
          </div>
        </div>
      </footer>
    </main>
  );
}
