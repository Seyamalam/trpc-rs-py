"use client";

import { useEffect, useState } from "react";

import { trpc } from "../lib/trpc";

export default function Home() {
  const [greeting, setGreeting] = useState<string>("loading…");

  useEffect(() => {
    trpc.greeting.query({ name: "world" }).then(setGreeting).catch(console.error);
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", padding: "2rem" }}>
      <h1>{"{{name}}"}</h1>
      <p>{greeting}</p>
    </main>
  );
}
