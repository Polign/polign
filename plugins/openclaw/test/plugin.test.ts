import { test } from "node:test";
import assert from "node:assert/strict";
import { createPlugin } from "../index.ts";
import { RecallClient, type RecallResult, type RememberResult, type ForgetResult } from "../src/client.ts";
import { resolveConfig } from "../src/config.ts";

type Tool = {
  name: string;
  execute: (id: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<{ content: Array<{ type: string; text: string }>; details?: unknown }>;
};
type Hook = (event: Record<string, unknown>, ctx: Record<string, unknown>) => Promise<unknown>;

/** A stand-in for the OpenClaw plugin API that records what the plugin registers. */
function fakeApi(config: Record<string, unknown>) {
  const tools = new Map<string, Tool>();
  const hooks = new Map<string, Hook>();
  const logs: string[] = [];
  let cliCommands: string[] = [];
  const api = {
    pluginConfig: config,
    logger: {
      info: (m: string) => logs.push(`info ${m}`),
      warn: (m: string) => logs.push(`warn ${m}`),
      error: (m: string) => logs.push(`error ${m}`),
      debug: () => {},
    },
    registerTool: (tool: Tool) => tools.set(tool.name, tool),
    on: (name: string, handler: Hook) => hooks.set(name, handler),
    registerCli: (_registrar: unknown, opts: { commands: string[] }) => {
      cliCommands = opts.commands;
    },
  };
  return { api, tools, hooks, logs, cli: () => cliCommands };
}

/** A client whose answers are scripted and whose calls are recorded. */
function scriptedClient(script: { recall?: RecallResult; remember?: RememberResult; forget?: ForgetResult; fail?: Error }) {
  const calls: Array<{ verb: string; args: unknown }> = [];
  const timeouts: Array<{ verb: string; timeoutMs: number | undefined }> = [];
  const client = new RecallClient({ url: "http://unused", apiKey: "k" });
  const answer = <T>(verb: string, args: unknown, timeoutMs: number | undefined, value: T | undefined): Promise<T> => {
    calls.push({ verb, args });
    timeouts.push({ verb, timeoutMs });
    if (script.fail) return Promise.reject(script.fail);
    return Promise.resolve(value as T);
  };
  client.recall = (args, _signal, timeoutMs) => answer("recall", args, timeoutMs, script.recall);
  client.remember = (args, _signal, timeoutMs) => answer("remember", args, timeoutMs, script.remember);
  client.forget = (args, _signal, timeoutMs) => answer("forget", args, timeoutMs, script.forget);
  return { client, calls, timeouts };
}

const config = resolveConfig({ apiKey: "k", maxMemories: 2, captureMessages: 4 }, {});

test("registers the three memory tools, both hooks and the CLI", () => {
  const { api, tools, hooks, cli } = fakeApi({ apiKey: "k" });
  createPlugin({ client: scriptedClient({}).client, config }).register(api as never);
  assert.deepEqual([...tools.keys()].sort(), ["memory_forget", "memory_search", "memory_store"]);
  assert.deepEqual([...hooks.keys()].sort(), ["agent_end", "before_prompt_build"]);
  assert.deepEqual(cli(), ["recall"]);
});

test("hooks are left out when auto-recall and auto-capture are off", () => {
  const { api, hooks } = fakeApi({});
  const cfg = resolveConfig({ apiKey: "k", autoRecall: false, autoCapture: false }, {});
  createPlugin({ client: scriptedClient({}).client, config: cfg }).register(api as never);
  assert.equal(hooks.size, 0);
});

test("a missing key is logged at registration", () => {
  const { api, logs } = fakeApi({});
  createPlugin({ client: scriptedClient({}).client, config: resolveConfig({}, {}) }).register(api as never);
  assert.ok(logs.some((l) => l.startsWith("warn recall: no key configured")));
});

test("memory_search renders memories with what they replaced, and as_of goes through", async () => {
  const { api, tools } = fakeApi({});
  const { client, calls } = scriptedClient({
    recall: { memories: [{ text: "Anup lives in Bellevue.", since: "2026-09-20", days_ago: 22, before: [{ text: "Anup lives in Seattle.", since: "2026-08-01" }] }] },
  });
  createPlugin({ client, config }).register(api as never);
  const out = await tools.get("memory_search")!.execute("1", { question: "where?", as_of: "2026-10-01T00:00:00Z" });
  assert.equal(out.content[0]?.text, '- Anup lives in Bellevue. (stated 2026-09-20; replaced "Anup lives in Seattle." from 2026-08-01)');
  assert.deepEqual(calls, [{ verb: "recall", args: { question: "where?", as_of: "2026-10-01T00:00:00Z" } }]);
});

test("memory_search says so when nothing is remembered", async () => {
  const { api, tools } = fakeApi({});
  createPlugin({ client: scriptedClient({ recall: { memories: [] } }).client, config }).register(api as never);
  const out = await tools.get("memory_search")!.execute("1", { question: "anything?" });
  assert.equal(out.content[0]?.text, "Nothing remembered about that.");
});

test("memory_store reports what was remembered and what it replaced", async () => {
  const { api, tools } = fakeApi({});
  const { client } = scriptedClient({
    remember: { remembered: [{ text: "Anup prefers vim.", replaces: ["Anup prefers emacs."] }, { text: "Anup uses Go.", already_known: true }] },
  });
  createPlugin({ client, config }).register(api as never);
  const out = await tools.get("memory_store")!.execute("1", { text: "I switched to vim" });
  assert.equal(out.content[0]?.text, "Remembered: Anup prefers vim.\n  replaces: Anup prefers emacs.\nAlready known: Anup uses Go.");
});

test("memory_store passes on the server's note when text is kept as written", async () => {
  const { api, tools } = fakeApi({});
  const { client } = scriptedClient({ remember: { kept_as_text: true, note: "This server has no extraction model, so the text was kept as written." } });
  createPlugin({ client, config }).register(api as never);
  const out = await tools.get("memory_store")!.execute("1", { text: "x" });
  assert.match(out.content[0]?.text ?? "", /no extraction model/);
});

test("memory_forget lists what was withdrawn", async () => {
  const { api, tools } = fakeApi({});
  const { client } = scriptedClient({ forget: { forgotten: ["Anup prefers vim."] } });
  createPlugin({ client, config }).register(api as never);
  const out = await tools.get("memory_forget")!.execute("1", { text: "my editor" });
  assert.equal(out.content[0]?.text, "Forgot: Anup prefers vim.");
  const none = createPlugin({ client: scriptedClient({ forget: { forgotten: [] } }).client, config });
  const other = fakeApi({});
  none.register(other.api as never);
  assert.equal((await other.tools.get("memory_forget")!.execute("1", { text: "x" })).content[0]?.text, "Nothing matched that description.");
});

test("tool errors surface to the host instead of being hidden in the text", async () => {
  const { api, tools } = fakeApi({});
  createPlugin({ client: scriptedClient({ fail: new Error("boom") }).client, config }).register(api as never);
  await assert.rejects(tools.get("memory_search")!.execute("1", { question: "x" }), /boom/);
});

test("before_prompt_build puts at most maxMemories in front of the prompt", async () => {
  const { api, hooks } = fakeApi({});
  const { client, calls } = scriptedClient({
    recall: {
      memories: [
        { text: "A.", since: "2026-10-01", days_ago: 1 },
        { text: "B.", since: "2026-10-02", days_ago: 0 },
        { text: "C.", since: "2026-10-03", days_ago: 0 },
      ],
    },
  });
  createPlugin({ client, config }).register(api as never);
  const result = (await hooks.get("before_prompt_build")!({ prompt: "What is my editor?", messages: [] }, {})) as { prependContext?: string } | undefined;
  const block = result?.prependContext ?? "";
  assert.ok(block.startsWith("<relevant-memories>"));
  assert.match(block, /- A\./);
  assert.match(block, /- B\./);
  assert.doesNotMatch(block, /- C\./);
  assert.deepEqual(calls, [{ verb: "recall", args: { question: "What is my editor?" } }]);
});

test("auto-recall gives up before OpenClaw's 15 second hook limit; tools use the longer timeout", async () => {
  const { api, hooks, tools } = fakeApi({});
  const { client, timeouts } = scriptedClient({ recall: { memories: [] } });
  createPlugin({ client, config }).register(api as never);
  await hooks.get("before_prompt_build")!({ prompt: "What is my editor?", messages: [] }, {});
  await tools.get("memory_search")!.execute("1", { question: "editor?" });
  assert.deepEqual(timeouts, [
    { verb: "recall", timeoutMs: 10_000 },
    { verb: "recall", timeoutMs: undefined },
  ]);
});

test("agent_end returns before the capture finishes, and captures run one at a time", async () => {
  const { api, hooks } = fakeApi({});
  const client = new RecallClient({ url: "http://unused", apiKey: "k" });
  const order: string[] = [];
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  let n = 0;
  client.remember = async () => {
    const id = ++n;
    order.push(`start ${id}`);
    if (id === 1) await gate;
    order.push(`end ${id}`);
    return {};
  };
  const plugin = createPlugin({ client, config });
  plugin.register(api as never);
  const hook = hooks.get("agent_end")!;
  await hook({ success: true, messages: [{ role: "user", content: "first" }] }, {});
  await hook({ success: true, messages: [{ role: "user", content: "second" }] }, {});
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(order, ["start 1"], "the hooks returned while the first capture was still waiting");
  release();
  await plugin.idle();
  assert.deepEqual(order, ["start 1", "end 1", "start 2", "end 2"]);
});

test("before_prompt_build prefers the current user message, skips short prompts and empty answers", async () => {
  const { api, hooks } = fakeApi({});
  const { client, calls } = scriptedClient({ recall: { memories: [] } });
  createPlugin({ client, config }).register(api as never);
  const hook = hooks.get("before_prompt_build")!;
  assert.equal(await hook({ prompt: "hi", messages: [] }, {}), undefined);
  assert.equal(calls.length, 0);
  assert.equal(await hook({ prompt: "assembled prompt text", currentUserMessage: "Where do I live?", messages: [] }, {}), undefined);
  assert.deepEqual(calls, [{ verb: "recall", args: { question: "Where do I live?" } }]);
});

test("before_prompt_build logs and adds nothing when Recall is down", async () => {
  const { api, hooks, logs } = fakeApi({});
  createPlugin({ client: scriptedClient({ fail: new Error("connection refused") }).client, config }).register(api as never);
  assert.equal(await hooks.get("before_prompt_build")!({ prompt: "What is my editor?", messages: [] }, {}), undefined);
  assert.ok(logs.some((l) => l.includes("auto-recall skipped")));
});

test("agent_end sends the latest turns as one passage and strips injected memories", async () => {
  const { api, hooks } = fakeApi({});
  const { client, calls, timeouts } = scriptedClient({ remember: { remembered: [{ text: "Anup prefers vim." }] } });
  const plugin = createPlugin({ client, config });
  plugin.register(api as never);
  await hooks.get("agent_end")!(
    {
      success: true,
      messages: [
        { role: "user", content: "very old" },
        { role: "assistant", content: "old reply" },
        { role: "user", content: "<relevant-memories>\n- A. (stated 2026-10-01)\n</relevant-memories>\n\nI prefer vim now." },
        { role: "assistant", content: [{ type: "text", text: "Noted, vim it is." }] },
      ],
    },
    {},
  );
  await plugin.idle();
  assert.deepEqual(calls, [{ verb: "remember", args: { text: "User: very old\n\nAssistant: old reply\n\nUser: I prefer vim now.\n\nAssistant: Noted, vim it is." } }]);
  assert.deepEqual(timeouts, [{ verb: "remember", timeoutMs: 120_000 }]);
});

test("agent_end does nothing for failed runs, empty runs, or runs without user text", async () => {
  const { api, hooks } = fakeApi({});
  const { client, calls } = scriptedClient({ remember: {} });
  const plugin = createPlugin({ client, config });
  plugin.register(api as never);
  const hook = hooks.get("agent_end")!;
  await hook({ success: false, messages: [{ role: "user", content: "x" }] }, {});
  await hook({ success: true, messages: [] }, {});
  await hook({ success: true, messages: [{ role: "assistant", content: "only me" }] }, {});
  await plugin.idle();
  assert.equal(calls.length, 0);
});

test("agent_end logs and swallows a Recall failure", async () => {
  const { api, hooks, logs } = fakeApi({});
  const plugin = createPlugin({ client: scriptedClient({ fail: new Error("boom") }).client, config });
  plugin.register(api as never);
  await hooks.get("agent_end")!({ success: true, messages: [{ role: "user", content: "I prefer vim." }] }, {});
  await plugin.idle();
  assert.ok(logs.some((l) => l.includes("auto-capture skipped: boom")));
});
