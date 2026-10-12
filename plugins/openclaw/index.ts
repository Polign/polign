// Recall for OpenClaw: a memory plugin backed by Recall by Polign.
//
// The plugin fills OpenClaw's memory slot. Three tools let the agent search,
// store and forget; two hooks put relevant memories in front of each prompt
// and send the recent conversation to Recall after each turn. Everything goes
// over HTTP to `recall serve`, so the memories live wherever that server
// keeps them, and the Recall key chooses the namespace.

import { Type, type Static } from "typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { RecallClient, RecallError, type Memory, type RememberResult } from "./src/client.ts";
import { AUTO_RECALL_TIMEOUT_MS, resolveConfig, type RecallPluginConfig } from "./src/config.ts";
import { buildTranscript, describeMemory, renderMemoryContext } from "./src/context.ts";

export const PLUGIN_ID = "recall";

type ToolText = { content: Array<{ type: "text"; text: string }>; details: unknown };

function textResult(text: string, details: unknown = {}): ToolText {
  return { content: [{ type: "text", text }], details };
}

function describeRemember(result: RememberResult): string {
  const lines: string[] = [];
  for (const r of result.remembered ?? []) {
    lines.push(r.already_known ? `Already known: ${r.text}` : `Remembered: ${r.text}`);
    for (const old of r.replaces ?? []) lines.push(`  replaces: ${old}`);
  }
  if (result.kept_as_text) {
    lines.push(result.note ?? "Kept as text. Recall finds it by its words.");
  }
  return lines.length > 0 ? lines.join("\n") : "Nothing new to remember.";
}

function describeError(err: unknown): string {
  if (err instanceof RecallError) return `${err.code}: ${err.message}`;
  return err instanceof Error ? err.message : String(err);
}

const INSTANT = "an RFC 3339 instant such as 2026-09-01T00:00:00Z";

const searchParams = Type.Object({
  question: Type.String({ description: "What you want to know, in words." }),
  as_of: Type.Optional(Type.String({ description: `Answer as of this time rather than now: ${INSTANT}.` })),
});

const storeParams = Type.Object({
  text: Type.String({ description: "What to remember." }),
  observed_at: Type.Optional(
    Type.String({ description: `When it was said, as ${INSTANT}. Omit for now; use it only for something said earlier, such as an imported conversation.` }),
  ),
});

const forgetParams = Type.Object({
  text: Type.String({ description: "What to forget, described in words." }),
});

