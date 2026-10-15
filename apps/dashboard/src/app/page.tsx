"use client";

import { useState } from "react";
import Link from "next/link";

export default function HomePage() {
  const [email, setEmail] = useState("");

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
              <Link href="/onboarding" className="btn-primary text-lg px-8 py-3">
                Start Free Trial
              </Link>
              <Link href="/demo" className="btn-secondary text-lg px-8 py-3">
                Watch Demo
              </Link>
            </div>
          </div>

          {/* Trust Indicators */}
          <div className="mt-16 grid grid-cols-2 gap-8 md:grid-cols-4">
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">30 min</div>
              <div className="text-sm text-gray-600">Onboarding Time</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">24/7</div>
              <div className="text-sm text-gray-600">AI Agent Response</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">M-Pesa</div>
              <div className="text-sm text-gray-600">Native Integration</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-primary-600">100%</div>
              <div className="text-sm text-gray-600">Tenant Isolation</div>
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
              One platform. Zero app switching. Your customers never leave the chat.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-8 md:grid-cols-2 lg:grid-cols-3">
            {[
              {
                title: "Conversations",
                description: "Unified inbox for WhatsApp, Instagram DMs, and TikTok. AI agent handles FAQs, orders, and payments 24/7.",
                icon: "MessageSquare",
              },
              {
                title: "Inventory & Orders",
                description: "Live stock, reservations, and order state machine. Never oversell. Automatic stock updates from sales.",
                icon: "Package",
              },
              {
                title: "M-Pesa Payments",
                description: "STK Push at order or delivery. Callbacks, reconciliation, and fallback handling built-in.",
                icon: "CreditCard",
              },
              {
                title: "Delivery & Riders",
                description: "WhatsApp-based rider flow. Live tracking, proof of delivery, pay-on-delivery triggers.",
                icon: "Truck",
              },
              {
                title: "Customer Memory",
                description: "Unified profiles across channels. Preferences, history, RFM scores. Personalized offers.",
                icon: "Users",
              },
              {
                title: "Owner Console",
                description: "Run your business from WhatsApp. \"How many orders today?\" \"Mark order 1042 delivered.\"",
                icon: "Smartphone",
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
            Join the waitlist for early access. 30-minute onboarding. No credit card required.
          </p>
          <form className="mt-8 flex flex-col sm:flex-row gap-4 justify-center" onSubmit={(e) => { e.preventDefault(); alert("Thanks for joining the waitlist!"); }}>
            <input
              type="email"
              placeholder="your@email.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="flex-1 max-w-md px-4 py-3 rounded-lg text-gray-900 placeholder-gray-400"
              required
            />
            <button type="submit" className="bg-white text-primary-600 px-8 py-3 rounded-lg font-medium hover:bg-primary-50 transition-colors">
              Join Waitlist
            </button>
          </form>
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
                <li><Link href="/features" className="hover:text-white">Features</Link></li>
                <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
                <li><Link href="/docs" className="hover:text-white">Documentation</Link></li>
                <li><Link href="/api" className="hover:text-white">API Reference</Link></li>
              </ul>
            </div>
            <div>
              <h4 className="text-white font-medium mb-4">Company</h4>
              <ul className="space-y-2 text-sm">
                <li><Link href="/about" className="hover:text-white">About</Link></li>
                <li><Link href="/blog" className="hover:text-white">Blog</Link></li>
                <li><Link href="/careers" className="hover:text-white">Careers</Link></li>
                <li><Link href="/contact" className="hover:text-white">Contact</Link></li>
              </ul>
            </div>
            <div>
              <h4 className="text-white font-medium mb-4">Legal</h4>
              <ul className="space-y-2 text-sm">
                <li><Link href="/privacy" className="hover:text-white">Privacy Policy</Link></li>
                <li><Link href="/terms" className="hover:text-white">Terms of Service</Link></li>
                <li><Link href="/dpa" className="hover:text-white">Data Protection</Link></li>
                <li><Link href="/security" className="hover:text-white">Security</Link></li>
              </ul>
            </div>
          </div>
          <div className="mt-8 border-t border-gray-800 pt-8 text-center text-sm">
            © 2024 SocialERP. Built for Kenyan sellers.
          </div>
        </div>
      </footer>
    </main>
  );
}