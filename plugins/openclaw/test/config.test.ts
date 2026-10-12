import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_URL, resolveConfig } from "../src/config.ts";

test("defaults apply when nothing is configured", () => {
  const cfg = resolveConfig(undefined, {});
  assert.equal(cfg.url, DEFAULT_URL);
  assert.equal(cfg.apiKey, "");
  assert.equal(cfg.autoRecall, true);
  assert.equal(cfg.autoCapture, true);
  assert.equal(cfg.maxMemories, 5);
  assert.equal(cfg.captureMessages, 10);
  assert.equal(cfg.timeoutMs, 10_000);
});

test("plugin config wins over the environment, which wins over the defaults", () => {
  const env = { RECALL_URL: "http://env:1/", RECALL_API_KEY: "rcl_env" };
  assert.equal(resolveConfig({}, env).url, "http://env:1");
  assert.equal(resolveConfig({}, env).apiKey, "rcl_env");
  const cfg = resolveConfig({ url: "http://cfg:2//", apiKey: " rcl_cfg ", autoRecall: false, maxMemories: 2.7, timeoutMs: 1 }, env);
  assert.equal(cfg.url, "http://cfg:2");
  assert.equal(cfg.apiKey, "rcl_cfg");
  assert.equal(cfg.autoRecall, false);
  assert.equal(cfg.maxMemories, 2);
  assert.equal(cfg.timeoutMs, 10_000, "a timeout under the minimum falls back");
});
