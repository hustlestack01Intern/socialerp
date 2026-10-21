import Link from "next/link";
import DraftBanner from "@/components/DraftBanner";

export const metadata = {
  title: "Data Protection - SocialERP",
  description: "How we handle data under the Kenya Data Protection Act. DRAFT.",
};

export default function DpaPage() {
  return (
    <main className="min-h-screen bg-white py-16">
      <div className="mx-auto max-w-3xl px-4 space-y-8">
        <h1 className="text-2xl font-bold text-gray-900">
          Data protection (Kenya Data Protection Act)
        </h1>

        <DraftBanner>
          <p>
            This page is our reading of the Kenya Data Protection Act, 2019 and
            its regulations, written by the founders. It has not been reviewed
            by a Kenyan data-protection lawyer, and we have not yet registered
            with the Office of the Data Protection Commissioner. We are
            registering, and we will publish the lawyer&rsquo;s review here.
          </p>
        </DraftBanner>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Who is the controller, and who is the processor
          </h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              <span className="font-medium">You are the controller</span> for
              your business data and for your customers&rsquo; data: you decide
              what your business does with it.
            </li>
            <li>
              <span className="font-medium">We are the processor</span> for
              that data: we process it on your instructions, to run the service
              you bought.
            </li>
            <li>
              <span className="font-medium">We are the controller</span> for
              our own business records: our invoices, our own staff records and
              the logs of who used the platform.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">Lawful basis</h2>
          <ul className="list-disc space-y-2 pl-6 text-gray-700">
            <li>
              <span className="font-medium">Contract:</span> processing your
              business data and your customers&rsquo; order data, because that
              is the service you bought.
            </li>
            <li>
              <span className="font-medium">Legal obligation:</span> keeping
              payment and invoice records for the period Kenyan law requires.
            </li>
            <li>
              <span className="font-medium">Legitimate interests:</span>{" "}
              securing the service, preventing abuse (for example, stopping
              repeated trial sign-ups) and improving the product. You can ask
              us to stop.
            </li>
            <li>
              <span className="font-medium">Consent:</span> sending marketing
              messages to your customers. Without it, we send nothing. Every
              marketing message is a template that has been approved, and you
              can opt a customer out at any time.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Data minimisation
          </h2>
          <p className="text-gray-700">
            We collect the customer data your business needs to take an order
            and deliver it, and we keep it in your own tenant, separated from
            every other business by database-level rules. We do not use your
            customers&rsquo; data to build advertising profiles, and we do not
            sell it.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Data subject rights
          </h2>
          <p className="text-gray-700">
            Your customers can ask you for access to their data, correction,
            deletion or a copy. Ask us and we will help you answer them within
            the time the law allows. Two parts of erasure are still being
            finished (see the privacy notice), and we will not claim otherwise.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Cross-border transfers
          </h2>
          <p className="text-gray-700">
            Your conversations travel through Meta (WhatsApp, Instagram) and
            your payments through Safaricom, which means some of the data
            crosses borders as part of using those services. If you want the
            details of the infrastructure we use, ask us and we will send the
            current list.
          </p>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-gray-900">
            Breach response
          </h2>
          <p className="text-gray-700">
            If a breach affects personal data, we contain it, record what
            happened, and notify the affected customers and the Data Protection
            Commissioner within the period the law requires. We keep an audit
            trail of every agent action, which is what makes an investigation
            possible at all.
          </p>
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
          <Link href="/security" className="text-primary-600 hover:underline">
            security
          </Link>{" "}
          pages.
        </p>
      </div>
    </main>
  );
}
