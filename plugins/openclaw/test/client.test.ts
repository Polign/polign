import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { RecallClient, RecallError } from "../src/client.ts";

type Seen = { path: string; auth: string | undefined; body: unknown };

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

/** A stand-in for `recall serve` that records requests and answers from a table. */
async function fakeServer(
  answer: (path: string) => { status: number; body?: unknown; delayMs?: number; raw?: string },
): Promise<{ server: Server; url: string; seen: Seen[] }> {
  const seen: Seen[] = [];
  const server = createServer(async (req, res) => {
    const text = await readBody(req);
    seen.push({ path: req.url ?? "", auth: req.headers.authorization, body: text ? JSON.parse(text) : undefined });
    const a = answer(req.url ?? "");
    if (a.delayMs) await new Promise((r) => setTimeout(r, a.delayMs));
    res.statusCode = a.status;
    res.setHeader("Content-Type", "application/json");
    res.end(a.raw ?? JSON.stringify(a.body ?? {}));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const addr = server.address();
  if (!addr || typeof addr === "string") throw new Error("no address");
  return { server, url: `http://127.0.0.1:${addr.port}`, seen };
}

test("recall posts the question with the bearer key and returns the memories", async (t) => {
  const { server, url, seen } = await fakeServer(() => ({
    status: 200,
    body: { memories: [{ text: "Anup prefers vim.", since: "2026-10-01", days_ago: 11 }] },
  }));
  t.after(() => server.close());
  const client = new RecallClient({ url: `${url}/`, apiKey: "rcl_a_b" });
  const out = await client.recall({ question: "editor?", as_of: "2026-10-05T00:00:00Z" });
  assert.equal(out.memories[0]?.text, "Anup prefers vim.");
  assert.deepEqual(seen, [{ path: "/v1/recall", auth: "Bearer rcl_a_b", body: { question: "editor?", as_of: "2026-10-05T00:00:00Z" } }]);
});

test("remember and forget hit their verbs", async (t) => {
  const { server, url, seen } = await fakeServer((path) =>
    path === "/v1/remember"
      ? { status: 200, body: { remembered: [{ text: "Anup prefers vim.", replaces: ["Anup prefers emacs."] }] } }
      : { status: 200, body: { forgotten: ["Anup prefers vim."] } },
  );
  t.after(() => server.close());
  const client = new RecallClient({ url, apiKey: "k" });
  const r = await client.remember({ text: "I use vim now" });
  assert.deepEqual(r.remembered?.[0]?.replaces, ["Anup prefers emacs."]);
  const f = await client.forget({ text: "my editor" });
  assert.deepEqual(f.forgotten, ["Anup prefers vim."]);
  assert.deepEqual(seen.map((s) => s.path), ["/v1/remember", "/v1/forget"]);
});

test("server errors carry the code and message the server gave", async (t) => {
  const { server, url } = await fakeServer(() => ({
    status: 401,
    body: { error: { code: "unauthenticated", message: "unknown key" } },
  }));
  t.after(() => server.close());
  const client = new RecallClient({ url, apiKey: "k" });
  await assert.rejects(client.recall({ question: "x" }), (err: unknown) => {
    assert.ok(err instanceof RecallError);
    assert.equal(err.code, "unauthenticated");
    assert.equal(err.message, "unknown key");
    assert.equal(err.status, 401);
    return true;
  });
});

test("a missing key fails before any request", async () => {
  const client = new RecallClient({ url: "http://127.0.0.1:1", apiKey: "" });
  await assert.rejects(client.recall({ question: "x" }), (err: unknown) => err instanceof RecallError && err.code === "unauthenticated");
});

test("an unreachable server and a timeout are reported as unreachable", async (t) => {
  const down = new RecallClient({ url: "http://127.0.0.1:1", apiKey: "k", timeoutMs: 2_000 });
  await assert.rejects(down.recall({ question: "x" }), (err: unknown) => err instanceof RecallError && err.code === "unreachable");

  const { server, url } = await fakeServer(() => ({ status: 200, body: { memories: [] }, delayMs: 500 }));
  t.after(() => server.close());
  const slow = new RecallClient({ url, apiKey: "k", timeoutMs: 100 });
  await assert.rejects(slow.recall({ question: "x" }), (err: unknown) => err instanceof RecallError && err.code === "unreachable");
});

test("a non-JSON answer is refused", async (t) => {
  const { server, url } = await fakeServer(() => ({ status: 200, raw: "ok" }));
  t.after(() => server.close());
  const client = new RecallClient({ url, apiKey: "k" });
  await assert.rejects(client.recall({ question: "x" }), (err: unknown) => err instanceof RecallError && err.code === "bad_response");
  assert.equal(await client.healthy(), true);
});
