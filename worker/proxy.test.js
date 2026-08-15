import assert from "node:assert/strict";
import test from "node:test";

import { proxyToContainer } from "./proxy.js";

test("proxyToContainer returns the container's response on success", async () => {
  const containerResponse = new Response("ok", { status: 200 });
  const request = new Request("https://hal.example/publications/");

  const result = await proxyToContainer(request, async (req) => {
    assert.equal(req, request);
    return containerResponse;
  });

  assert.equal(result, containerResponse);
});

test("proxyToContainer falls back to a 503 when the container fetch rejects", async (t) => {
  const consoleError = t.mock.method(console, "error", () => {});
  const request = new Request("https://hal.example/publications/");

  const result = await proxyToContainer(request, async () => {
    throw new Error("The container is not running, consider calling start()");
  });

  assert.equal(result.status, 503);
  assert.equal(result.headers.get("retry-after"), "10");
  assert.deepEqual(await result.json(), { error: "Application temporarily unavailable" });

  assert.equal(consoleError.mock.calls.length, 1);
  const logged = JSON.parse(consoleError.mock.calls[0].arguments[0]);
  assert.deepEqual(logged, {
    event: "django_container_proxy_error",
    message: "The container is not running, consider calling start()",
  });
});

test("proxyToContainer stringifies non-Error rejections", async (t) => {
  t.mock.method(console, "error", () => {});
  const request = new Request("https://hal.example/publications/");

  const result = await proxyToContainer(request, async () => {
    throw "container unavailable";
  });

  assert.equal(result.status, 503);
  assert.deepEqual(await result.json(), { error: "Application temporarily unavailable" });
});
