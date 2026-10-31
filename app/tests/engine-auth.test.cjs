const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const code = fs.readFileSync(path.join(__dirname, "../dist-electron/engine-bridge.js"), "utf8");

function fixture() {
  const requests = [];
  const launches = [];
  const exported = {};
  const child = () => {
    const process = new EventEmitter();
    process.stdout = new EventEmitter();
    process.stderr = new EventEmitter();
    process.kill = () => process.emit("exit", 0);
    return process;
  };
  const mockRequire = (name) => {
    if (name === "electron") return {
      app: { isPackaged: false },
      BrowserWindow: { getAllWindows: () => [] },
    };
    if (name === "child_process") return {
      spawn: (_python, _args, options) => {
        launches.push(options.env);
        return child();
      },
    };
    if (name === "fs") return { existsSync: () => true };
    if (name === "net") return {
      createServer: () => ({
        listen: (_port, callback) => queueMicrotask(callback),
        address: () => ({ port: 12345 }),
        close: (callback) => callback(),
        on() {},
      }),
    };
    return require(name);
  };
  vm.runInNewContext(code, {
    require: mockRequire,
    exports: exported,
    __dirname,
    process: { platform: "darwin", env: {} },
    console: { log() {}, error() {} },
    URL,
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { json: async () => ({ status: "ready" }) };
    },
  });
  return { bridge: exported, requests, launches };
}

test("health, JSON calls and streaming requests use the private launch token", async () => {
  const { bridge, requests, launches } = fixture();
  await bridge.startEngine();
  await bridge.engineFetch("/health");
  await bridge.engineRequest("/chat/stream", { body: { message: "Fictional" } });
  assert.match(launches[0].COMPASS_ENGINE_TOKEN, /^[a-f0-9]{64}$/);
  assert.equal(requests.length, 3);
  for (const request of requests) {
    assert.equal(request.options.headers.Authorization, `Bearer ${launches[0].COMPASS_ENGINE_TOKEN}`);
    assert.equal(new URL(request.url).origin, "http://127.0.0.1:12345");
  }
  assert.equal(requests[2].options.method, "POST");
  bridge.stopEngine();
});

test("restart rotates the token and stopped engine requests fail", async () => {
  const { bridge, launches } = fixture();
  await bridge.startEngine();
  bridge.stopEngine();
  await assert.rejects(bridge.engineFetch("/health"), /Engine not started/);
  await bridge.startEngine();
  assert.notEqual(launches[0].COMPASS_ENGINE_TOKEN, launches[1].COMPASS_ENGINE_TOKEN);
  bridge.stopEngine();
});

test("caller-controlled endpoints cannot send the token to another origin", async () => {
  const { bridge, requests } = fixture();
  await bridge.startEngine();
  for (const endpoint of ["https://untrusted.example/", "//untrusted.example/", "@untrusted.example/"]) {
    await assert.rejects(bridge.engineRequest(endpoint), /Engine endpoint must stay/);
  }
  assert.equal(requests.length, 1);
  bridge.stopEngine();
});

test("desktop child environment drops provider credentials and requires native grants",()=>{
 const {bridge}=fixture();
 const environment=bridge.engineEnvironment({PATH:'/fictional/bin',ANTHROPIC_API_KEY:'fictional-secret',OPENROUTER_API_KEY:'fictional-secret',HF_TOKEN:'fictional-hf',COMPASS_CHATGPT_ACCESS_TOKEN:'fictional-secret',COMPASS_AUTH_TOKEN:'fictional-secret'});
 assert.equal(environment.PATH,'/fictional/bin');assert.equal(environment.COMPASS_NATIVE_INFERENCE_ONLY,'1');
 for(const name of ['ANTHROPIC_API_KEY','OPENROUTER_API_KEY','HF_TOKEN','COMPASS_CHATGPT_ACCESS_TOKEN','COMPASS_AUTH_TOKEN'])assert.equal(environment[name],undefined);
});
