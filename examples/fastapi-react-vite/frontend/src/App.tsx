import { useState } from "react";

import { trpc } from "./trpc";

export function App() {
  const [name, setName] = useState("Ada");

  const greeting = trpc.greeting.useQuery({ name });
  const user = trpc.user_by_id.useQuery({ user_id: 1 });
  const createUser = trpc.create_user.useMutation();

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 480, margin: "2rem auto" }}>
      <h1>fastapi-react-vite</h1>
      <p>
        React + <code>@trpc/react-query</code> against a Python FastAPI backend.
      </p>

      <label>
        Name:{" "}
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <p>{greeting.isPending ? "…" : greeting.data}</p>

      <button
        onClick={() => createUser.mutate({ user: { name, age: 36 } })}
        disabled={createUser.isPending}
      >
        create_user mutation
      </button>
      {createUser.data ? (
        <p>
          Created user #{createUser.data.id}: {createUser.data.name} (
          {createUser.data.age})
        </p>
      ) : null}
      {createUser.error ? <p>Error: {createUser.error.message}</p> : null}

      <h2>user_by_id(1)</h2>
      {user.data ? (
        <p>
          #{user.data.id} {user.data.name}, {user.data.age}
        </p>
      ) : (
        <p>{user.error?.message ?? "not created yet"}</p>
      )}
    </main>
  );
}