export function createPlugin(deps: { client?: RecallClient; config?: RecallPluginConfig } = {}) {
  // Captures run one at a time, after the turn, so a slow extraction model
  // neither holds up the agent nor runs several extractions at once.
  let captures: Promise<void> = Promise.resolve();
  let queued = 0;
  const MAX_QUEUED_CAPTURES = 8;

  return {
    /** Resolves once every capture queued so far has finished. */
    idle(): Promise<void> {
      return captures;
    },
    id: PLUGIN_ID,
    name: "Memory (Recall)",
    description:
      "Long-term memory kept in Recall by Polign: a new statement replaces the old one, the old one stays in the history, and memory can be read as of any earlier time.",
    register(api: OpenClawPluginApi) {
      const cfg = deps.config ?? resolveConfig(api.pluginConfig);
      const client = deps.client ?? new RecallClient({ url: cfg.url, apiKey: cfg.apiKey, timeoutMs: cfg.timeoutMs });
      const log = api.logger;

      if (!cfg.apiKey) {
        log.warn("recall: no key configured; set apiKey in plugins.entries.recall.config or RECALL_API_KEY");
      }

      api.registerTool(
        {
          name: "memory_search",
          label: "Memory search",
          description:
            "Ask what is remembered about the user or their work, in words, before assuming anything. Answers are what is believed now. Each shows what it replaced and when, so a correction comes with the answer. Pass as_of to ask what was believed at an earlier time.",
          parameters: searchParams,
          async execute(_toolCallId, raw, signal) {
            const params = raw as Static<typeof searchParams>;
            const out = await client.recall({ question: params.question, as_of: params.as_of }, signal);
            const memories: Memory[] = out.memories ?? [];
            if (memories.length === 0) return textResult("Nothing remembered about that.", { memories: [] });
            return textResult(memories.map((m) => `- ${describeMemory(m)}`).join("\n"), { memories });
          },
        },
        { name: "memory_search" },
      );

      api.registerTool(
        {
          name: "memory_store",
          label: "Memory store",
          description:
            "Keep something worth remembering about the user or their work: a preference, a fact, a decision, or a correction. Pass the passage as text, in the user's words where you can. Recall works out what it says. A newer statement replaces an older one about the same thing; restating something already known changes nothing.",
          parameters: storeParams,
          async execute(_toolCallId, raw, signal) {
            const params = raw as Static<typeof storeParams>;
            const out = await client.remember({ text: params.text, observed_at: params.observed_at }, signal);
            return textResult(describeRemember(out), out);
          },
        },
        { name: "memory_store" },
      );

      api.registerTool(
        {
          name: "memory_forget",
          label: "Memory forget",
          description:
            'Stop believing what a description names, such as "my editor" or "that I live in Lisbon". Returns what was withdrawn. Nothing is deleted: it stays in the history, and remembering it again brings it back.',
          parameters: forgetParams,
          async execute(_toolCallId, raw, signal) {
            const params = raw as Static<typeof forgetParams>;
            const out = await client.forget({ text: params.text }, signal);
            const forgotten = out.forgotten ?? [];
            if (forgotten.length === 0) return textResult("Nothing matched that description.", out);
            return textResult(forgotten.map((f) => `Forgot: ${f}`).join("\n"), out);
          },
        },
        { name: "memory_forget" },
      );

      if (cfg.autoRecall) {
        api.on("before_prompt_build", async (event) => {
          const prompt = (event.currentUserMessage ?? event.prompt ?? "").trim();
          if (prompt.length < 5) return;
          try {
            const out = await client.recall({ question: prompt }, undefined, Math.min(cfg.timeoutMs, AUTO_RECALL_TIMEOUT_MS));
            const block = renderMemoryContext(out.memories ?? [], cfg.maxMemories);
            if (!block) return;
            log.info(`recall: ${Math.min(out.memories.length, cfg.maxMemories)} memories in front of the prompt`);
            return { prependContext: block };
          } catch (err) {
            log.warn(`recall: auto-recall skipped: ${describeError(err)}`);
            return;
          }
        });
      }

      if (cfg.autoCapture) {
        api.on("agent_end", async (event) => {
          if (!event.success || !Array.isArray(event.messages) || event.messages.length === 0) return;
          const passage = buildTranscript(event.messages, cfg.captureMessages);
          if (!passage) return;
          if (queued >= MAX_QUEUED_CAPTURES) {
            log.warn(`recall: auto-capture skipped: ${queued} captures already waiting for Recall`);
            return;
          }
          // OpenClaw stops waiting for agent_end hooks after 30 seconds, and an
          // extraction model can take longer, so the capture runs after the hook
          // returns. A process that exits right after one turn, such as
          // `openclaw agent --local`, may end before it finishes.
          queued++;
          captures = captures.then(async () => {
            try {
              const out = await client.remember({ text: passage }, undefined, cfg.captureTimeoutMs);
              const kept = (out.remembered ?? []).filter((r) => !r.already_known).length;
              if (kept > 0 || out.kept_as_text) {
                log.info(`recall: auto-capture kept ${kept} statement${kept === 1 ? "" : "s"}${out.kept_as_text ? " and the text" : ""}`);
              }
            } catch (err) {
              log.warn(`recall: auto-capture skipped: ${describeError(err)}`);
            } finally {
              queued--;
            }
          });
        });
      }

      api.registerCli(
        ({ program }) => {
          const recall = program.command("recall").description("Recall memory: search, remember, forget, status");
          recall
            .command("search")
            .description("Ask what is remembered")
            .argument("<question>", "What you want to know")
            .option("--as-of <instant>", `Answer as of ${INSTANT}`)
            .action(async (question: string, opts: { asOf?: string }) => {
              const out = await client.recall({ question, as_of: opts.asOf });
              console.log(JSON.stringify(out, null, 2));
            });
          recall
            .command("remember")
            .description("Keep something worth remembering")
            .argument("<text>", "What to remember")
            .action(async (text: string) => {
              const out = await client.remember({ text });
              console.log(JSON.stringify(out, null, 2));
            });
          recall
            .command("forget")
            .description("Stop believing what a description names")
            .argument("<text>", "What to forget, in words")
            .action(async (text: string) => {
              const out = await client.forget({ text });
              console.log(JSON.stringify(out, null, 2));
            });
          recall
            .command("status")
            .description("Show the configured server and whether it answers")
            .action(async () => {
              const healthy = await client.healthy();
              console.log(
                JSON.stringify(
                  {
                    url: cfg.url,
                    key: cfg.apiKey ? "set" : "missing",
                    server: healthy ? "answering" : "not answering",
                    autoRecall: cfg.autoRecall,
                    autoCapture: cfg.autoCapture,
                  },
                  null,
                  2,
                ),
              );
            });
        },
        { commands: ["recall"] },
      );

      log.info(`recall: memory at ${cfg.url} (auto-recall ${cfg.autoRecall ? "on" : "off"}, auto-capture ${cfg.autoCapture ? "on" : "off"})`);
    },
  };
}

const plugin = createPlugin();
export default plugin;
