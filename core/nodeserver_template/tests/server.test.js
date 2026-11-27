"use strict";
/** node:test suite — real HTTP against the real server (port 0). Covers:
 *  health, main feature (notes CRUD), error handling (400/404/invalid JSON). */
const test = require("node:test");
const assert = require("node:assert");
const { server, reset } = require("../server.js");

let base = "";

test.before(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = "http://127.0.0.1:" + server.address().port;
});
test.after(async () => new Promise((r) => server.close(r)));
test.beforeEach(() => reset());

async function req(method, path, body) {
  const res = await fetch(base + path, {
    method,
    headers: body !== undefined ? {"Content-Type": "application/json"} : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let json = null;
  try { json = await res.json(); } catch (_) {}
  return { status: res.status, json };
}

test("health endpoint returns 200 {ok:true}", async () => {
  const r = await req("GET", "/api/health");
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
});

test("main feature: create + list notes", async () => {
  const created = await req("POST", "/api/notes", {title: "یادداشت اول"});
  assert.equal(created.status, 201);
  assert.equal(created.json.ok, true);
  assert.equal(created.json.note.title, "یادداشت اول");
  const list = await req("GET", "/api/notes");
  assert.equal(list.status, 200);
  assert.equal(list.json.notes.length, 1);
});

test("error handling: missing title → 400", async () => {
  const r = await req("POST", "/api/notes", {});
  assert.equal(r.status, 400);
  assert.equal(r.json.ok, false);
});

test("error handling: invalid JSON → 400", async () => {
  const res = await fetch(base + "/api/notes", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: "{not-json",
  });
  assert.equal(res.status, 400);
});

test("error handling: delete unknown note → 404", async () => {
  const r = await req("DELETE", "/api/notes/999");
  assert.equal(r.status, 404);
  assert.equal(r.json.ok, false);
});

test("error handling: unknown api route → 404", async () => {
  const r = await req("GET", "/api/nope");
  assert.equal(r.status, 404);
});
