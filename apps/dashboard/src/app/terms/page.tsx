import Link from "next/link";
import DraftBanner from "@/components/DraftBanner";

export const metadata = {
  title: "Terms of Service - SocialERP",
  description: "The terms you sign up under. DRAFT.",
};

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-white py-16">
      <div className="mx-auto max-w-3xl px-4 space-y-8">
        <h1 className="text-2xl font-bold text-gray-900">Terms of service</h1>

        <DraftBanner>
          <p>
            Draft terms, written by the founders. A Kenyan lawyer has not
            reviewed them yet, so they are published as a draft and we will
            send the reviewed version for signature before you pay for
            anything.
          </p>
        </DraftBanner>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            What we do for you
          </h2>
          <p className="text-gray-700">
            We run the software that sits between your customers and your
            business: conversations, stock, orders, M-Pesa payment requests and
            delivery updates, on the channels you connect. We do not sell for
            you, we do not hold your money, and we do not make the pricing
            decisions you make.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            What we do not do
          </h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              We do not take payment for your orders into our own account. The
              M-Pesa payment goes to your Till, exactly as if you had taken it
              yourself.
            </li>
            <li>
              We do not promise delivery times, stock levels or prices that the
              system has not confirmed.
            </li>
            <li>
              We do not train models on your customers&rsquo; messages.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">Your trial</h2>
          <p className="text-gray-700">
            The trial is 14 days and it starts when you connect your first
            channel, not when you sign up. One trial per business. When the
            trial ends, the agent stops transacting and your business keeps
            running; nothing is deleted and you can start a paid plan at any
            time.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">Your plan</h2>
          <p className="text-gray-700">
            Paid plans are monthly, in advance, and start on the payment date.
            We tell you what each plan includes on the{" "}
            <Link href="/pricing" className="text-primary-600 hover:underline">
              pricing page
            </Link>
            , and the system enforces it. Refunds are a human decision: ask us
            and a person will look at it.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            The agent is not a person
          </h2>
          <p className="text-gray-700">
            The agent is software. It can be wrong, and it is designed to hand
            over to a human when it is unsure. Refunds, discounts beyond your
            limits, price changes and bulk marketing are always approved by a
            person, always.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Ending the relationship
          </h2>
          <p className="text-gray-700">
            You can stop at any time. Tell us and we close the account and
            delete your data on request, except the records Kenyan law requires
            us to keep (invoices and payment records).
          </p>
        </section>

        <p className="text-sm text-gray-600">
          See also our{" "}
          <Link href="/privacy" className="text-primary-600 hover:underline">
            privacy notice
          </Link>
          ,{" "}
          <Link href="/dpa" className="text-primary-600 hover:underline">
            data protection
          </Link>{" "}
          and{" "}
          <Link href="/security" className="text-primary-600 hover:underline">
            security
          </Link>{" "}
          pages.
        </p>
      </div>
    </main>
  );
}
