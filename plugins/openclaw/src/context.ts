// Turning Recall's answers into prompt context, and the conversation into a
// passage Recall can work facts out of.

import type { Memory } from "./client.ts";

export const MEMORY_BLOCK_OPEN = "<relevant-memories>";
export const MEMORY_BLOCK_CLOSE = "</relevant-memories>";

/** One memory as a line: the statement, when it was stated, and what it replaced. */
export function describeMemory(m: Memory): string {
  const parts: string[] = [];
  parts.push(`stated ${m.since}`);
  for (const b of m.before ?? []) {
    parts.push(`replaced "${b.text}" from ${b.since}`);
  }
  for (const e of m.elsewhere ?? []) {
    parts.push(`also believed: "${e.text}" from ${e.since}`);
  }
  return `${m.text} (${parts.join("; ")})`;
}

/**
 * The block put in front of the prompt. Undefined when there is nothing to say,
 * so the hook adds nothing.
 */
export function renderMemoryContext(memories: Memory[], limit: number): string | undefined {
  const picked = memories.slice(0, Math.max(1, limit));
  if (picked.length === 0) return undefined;
  const lines = picked.map((m) => `- ${describeMemory(m)}`);
  return [
    MEMORY_BLOCK_OPEN,
    "Remembered from earlier conversations, and possibly relevant now. A newer statement replaces an older one; where a value was replaced, the old one is shown so you can mention the correction when it matters. Use memory_search to ask for more.",
    ...lines,
    MEMORY_BLOCK_CLOSE,
  ].join("\n");
}

/** Removes any memory blocks a message carries, so captured text never feeds back. */
export function stripMemoryContext(text: string): string {
  const open = MEMORY_BLOCK_OPEN.replace(/[<>/]/g, "\\$&");
  const close = MEMORY_BLOCK_CLOSE.replace(/[<>/]/g, "\\$&");
  return text.replace(new RegExp(`${open}[\\s\\S]*?${close}\\s*`, "g"), "").trim();
}

export type Turn = { role: "user" | "assistant"; text: string };

/** The user and assistant text in OpenClaw's session messages, in order. */
export function extractTurns(messages: unknown[]): Turn[] {
  const turns: Turn[] = [];
  for (const raw of messages) {
    if (!raw || typeof raw !== "object") continue;
    const msg = raw as { role?: unknown; content?: unknown };
    if (msg.role !== "user" && msg.role !== "assistant") continue;
    const text = stripMemoryContext(contentText(msg.content));
    if (text === "") continue;
    turns.push({ role: msg.role, text });
  }
  return turns;
}

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const pieces: string[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    const b = block as { type?: unknown; text?: unknown };
    if (b.type === "text" && typeof b.text === "string") pieces.push(b.text);
  }
  return pieces.join("\n");
}

/** The server takes up to 32 KiB of text per remember call. */
export const MAX_PASSAGE_BYTES = 32_768;
const MAX_TURN_CHARS = 4_000;

/**
 * The latest turns as a passage, newest last, or undefined when there is no
 * user text to learn from. Long turns are cut, and older turns are dropped
 * first when the whole passage would not fit.
 */
export function buildTranscript(messages: unknown[], limit: number): string | undefined {
  const turns = extractTurns(messages).slice(-Math.max(1, limit));
  if (!turns.some((t) => t.role === "user")) return undefined;
  const lines = turns.map((t) => {
    const text = t.text.length > MAX_TURN_CHARS ? `${t.text.slice(0, MAX_TURN_CHARS)}…` : t.text;
    return `${t.role === "user" ? "User" : "Assistant"}: ${text}`;
  });
  while (lines.length > 1 && Buffer.byteLength(lines.join("\n\n"), "utf8") > MAX_PASSAGE_BYTES) {
    lines.shift();
  }
  const passage = lines.join("\n\n");
  return Buffer.byteLength(passage, "utf8") > MAX_PASSAGE_BYTES ? undefined : passage;
}
