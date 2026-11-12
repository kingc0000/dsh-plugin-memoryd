import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import Path from "node:path";
import { TokenStore } from "../dist/token.js";

function mkTempDir() {
  return fs.mkdtempSync(Path.join(os.tmpdir(), "memoryd-token-"));
}

function configFor(dir, endpoint, extra = {}) {
  return {
    endpoint,
    token: "",
    apiKey: "",
    requestTimeoutMs: 3000,
    searchLimit: 8,
    autoMemoryEnabled: true,
    autoMemoryInterval: 5,
    rootAgentsOnly: false,
    language: "zh",
    autoRegister: true,
    deviceName: "test-agent",
    tokenFile: Path.join(dir, "token"),
    fingerprintFile: Path.join(dir, "fingerprint"),
    registrationPollMs: 1000,
    ...extra,
  };
}

function startServer(handlers) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url, "http://x");
      const h = handlers[url.pathname];
      if (!h) {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end("{}");
        return;
      }
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        const { status = 200, json } = h(req, body);
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(json ? JSON.stringify(json) : "");
      });
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

function waitFor(fn, timeoutMs = 8000, stepMs = 50) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      const v = fn();
      if (v) return resolve(v);
      if (Date.now() > deadline) return reject(new Error("waitFor timeout"));
      setTimeout(tick, stepMs);
    };
    tick();
  });
}

test("no token: auto-registers, polls until approved, persists token file", async () => {
  const dir = mkTempDir();
  let claims = 0;
  const server = await startServer({
    "/v1/auth/register": () => ({ status: 202, json: { request_id: 1, status: "pending" } }),
    "/v1/auth/register/claim": () => {
      claims += 1;
      if (claims < 2) return { status: 202, json: {} };
      return { status: 200, json: { request_id: 1, status: "issued", token: "mdt-test-token" } };
    },
  });
  const endpoint = `http://127.0.0.1:${server.address().port}`;
  const store = new TokenStore(() => configFor(dir, endpoint));
  store.start();
  assert.equal(await waitFor(() => store.resolve()), "mdt-test-token");
  assert.ok(claims >= 2);
  assert.equal(fs.readFileSync(Path.join(dir, "token"), "utf8").trim(), "mdt-test-token");
  const fp = fs.readFileSync(Path.join(dir, "fingerprint"), "utf8").trim();
  assert.ok(fp.length >= 16);
  store.dispose();
  server.close();
});

test("existing token file: loads without registering", async () => {
  const dir = mkTempDir();
  fs.writeFileSync(Path.join(dir, "token"), "file-token\n");
  let registered = 0;
  const server = await startServer({
    "/v1/auth/register": () => {
      registered += 1;
      return { status: 202, json: {} };
    },
  });
  const store = new TokenStore(() => configFor(dir, `http://127.0.0.1:${server.address().port}`));
  store.start();
  assert.equal(store.resolve(), "file-token");
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(registered, 0);
  store.dispose();
  server.close();
});

test("explicit token wins over auto-registration", async () => {
  const dir = mkTempDir();
  let registered = 0;
  const server = await startServer({
    "/v1/auth/register": () => {
      registered += 1;
      return { status: 202, json: {} };
    },
  });
  const store = new TokenStore(() => configFor(dir, `http://127.0.0.1:${server.address().port}`, { token: "explicit-token" }));
  store.start();
  assert.equal(store.resolve(), "explicit-token");
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(registered, 0);
  store.dispose();
  server.close();
});

test("rejected registration stops polling", async () => {
  const dir = mkTempDir();
  let claims = 0;
  const server = await startServer({
    "/v1/auth/register": () => ({ status: 202, json: { request_id: 2, status: "pending" } }),
    "/v1/auth/register/claim": () => {
      claims += 1;
      return { status: 403, json: { detail: "注册请求已被拒绝" } };
    },
  });
  const store = new TokenStore(() => configFor(dir, `http://127.0.0.1:${server.address().port}`, { registrationPollMs: 100 }));
  store.start();
  await waitFor(() => claims >= 1);
  const after = claims;
  await new Promise((r) => setTimeout(r, 500));
  assert.equal(claims, after, "must not keep polling after rejection");
  assert.equal(store.resolve(), "");
  store.dispose();
  server.close();
});
