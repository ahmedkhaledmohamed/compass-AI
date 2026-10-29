const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const code = fs.readFileSync(path.join(__dirname, "../dist-electron/ipc.js"), "utf8");

function fixture(url = "file:///fictional/compass/index.html") {
  const handlers = new Map();
  const exported = {};
  vm.runInNewContext(code, {
    require: () => ({ ipcMain: { handle: (name, handler) => handlers.set(name, handler) } }),
    exports: exported,
    URL,
  });
  const events = new Map();
  const frame = { processId: 1, routingId: 1, origin: url.startsWith("file:") ? "file://" : new URL(url).origin, url };
  const contents = {
    id: 1,
    mainFrame: frame,
    isDestroyed: () => false,
    on: (name, handler) => events.set(name, handler),
    setWindowOpenHandler: (handler) => events.set("window-open", handler),
  };
  const window = { webContents: contents };
  exported.trustMainWindow(window, url);
  let calls = 0;
  exported.handleMainIPC("fixture", (_event, argument) => { calls++; return argument; });
  return {
    ipc: exported, events, frame, contents, window,
    invoke: (event = { sender: contents, senderFrame: { ...frame } }) => handlers.get("fixture")(event, "accepted"),
    calls: () => calls,
  };
}

test("trusted main-frame identity is accepted even through another frame wrapper", () => {
  const f = fixture();
  assert.equal(f.invoke(), "accepted");
  assert.equal(f.calls(), 1);
});

test("other windows, child frames and missing frames cannot invoke a privileged handler", () => {
  const f = fixture();
  for (const event of [
    { sender: { id: 2 }, senderFrame: f.frame },
    { sender: f.contents, senderFrame: { ...f.frame, routingId: 2 } },
    { sender: f.contents, senderFrame: { ...f.frame, processId: 2 } },
    { sender: f.contents, senderFrame: null },
  ]) assert.throws(() => f.invoke(event), /Unauthorized IPC sender/);
  assert.equal(f.calls(), 0);
});

test("navigation, opaque/inherited URLs and mismatched origins do not preserve privilege", () => {
  const f = fixture();
  for (const frame of [
    { ...f.frame, url: "https://untrusted.example/", origin: "https://untrusted.example" },
    { ...f.frame, url: "file:///fictional/other.html" },
    { ...f.frame, url: "about:blank" },
    { ...f.frame, origin: "https://untrusted.example" },
  ]) assert.throws(() => f.invoke({ sender: f.contents, senderFrame: frame }), /Unauthorized IPC sender/);
  assert.equal(f.calls(), 0);
});

test("development renderer accepts its exact origin and rejects another port", () => {
  const f = fixture("http://localhost:5173");
  assert.equal(f.invoke(), "accepted");
  assert.equal(f.ipc.isMainRendererURL("http://localhost:5173/#/settings"), true);
  assert.equal(f.ipc.isMainRendererURL("http://localhost:5174/"), false);
});

test("untrusted navigation and redirects are prevented and extra windows are denied", () => {
  const f = fixture();
  let prevented = 0;
  for (const name of ["will-navigate", "will-redirect"]) {
    f.events.get(name)({ preventDefault: () => prevented++ }, "https://untrusted.example/");
    f.events.get(name)({ preventDefault: () => prevented++ }, f.frame.url + "#/settings");
  }
  assert.equal(prevented, 2);
  assert.equal(f.events.get("window-open")().action, "deny");
});

test("destroyed trusted window cannot authorize later requests", () => {
  const f = fixture();
  f.events.get("destroyed")();
  assert.throws(() => f.invoke(), /Unauthorized IPC sender/);
  assert.equal(f.calls(), 0);
});

test("queued IPC rejects destroyed native wrappers before invoking a handler", () => {
  for (const wrapper of ["window", "mainFrame", "senderFrame"]) {
    const f = fixture();
    const event = { sender: f.contents, senderFrame: { ...f.frame } };
    const target = wrapper === "window" ? f.window : wrapper === "mainFrame" ? f.contents : event;
    const property = wrapper === "window" ? "webContents" : wrapper;
    Object.defineProperty(target, property, { get() { throw new TypeError("Object has been destroyed"); } });
    assert.throws(() => f.invoke(event), /Unauthorized IPC sender/);
    assert.equal(f.calls(), 0);
  }
});

test("all privileged main and OAuth registrations use the trusted wrapper", () => {
  for (const name of ["main.ts", "oauth.ts"]) {
    const source = fs.readFileSync(path.join(__dirname, "../electron", name), "utf8");
    assert.doesNotMatch(source, /\bipcMain\.handle\s*\(/);
    assert.match(source, /handleMainIPC\(/);
  }
});

test("generated prototype scripts cannot share the privileged renderer origin", () => {
  const source = fs.readFileSync(path.join(__dirname, "../src/components/prototype/PrototypePreview.tsx"), "utf8");
  assert.match(source, /sandbox="allow-scripts"/);
  assert.doesNotMatch(source, /allow-same-origin/);
});
