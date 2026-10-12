import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildTranscript,
  describeMemory,
  extractTurns,
  renderMemoryContext,
  stripMemoryContext,
  MAX_PASSAGE_BYTES,
} from "../src/context.ts";

test("describeMemory shows the replaced value beside the current one", () => {
  const line = describeMemory({
    text: "Anup lives in Bellevue.",
    since: "2026-09-20",
    days_ago: 22,
    before: [{ text: "Anup lives in Seattle.", since: "2026-08-01" }],
  });
  assert.equal(line, 'Anup lives in Bellevue. (stated 2026-09-20; replaced "Anup lives in Seattle." from 2026-08-01)');
});

test("renderMemoryContext is empty for no memories and bounded by the limit", () => {
  assert.equal(renderMemoryContext([], 5), undefined);
  const block = renderMemoryContext(
    [
      { text: "A.", since: "2026-10-01", days_ago: 1 },
      { text: "B.", since: "2026-10-02", days_ago: 0 },
    ],
    1,
  );
  assert.ok(block?.startsWith("<relevant-memories>\n"));
  assert.ok(block?.endsWith("\n</relevant-memories>"));
  assert.match(block ?? "", /- A\. \(stated 2026-10-01\)/);
  assert.doesNotMatch(block ?? "", /- B\./);
});

test("stripMemoryContext removes injected blocks and keeps the message", () => {
  const injected = `${renderMemoryContext([{ text: "A.", since: "2026-10-01", days_ago: 1 }], 5)}\n\nWhat is my editor?`;
  assert.equal(stripMemoryContext(injected), "What is my editor?");
  assert.equal(stripMemoryContext("plain"), "plain");
});

test("extractTurns keeps user and assistant text from strings and text blocks", () => {
  const turns = extractTurns([
    { role: "user", content: "I moved to Bellevue." },
    { role: "assistant", content: [{ type: "text", text: "Noted." }, { type: "image", data: "x" }] },
    { role: "toolResult", content: "ignored" },
    { role: "user", content: [{ type: "image", data: "x" }] },
    null,
    "not a message",
  ]);
  assert.deepEqual(turns, [
    { role: "user", text: "I moved to Bellevue." },
    { role: "assistant", text: "Noted." },
  ]);
});

test("buildTranscript needs a user turn and takes only the latest messages", () => {
  assert.equal(buildTranscript([{ role: "assistant", content: "Hello" }], 10), undefined);
  const messages = [
    { role: "user", content: "old" },
    { role: "assistant", content: "older reply" },
    { role: "user", content: "I prefer vim." },
    { role: "assistant", content: "Got it." },
  ];
  assert.equal(buildTranscript(messages, 2), "User: I prefer vim.\n\nAssistant: Got it.");
  assert.equal(buildTranscript(messages, 10), "User: old\n\nAssistant: older reply\n\nUser: I prefer vim.\n\nAssistant: Got it.");
});

test("buildTranscript drops the oldest turns to stay under the server's limit", () => {
  const big = "x".repeat(3_000);
  const messages = Array.from({ length: 20 }, (_, i) => ({ role: i % 2 === 0 ? "user" : "assistant", content: `${i}:${big}` }));
  const passage = buildTranscript(messages, 20);
  assert.ok(passage !== undefined);
  assert.ok(Buffer.byteLength(passage, "utf8") <= MAX_PASSAGE_BYTES);
  assert.ok(passage.endsWith(`Assistant: 19:${big}`));
  assert.ok(!passage.startsWith("User: 0:"));
});
