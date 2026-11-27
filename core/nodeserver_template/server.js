"use strict";
/**
 * node-server template server — real, standalone, zero external deps.
 *
 * Node.js stdlib only (http). Config: PORT env (default 8080), bind 127.0.0.1.
 *
 * Routes:
 *   GET    /              → HTML index (200)
 *   GET    /health        → {"ok": true}
 *   GET    /api/health    → {"ok": true}
 *   GET    /api/notes     → {"ok": true, "notes": [...]}
 *   POST   /api/notes     → {"ok": true, "note": {...}}  (400 on bad JSON/missing title)
 *   DELETE /api/notes/:id → {"ok": true, "deleted": id}  (404 when missing)
 *   other /api/*          → 404 JSON error
 */


const http = require("http");

const notes = [];
let nextId = 1;

function send(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => {
      data += c;
      if (data.length > 1e6) req.destroy();   // 1MB cap — error handling
    });
    req.on("end", () => resolve(data));
    req.on("error", () => resolve(""));
  });
}

const server = http.createServer(async (req, res) => {
  // normalize leading "//" (curl/preview trailing-slash combos) — otherwise
  // new URL treats "//x" as a protocol-relative host and breaks routing
  const norm = req.url.replace(/^\/{2,}/, "/");
  const url = new URL(norm, "http://127.0.0.1");
  const path = url.pathname;
  try {
    if (req.method === "GET" && path === "/") {
      const body = "<!doctype html><html lang=\"fa\" dir=\"rtl\"><head>"
        + "<meta charset=\"utf-8\"><title>node-server</title></head>"
        + "<body><h1>node-server</h1>"
        + "<p>سرویس Node.js واقعی — API یادداشت.</p></body></html>";
      res.writeHead(200, {"Content-Type": "text/html; charset=utf-8"});
      res.end(body);
      return;
    }
    if (req.method === "GET" && (path === "/health" || path === "/api/health")) {
      return send(res, 200, {ok: true});
    }
    if (path === "/api/notes" && req.method === "GET") {
      return send(res, 200, {ok: true, notes: notes.slice()});
    }
    if (path === "/api/notes" && req.method === "POST") {
      let parsed;
      try {
        parsed = JSON.parse((await readBody(req)) || "null");
      } catch (_) {
        return send(res, 400, {ok: false, error: "invalid JSON"});
      }
      if (!parsed || typeof parsed !== "object"
          || typeof parsed.title !== "string" || !parsed.title.trim()) {
        return send(res, 400, {ok: false, error: "title (string) required"});
      }
      const note = {id: nextId++, title: parsed.title.trim()};
      notes.push(note);
      return send(res, 201, {ok: true, note});
    }
    const mDel = /^\/api\/notes\/(\d+)$/.exec(path);
    if (req.method === "DELETE" && mDel) {
      const id = Number(mDel[1]);
      const i = notes.findIndex((n) => n.id === id);
      if (i === -1) {
        return send(res, 404, {ok: false, error: "note not found"});
      }
      notes.splice(i, 1);
      return send(res, 200, {ok: true, deleted: id});
    }
    if (path.startsWith("/api/")) {
      return send(res, 404, {ok: false, error: "unknown route"});
    }
    return send(res, 404, {ok: false, error: "not found"});
  } catch (err) {
    return send(res, 500, {ok: false, error: "internal error"});
  }
});

if (require.main === module) {
  const port = Number(process.env.PORT || 8080);
  server.listen(port, "127.0.0.1", () => {
    console.log("node-server listening on 127.0.0.1:" + port);
  });
}

module.exports = { server, reset: () => { notes.length = 0; nextId = 1; } };
