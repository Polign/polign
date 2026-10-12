// Plugin configuration: what OpenClaw passes under plugins.entries.recall.config,
// with the environment as the fallback and sensible defaults after that.

export type RecallPluginConfig = {
  /** Where `recall serve` listens, without a trailing slash. */
  url: string;
  /** A Recall key, rcl_<id>_<secret>. Empty means the plugin cannot reach the server. */
  apiKey: string;
  /** Put relevant memories in front of each prompt. */
  autoRecall: boolean;
  /** Send the recent conversation to Recall after each turn. */
  autoCapture: boolean;
  /** How many memories auto-recall injects. */
  maxMemories: number;
  /** How many of the latest messages auto-capture sends. */
  captureMessages: number;
  /** Timeout for the agent's tool calls. Storing waits for the extraction model. */
  timeoutMs: number;
  /** Timeout for one auto-capture, which runs in the background after the turn. */
  captureTimeoutMs: number;
};

/**
 * OpenClaw stops waiting for a before_prompt_build hook after 15 seconds, so
 * auto-recall gives up well before that and the turn goes ahead without it.
 */
export const AUTO_RECALL_TIMEOUT_MS = 10_000;

export const DEFAULT_URL = "http://127.0.0.1:8080";

export function resolveConfig(
  raw: Record<string, unknown> | undefined,
  env: Record<string, string | undefined> = process.env,
): RecallPluginConfig {
  const cfg = raw ?? {};
  const str = (key: string): string | undefined => {
    const v = cfg[key];
    return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
  };
  const bool = (key: string, fallback: boolean): boolean => {
    const v = cfg[key];
    return typeof v === "boolean" ? v : fallback;
  };
  const num = (key: string, fallback: number, min: number): number => {
    const v = cfg[key];
    return typeof v === "number" && Number.isFinite(v) && v >= min ? Math.floor(v) : fallback;
  };
  const envStr = (key: string): string | undefined => {
    const v = env[key];
    return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
  };
  return {
    url: (str("url") ?? envStr("RECALL_URL") ?? DEFAULT_URL).replace(/\/+$/, ""),
    apiKey: str("apiKey") ?? envStr("RECALL_API_KEY") ?? "",
    autoRecall: bool("autoRecall", true),
    autoCapture: bool("autoCapture", true),
    maxMemories: num("maxMemories", 5, 1),
    captureMessages: num("captureMessages", 10, 1),
    timeoutMs: num("timeoutMs", 60_000, 100),
    captureTimeoutMs: num("captureTimeoutMs", 120_000, 100),
  };
}
