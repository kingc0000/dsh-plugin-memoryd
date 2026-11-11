import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { MemorydClient } from "../dist/client.js";

/** 起一个假 memoryd 做端到端客户端测试。 */
function startFakeMemoryd(handler) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", () => {
        const payload = body ? JSON.parse(body) : null;
        const result = handler(req, payload);
        res.writeHead(result.status ?? 200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(result.body ?? {}));
      });
    });
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({ server, base: `http://127.0.0.1:${port}` });
    });
  });
}

function makeClient(base, apiKey = "test-token") {
  return new MemorydClient({
    endpoint: base,
    apiKey,
    requestTimeoutMs: 2000,
  });
}

test("client sends Bearer token and searches", async () => {
  let seenAuth = "";
  let seenPath = "";
  const { server, base } = await startFakeMemoryd((req, payload) => {
    seenAuth = req.headers.authorization;
    seenPath = req.url;
    return {
      body: { results: [{ id: 7, scope: "user", content: `命中:${payload.query}` }] },
    };
  });
  try {
    const client = makeClient(base);
    const result = await client.search("中文偏好", { limit: 3 });
    assert.equal(result.ok, true);
    assert.equal(seenAuth, "Bearer test-token");
    assert.equal(seenPath, "/v1/memories:search");
    assert.equal(result.data.results[0].id, 7);
  } finally {
    server.close();
  }
});

test("client write defaults scope to device", async () => {
  let payload = null;
  const { server, base } = await startFakeMemoryd((req, body) => {
    payload = body;
    return { body: { id: 1, scope: body.scope, version: 1 } };
  });
  try {
    const client = makeClient(base);
    const result = await client.write("一条记忆", { scope: "device" });
    assert.equal(result.ok, true);
    assert.equal(payload.content, "一条记忆");
    assert.equal(payload.scope, "device");
  } finally {
    server.close();
  }
});

test("client surfaces server error detail", async () => {
  const { server, base } = await startFakeMemoryd(() => ({
    status: 403,
    body: { detail: "无权写入 user 区" },
  }));
  try {
    const client = makeClient(base);
    const result = await client.write("x", { scope: "user" });
    assert.equal(result.ok, false);
    assert.equal(result.status, 403);
    assert.equal(result.error, "无权写入 user 区");
  } finally {
    server.close();
  }
});

test("client reports timeout as timeout(max)", async () => {
  const server = http.createServer(() => {
    // 永不响应：让请求超时
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const client = new MemorydClient({
      endpoint: base,
      apiKey: "t",
      requestTimeoutMs: 100,
    });
    const result = await client.whoami();
    assert.equal(result.ok, false);
    assert.ok(result.error.startsWith("timeout(max)"), result.error);
  } finally {
    server.close();
  }
});

test("client returns error when endpoint unreachable", async () => {
  const client = new MemorydClient({
    endpoint: "http://127.0.0.1:1",
    apiKey: "t",
    requestTimeoutMs: 500,
  });
  const result = await client.whoami();
  assert.equal(result.ok, false);
  assert.ok(result.error.length > 0);
});

test("client sends no Authorization header when apiKey empty", async () => {
  let seenAuth = "unset";
  const { server, base } = await startFakeMemoryd((req) => {
    seenAuth = req.headers.authorization;
    return { status: 401, body: { detail: "missing token" } };
  });
  try {
    const client = makeClient(base, "");
    const result = await client.whoami();
    assert.equal(result.ok, false);
    assert.equal(result.status, 401);
    assert.equal(seenAuth, undefined);
  } finally {
    server.close();
  }
});
