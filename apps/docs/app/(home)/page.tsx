import Link from 'next/link';

export default function HomePage() {
  return (
    <div className="flex flex-col justify-center text-center flex-1 px-6">
      <h1 className="text-4xl font-bold mb-4">trpc-rs-py</h1>
      <p className="text-lg text-fd-muted-foreground mb-8 max-w-xl mx-auto">
        The Python backend for your tRPC app — define procedures in Python, call
        them from TypeScript with the official @trpc/client, and get end-to-end
        types that update live as you edit Python.
      </p>
      <div className="flex flex-row gap-4 justify-center">
        <Link
          href="/docs"
          className="rounded-lg bg-fd-primary px-5 py-2.5 font-medium text-fd-primary-foreground"
        >
          Read the docs
        </Link>
        <Link
          href="/docs/getting-started"
          className="rounded-lg border border-fd-border px-5 py-2.5 font-medium"
        >
          Getting started
        </Link>
      </div>
    </div>
  );
}
