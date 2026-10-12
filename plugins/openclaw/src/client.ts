// A small client for the text surface of `recall serve`: POST /v1/remember,
// /v1/recall and /v1/forget with JSON bodies, behind a Recall key. The shapes
// below are what the server answers; see docs/memory-api-v1.md in
// github.com/Polign/recall.

export type Earlier = {
  text: string;
  /** The day it was stated, YYYY-MM-DD. */
  since: string;
};

export type Memory = {
  /** What is believed now, as a sentence. */
  text: string;
  since: string;
  days_ago: number;
  /** What this statement replaced. */
  before?: Earlier[];
  /** What else is believed about the same thing in other words. */
  elsewhere?: Earlier[];
};

export type RecallResult = { memories: Memory[] };

export type Remembered = {
  text: string;
  replaces?: string[];
  already_known?: boolean;
};

export type RememberResult = {
  remembered?: Remembered[];
  /** True when the text was kept as written rather than worked out into statements. */
  kept_as_text?: boolean;
  note?: string;
};

export type ForgetResult = { forgotten: string[] };

export class RecallError extends Error {
  /** The server's error code, or unreachable, bad_response, unauthenticated from the client. */
  readonly code: string;
  /** The HTTP status, when the server answered. */
  readonly status: number | undefined;

  constructor(code: string, message: string, status?: number) {
    super(message);
    this.name = "RecallError";
    this.code = code;
    this.status = status;
  }
}

export type ClientOptions = {
  url: string;
  apiKey: string;
  timeoutMs?: number;
  fetch?: typeof fetch;
};

export class RecallClient {
  private readonly url: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: ClientOptions) {
    this.url = opts.url.replace(/\/+$/, "");
    this.apiKey = opts.apiKey;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.fetchImpl = opts.fetch ?? fetch;
  }

  recall(args: { question: string; as_of?: string }, signal?: AbortSignal, timeoutMs?: number): Promise<RecallResult> {
    return this.post<RecallResult>("recall", args, signal, timeoutMs);
  }

  remember(args: { text: string; observed_at?: string }, signal?: AbortSignal, timeoutMs?: number): Promise<RememberResult> {
    return this.post<RememberResult>("remember", args, signal, timeoutMs);
  }

  forget(args: { text: string }, signal?: AbortSignal, timeoutMs?: number): Promise<ForgetResult> {
    return this.post<ForgetResult>("forget", args, signal, timeoutMs);
  }

  /** True when the server answers GET /healthz. No credential is needed. */
  async healthy(signal?: AbortSignal): Promise<boolean> {
    try {
      const res = await this.fetchImpl(`${this.url}/healthz`, { signal: this.signal(signal) });
      return res.ok;
    } catch {
      return false;
    }
  }

  private signal(outer?: AbortSignal, timeoutMs?: number): AbortSignal {
    const timeout = AbortSignal.timeout(timeoutMs ?? this.timeoutMs);
    return outer ? AbortSignal.any([outer, timeout]) : timeout;
  }

  private async post<T>(verb: string, body: unknown, signal?: AbortSignal, timeoutMs?: number): Promise<T> {
    if (!this.apiKey) {
      throw new RecallError("unauthenticated", "no Recall key: set apiKey in the plugin config or RECALL_API_KEY");
    }
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.url}/v1/${verb}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body ?? {}),
        signal: this.signal(signal, timeoutMs),
      });
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      throw new RecallError("unreachable", `Recall at ${this.url} did not answer: ${reason}`);
    }
    const text = await res.text();
    let parsed: unknown = undefined;
    if (text.trim() !== "") {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = undefined;
      }
    }
    if (!res.ok) {
      const err = (parsed as { error?: { code?: string; message?: string } } | undefined)?.error;
      throw new RecallError(err?.code ?? "http_error", err?.message ?? `HTTP ${res.status} from ${verb}`, res.status);
    }
    if (parsed === undefined) {
      throw new RecallError("bad_response", `${verb} answered with something other than JSON`, res.status);
    }
    return parsed as T;
  }
}
