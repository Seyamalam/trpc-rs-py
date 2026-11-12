import { useState, type FormEvent } from "react";

// Generated from the SAME Python model that validates the mutation
// server-side: one constraint source, checked twice.
import { RegisterInput } from "../app-schemas";
import { trpc } from "./trpc";

type Fields = "username" | "email" | "age" | "newsletter";

export function App() {
  const [form, setForm] = useState({
    username: "",
    email: "",
    age: "",
    newsletter: false,
  });
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<Fields, string>>>({});

  const registrations = trpc.registrations.useQuery();
  const register = trpc.register.useMutation({
    onSuccess: () => registrations.refetch(),
  });

  const set = (key: Fields, value: string | boolean) =>
    setForm((current) => ({ ...current, [key]: value }));

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFieldErrors({});

    // Client-side check with the generated Zod schema. The same constraints
    // run server-side in Pydantic — try an invalid payload and the server
    // still rejects it (shown under "Server response").
    const parsed = RegisterInput.safeParse({
      username: form.username,
      email: form.email,
      age: Number(form.age),
      newsletter: form.newsletter,
    });
    if (!parsed.success) {
      const errors: Partial<Record<Fields, string>> = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0];
        if (typeof key === "string" && !(key in errors)) {
          errors[key as Fields] = issue.message;
        }
      }
      setFieldErrors(errors);
      return;
    }
    register.mutate({ form: parsed.data });
  };

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 480, margin: "2rem auto" }}>
      <h1>react-zod-form</h1>
      <p>
        Zod form validation generated from the same Python input schema the
        server enforces.
      </p>

      <form onSubmit={onSubmit}>
        <label>
          Username:{" "}
          <input
            value={form.username}
            onChange={(event) => set("username", event.target.value)}
          />
        </label>
        {fieldErrors.username ? <p role="alert">{fieldErrors.username}</p> : null}

        <br />
        <label>
          Email:{" "}
          <input
            value={form.email}
            onChange={(event) => set("email", event.target.value)}
          />
        </label>
        {fieldErrors.email ? <p role="alert">{fieldErrors.email}</p> : null}

        <br />
        <label>
          Age:{" "}
          <input
            type="number"
            value={form.age}
            onChange={(event) => set("age", event.target.value)}
          />
        </label>
        {fieldErrors.age ? <p role="alert">{fieldErrors.age}</p> : null}

        <br />
        <label>
          <input
            type="checkbox"
            checked={form.newsletter}
            onChange={(event) => set("newsletter", event.target.checked)}
          />{" "}
          Newsletter
        </label>

        <br />
        <button type="submit" disabled={register.isPending}>
          register
        </button>
      </form>

      <h2>Server response</h2>
      {register.error ? <p role="alert">{register.error.message}</p> : null}
      {register.data ? (
        <p>
          Registered #{register.data.id}: {register.data.username} (
          {register.data.email})
        </p>
      ) : null}

      <h2>registrations</h2>
      <ul>
        {(registrations.data ?? []).map((entry) => (
          <li key={entry.id}>
            #{entry.id} {entry.username} — {entry.email}
          </li>
        ))}
      </ul>
    </main>
  );
}
