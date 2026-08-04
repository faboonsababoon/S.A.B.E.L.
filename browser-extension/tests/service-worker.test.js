"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
require("../protocol.js");
require("../security.js");
const { createBridgeController } = require("../service-worker.js");

function chromeMock(options = {}) {
  const calls = { create: [], update: [], windowUpdate: [], remove: [], sendMessage: [], executeScript: [], permission: [] };
  const storage = { profileId: "personal", profileName: "Personal", port: 8765, token: "bridge-secret", allowedSites: ["youtube.com", "google.com"], instanceId: "instance-1" };
  const tab = { id: 7, windowId: 2, title: "YouTube", url: options.url || "https://www.youtube.com/", active: true };
  const api = {
    storage: { local: { async get() { return { ...storage }; }, async set(values) { Object.assign(storage, values); } } },
    permissions: {
      async contains(value) { calls.permission.push(value); return options.permission !== false; }
    },
    windows: {
      async update(id, value) { calls.windowUpdate.push([id, value]); return { id, ...value }; }
    },
    tabs: {
      async query(query) { return [tab]; },
      async get(id) { return { ...tab, id }; },
      async create(value) { calls.create.push(value); return { ...tab, id: 8, url: value.url, active: value.active }; },
      async update(id, value) { calls.update.push([id, value]); return { ...tab, id, ...value }; },
      async remove(id) { calls.remove.push(id); },
      async goBack(id) { calls.update.push([id, { goBack: true }]); },
      async sendMessage(id, message) {
        calls.sendMessage.push([id, message]);
        if (options.sendFailure) throw new Error("No content script");
        return { success: true, result: { snapshot_id: "snapshot-1", tab_id: id } };
      }
    },
    scripting: { async executeScript(value) { calls.executeScript.push(value); } }
  };
  return { api, calls, storage, tab };
}

class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }
  send(value) { this.sent.push(value); }
  close() { this.readyState = 3; if (this.onclose) this.onclose(); }
  serverClose(code, reason = "") { this.readyState = 3; if (this.onclose) this.onclose({ code, reason }); }
  open() { this.readyState = 1; this.onopen(); }
  message(value) { this.onmessage({ data: JSON.stringify(value) }); }
}

function controllerHarness(options = {}) {
  FakeWebSocket.instances = [];
  const chrome = chromeMock(options);
  const scheduled = [];
  const intervals = [];
  const controller = createBridgeController({
    chromeApi: chrome.api,
    WebSocketClass: FakeWebSocket,
    randomId: (() => { let value = 0; return () => String(++value); })(),
    setTimeoutFn(callback, delay) { scheduled.push({ callback, delay }); return scheduled.length; },
    clearTimeoutFn() {},
    setIntervalFn(callback, delay) { intervals.push({ callback, delay }); return intervals.length; },
    clearIntervalFn() {}
  });
  return { controller, chrome, scheduled, intervals };
}

test("service worker registers, reconnects with backoff, and sends heartbeats", async () => {
  const { controller, scheduled, intervals } = controllerHarness();
  await controller.connect();
  const socket = FakeWebSocket.instances[0];
  assert.equal(socket.url, "ws://127.0.0.1:8765");
  socket.open();
  const registration = JSON.parse(socket.sent[0]);
  assert.equal(registration.type, "register");
  assert.equal(registration.profile_id, "personal");
  socket.message({ protocol_version: 1, type: "registered", request_id: registration.request_id, profile_id: "personal", profile_name: "Personal" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(controller.status().connected, true);
  intervals[0].callback();
  assert.equal(JSON.parse(socket.sent.at(-1)).type, "heartbeat");
  socket.close();
  assert.equal(scheduled[0].delay, 1000);
});

test("duplicate profile replacement stops reconnect storms and explains the fix", async () => {
  const { controller, scheduled } = controllerHarness();
  await controller.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();
  socket.serverClose(4001, "New profile connection");
  assert.equal(controller.status().stopped, true);
  assert.match(controller.status().lastError, /different profile IDs/);
  assert.equal(scheduled.length, 0);
});

test("commands route to the correct tab and return structured responses", async () => {
  const { controller, chrome } = controllerHarness();
  await controller.loadSettings();
  const snapshot = await controller.executeCommand("browser_get_snapshot", { tab_id: 7 });
  assert.equal(snapshot.snapshot_id, "snapshot-1");
  assert.equal(chrome.calls.sendMessage[0][0], 7);
  const opened = await controller.executeCommand("browser_open_tab", { url: "https://www.youtube.com/", active: true });
  assert.equal(opened.tab_id, 8);
  assert.equal(chrome.calls.create[0].url, "https://www.youtube.com/");
  assert.deepEqual(chrome.calls.windowUpdate[0], [2, { focused: true }]);
});

test("restricted pages and denied site permissions do not inject content scripts", async () => {
  const restricted = controllerHarness({ url: "chrome://settings/" });
  await restricted.controller.loadSettings();
  await assert.rejects(() => restricted.controller.executeCommand("browser_get_snapshot", { tab_id: 7 }), (error) => error.code === "SITE_NOT_ALLOWED");
  assert.equal(restricted.chrome.calls.executeScript.length, 0);

  const denied = controllerHarness({ permission: false });
  await denied.controller.loadSettings();
  await assert.rejects(() => denied.controller.executeCommand("browser_get_snapshot", { tab_id: 7 }), (error) => error.code === "SITE_PERMISSION_REQUIRED");
  assert.equal(denied.chrome.calls.executeScript.length, 0);
});

test("stop browser control rejects later commands without affecting tabs", async () => {
  const { controller, chrome } = controllerHarness();
  await controller.loadSettings();
  const sent = [];
  controller.state.socket = { readyState: 1, send(value) { sent.push(JSON.parse(value)); } };
  await controller.stopBrowserControl();
  assert.equal(controller.status().stopped, true);
  await controller.handleBridgeMessage(JSON.stringify({
    protocol_version: 1,
    type: "command",
    request_id: "request-after-stop",
    profile_id: "personal",
    action: "browser_get_active_tab",
    arguments: {}
  }));
  assert.equal(sent.at(-1).success, false);
  assert.equal(sent.at(-1).error.code, "CONTROL_STOPPED");
  assert.equal(chrome.calls.remove.length, 0);
  assert.equal(chrome.calls.update.length, 0);
});

test("token is sent only in registration and is never logged", async () => {
  const originalLog = console.log;
  const logs = [];
  console.log = (...values) => logs.push(values.join(" "));
  try {
    const { controller } = controllerHarness();
    await controller.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    assert.equal(JSON.parse(socket.sent[0]).token, "bridge-secret");
    assert.ok(!logs.join("\n").includes("bridge-secret"));
  } finally {
    console.log = originalLog;
  }
});
