const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const mainCode = fs.readFileSync(path.join(__dirname, "../dist-electron/main.js"), "utf8");

function fixture(t, { platform = "darwin", available = true, backend = "gnome_libsecret" } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "compass-secret-check-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const handlers = new Map();
  const state = { available };
  const electron = {
    app: {
      getPath: () => directory,
      whenReady: () => ({ then() {} }),
      setAsDefaultProtocolClient() {},
      on() {},
    },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    safeStorage: {
      isEncryptionAvailable: () => state.available,
      getSelectedStorageBackend: () => {
        assert.equal(platform, "linux");
        return backend;
      },
      // This fixture checks credential policy and IPC behavior, not OS cryptography.
      encryptString: (value) => Buffer.from(`fixture-ciphertext:${value}`),
      decryptString: (value) => {
        const text = value.toString();
        if (!text.startsWith("fixture-ciphertext:")) throw new Error("Not encrypted");
        return text.slice("fixture-ciphertext:".length);
      },
    },
  };
  vm.runInNewContext(mainCode, {
    require: (name) => {
      if (name === "electron") return electron;
      if (name === "electron-updater") return { autoUpdater: {} };
      if (name.startsWith("./")) return {};
      return require(name);
    },
    exports: {},
    __dirname: path.join(directory, "dist-electron"),
    process: { platform, argv: [], env: {} },
    Buffer,
    console,
  });
  return {
    call: (name, ...args) => handlers.get(name)({}, ...args),
    file: path.join(directory, "secrets.enc"),
    state,
  };
}

test("unavailable OS encryption rejects both credential writes without creating a file", async (t) => {
  const vault = fixture(t, { available: false });
  await assert.rejects(vault.call("secrets-store", "key", "fictional-secret"), /Secure credential storage is unavailable/);
  await assert.rejects(vault.call("credential-store", "example", { access_token: "fictional-token" }), /Secure credential storage is unavailable/);
  assert.equal(fs.existsSync(vault.file), false);
});

test("encrypted credentials roundtrip and remain inaccessible when encryption becomes unavailable", async (t) => {
  const vault = fixture(t);
  await vault.call("secrets-store", "key", "fictional-secret");
  await vault.call("credential-store", "example", { access_token: "fictional-token" });
  const stored = fs.readFileSync(vault.file, "utf8");
  assert.equal(stored.includes("fictional-secret"), false);
  assert.equal(stored.includes("fictional-token"), false);
  assert.equal(await vault.call("secrets-load", "key"), "fictional-secret");
  assert.equal((await vault.call("credential-load", "example")).access_token, "fictional-token");
  vault.state.available = false;
  assert.equal(await vault.call("secrets-load", "key"), null);
  assert.equal(await vault.call("credential-load", "example"), null);
  assert.equal((await vault.call("credential-list")).length, 0);
  assert.equal(fs.readFileSync(vault.file, "utf8"), stored);
});

test("Linux basic_text is rejected even when Electron reports encryption available", async (t) => {
  const vault = fixture(t, { platform: "linux", backend: "basic_text" });
  await assert.rejects(vault.call("secrets-store", "key", "fictional-secret"), /Secure credential storage is unavailable/);
  await assert.rejects(vault.call("credential-store", "example", { access_token: "fictional-token" }), /Secure credential storage is unavailable/);
  assert.equal(fs.existsSync(vault.file), false);
});

test("Linux native secret backend supports the encrypted storage path", async (t) => {
  const vault = fixture(t, { platform: "linux" });
  await vault.call("secrets-store", "key", "fictional-secret");
  assert.equal(await vault.call("secrets-load", "key"), "fictional-secret");
});

test("legacy plaintext is never returned, and explicit deletion still works", async (t) => {
  const vault = fixture(t, { available: false });
  fs.writeFileSync(vault.file, JSON.stringify({ key: "old-plaintext", "credential:example": "old-plaintext" }));
  assert.equal(await vault.call("secrets-load", "key"), null);
  assert.equal(await vault.call("credential-load", "example"), null);
  vault.state.available = true;
  assert.equal(await vault.call("secrets-load", "key"), null);
  assert.equal(await vault.call("credential-load", "example"), null);
  await vault.call("secrets-delete", "key");
  await vault.call("credential-delete", "example");
  assert.deepEqual(JSON.parse(fs.readFileSync(vault.file, "utf8")), {});
});
