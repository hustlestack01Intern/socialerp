import Link from "next/link";
import DraftBanner from "@/components/DraftBanner";

export const metadata = {
  title: "Privacy Notice - SocialERP",
  description: "What we collect, why, and what we still have to fix. DRAFT.",
};

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-white py-16">
      <div className="mx-auto max-w-3xl px-4 space-y-8">
        <h1 className="text-3xl font-bold text-gray-900">Privacy notice</h1>

        <DraftBanner>
          <p>
            This notice describes what the system does today, in plain words. It
            is not yet reviewed by a Kenyan data-protection lawyer, and two
            known gaps are listed below rather than hidden. We will publish the
            reviewed version before charging anyone.
          </p>
        </DraftBanner>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            What we collect
          </h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              Your business details: business name, your name, your email, your
              M-Pesa phone number.
            </li>
            <li>
              Your customers&rsquo; details, because your customers talk to us
              through your channels: their phone number or handle, the messages
              they send, the orders they place and the payments they make.
            </li>
            <li>
              An audit record of what the agent did: which rule allowed each
              action, what was sent, and to whom.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">Why we collect it</h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              To run the business you asked us to run: taking orders, holding
              stock, taking payment, and telling your customers what is
              happening with their order.
            </li>
            <li>
              To keep an auditable record of what the agent did on your behalf.
            </li>
            <li>To meet our legal and record-keeping obligations in Kenya.</li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">Who we share it with</h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              Safaricom, for M-Pesa payments you authorise (we never see your
              PIN).
            </li>
            <li>
              Meta, for WhatsApp and Instagram messaging (your messages travel
              through their infrastructure).
            </li>
            <li>
              Your riders, for the name, phone number and address needed to
              deliver an order.
            </li>
          </ul>
          <p className="text-sm text-gray-600">
            We do not sell your data, and we do not use your customers&rsquo;
            messages to train models.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">How long we keep it</h2>
          <p className="text-gray-700">
            Business records (orders, payments, audit entries) are kept for as
            long as your business is with us, and then for the period Kenyan
            law requires us to keep financial records. Conversation history is
            kept for the same period as the business record, so that a
            dispute months later can still be answered.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Your rights, and what we have not finished
          </h2>
          <p className="text-gray-700">
            You can ask us what we hold about your customers, ask us to correct
            it, or ask us to stop using it for marketing. Write to us and we
            will answer.
          </p>
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
            <p className="font-semibold">Two gaps we have not closed yet</p>
            <ul className="list-disc space-y-1 pl-5">
              <li>
                If you ask us to delete a customer&rsquo;s data, the deletion
                request itself is recorded (it has to be, to show we handled
                it), and the message delivery log that carries the phone
                number may survive in the delivery pipeline. We are fixing this.
              </li>
              <li>
                The same applies to stored conversations. We are working on
                both, and we will say so here when they are closed.
              </li>
            </ul>
            <p className="mt-2">
              We would rather show you the gap than write a sentence we cannot
              keep.
            </p>
          </div>
        </section>

        <p className="text-sm text-gray-600">
          See also our{" "}
          <Link href="/terms" className="text-primary-600 hover:underline">
            terms
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
