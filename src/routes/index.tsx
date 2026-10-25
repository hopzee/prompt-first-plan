import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "FarmTrace — how to run the Python app" },
      {
        name: "description",
        content:
          "FarmTrace is a Python/Streamlit app for farm produce transport and QR traceability in Nigeria. This page explains how to run it.",
      },
      { property: "og:title", content: "FarmTrace — how to run the Python app" },
      {
        property: "og:description",
        content: "Setup steps for the FarmTrace Streamlit app: database, migrations, admin account and launch.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Index,
});

const steps: { title: string; command: string }[] = [
  { title: "Install the Python packages", command: "pip install -r requirements.txt" },
  { title: "Add your PostgreSQL connection string to .env", command: "cp .env.example .env" },
  { title: "Create the database tables", command: "alembic upgrade head" },
  {
    title: "Create the first administrator",
    command: 'python scripts/create_admin.py --email you@domain.ng --name "Your Name"',
  },
  { title: "Start FarmTrace", command: "streamlit run app.py" },
];

function Index() {
  return (
    <main className="min-h-screen bg-background px-6 py-16 text-foreground">
      <div className="mx-auto max-w-2xl">
        <p className="text-sm font-semibold uppercase tracking-widest text-primary">FarmTrace · Nigeria</p>
        <h1 className="mt-3 text-4xl font-bold tracking-tight">FarmTrace runs as a Python app</h1>
        <p className="mt-4 text-lg text-muted-foreground">
          This preview window can't run Python, so the FarmTrace app itself doesn't appear here. Run it on your
          computer or on Streamlit Community Cloud with the steps below. The full guide is in the README.
        </p>

        <ol className="mt-10 space-y-4">
          {steps.map((step, index) => (
            <li key={step.command} className="rounded-lg border border-border bg-card p-4">
              <p className="font-medium text-card-foreground">
                {index + 1}. {step.title}
              </p>
              <code className="mt-2 block overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-sm text-muted-foreground">
                {step.command}
              </code>
            </li>
          ))}
        </ol>

        <p className="mt-10 text-sm text-muted-foreground">
          You need your own PostgreSQL database (for example Supabase or Neon). None has been created for you yet.
        </p>
      </div>
    </main>
  );
}
