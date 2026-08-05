if (typeof importScripts === "function") {
  importScripts("protocol.js", "security.js");
}

(function (root) {
  "use strict";

  const Protocol = root.SabelProtocol || (typeof require !== "undefined" ? require("./protocol.js") : null);
  const Security = root.SabelSecurity || (typeof require !== "undefined" ? require("./security.js") : null);

  function createBridgeController(dependencies = {}) {
    const chromeApi = dependencies.chromeApi || root.chrome;
    const WebSocketClass = dependencies.WebSocketClass || root.WebSocket;
    const timeout = dependencies.setTimeoutFn || root.setTimeout.bind(root);
    const clearTimeoutFn = dependencies.clearTimeoutFn || root.clearTimeout.bind(root);
    const interval = dependencies.setIntervalFn || root.setInterval.bind(root);
    const clearIntervalFn = dependencies.clearIntervalFn || root.clearInterval.bind(root);
    const randomId = dependencies.randomId || (() => root.crypto?.randomUUID?.() || Math.random().toString(16).slice(2));
    const state = {
      socket: null,
      settings: null,
      instanceId: null,
      connected: false,
      connecting: false,
      stopped: false,
      reconnectAttempt: 0,
      reconnectTimer: null,
      heartbeatTimer: null,
      currentTask: null,
      controlGeneration: 0,
      lastError: null
    };

    async function storageGet(keys) {
      return chromeApi.storage.local.get(keys);
    }

    async function storageSet(values) {
      return chromeApi.storage.local.set(values);
    }

    async function loadSettings() {
      const stored = await storageGet(["profileId", "profileName", "port", "token", "allowedSites", "instanceId"]);
      const settings = Protocol.validateSettings({
        profileId: stored.profileId || "personal",
        profileName: stored.profileName || "Personal",
        port: Number(stored.port || 8765),
        token: stored.token || "",
        allowedSites: stored.allowedSites || ["youtube.com", "google.com"]
      });
      if (!stored.instanceId) {
        stored.instanceId = `instance-${randomId()}`;
        await storageSet({ instanceId: stored.instanceId });
      }
      state.settings = settings;
      state.instanceId = stored.instanceId;
      return settings;
    }

    function scheduleReconnect() {
      if (state.stopped || state.reconnectTimer) return;
      const delay = Math.min(30000, 1000 * (2 ** state.reconnectAttempt));
      state.reconnectAttempt = Math.min(state.reconnectAttempt + 1, 5);
      state.reconnectTimer = timeout(() => {
        state.reconnectTimer = null;
        connect().catch(() => {});
      }, delay);
    }

    function startHeartbeat() {
      if (state.heartbeatTimer) clearIntervalFn(state.heartbeatTimer);
      state.heartbeatTimer = interval(() => {
        if (!state.connected || !state.socket || state.socket.readyState !== 1) return;
        state.socket.send(JSON.stringify({
          protocol_version: 1,
          type: "heartbeat",
          request_id: `heartbeat-${randomId()}`,
          profile_id: state.settings.profileId
        }));
      }, 20000);
    }

    async function connect() {
      if (state.connecting || (state.socket && state.socket.readyState <= 1)) return;
      state.stopped = false;
      state.connecting = true;
      let settings;
      try {
        settings = await loadSettings();
      } catch (error) {
        state.connecting = false;
        state.lastError = error.message;
        return;
      }
      const socket = new WebSocketClass(`ws://127.0.0.1:${settings.port}`);
      state.socket = socket;
      socket.onopen = () => {
        state.connecting = false;
        const requestId = `register-${randomId()}`;
        socket.send(JSON.stringify(Protocol.registrationMessage(settings, state.instanceId, requestId)));
      };
      socket.onmessage = (event) => handleBridgeMessage(event.data).catch(() => {
        try { socket.close(4400, "Invalid bridge message"); } catch (_) {}
      });
      socket.onerror = () => {
        state.lastError = "The local SABEL bridge could not be reached.";
      };
      socket.onclose = (event = {}) => {
        if (state.socket !== socket) return;
        state.connected = false;
        state.connecting = false;
        if (state.heartbeatTimer) clearIntervalFn(state.heartbeatTimer);
        state.heartbeatTimer = null;
        if (event.code === 4001) {
          state.stopped = true;
          state.lastError = `Another extension is already connected as ${state.settings?.profileName || state.settings?.profileId || "this profile"}. Give Personal and NYU different profile IDs, then reconnect.`;
          return;
        }
        if (event.code === 4002) {
          state.stopped = true;
          state.lastError = "A newer connection from this extension instance replaced this stale connection. Use Reconnect if this message remains visible.";
          return;
        }
        if (event.code === 4401) {
          state.stopped = true;
          state.lastError = "SABEL rejected the authentication token. Replace it in extension settings, then reconnect.";
          return;
        }
        if (event.code === 4400) {
          state.stopped = true;
          state.lastError = "SABEL rejected the extension protocol or settings. Review settings, reload the extension, then reconnect.";
          return;
        }
        if (!state.stopped) scheduleReconnect();
      };
    }

    function strictServerEnvelope(message, type, fields) {
      const keys = new Set(["protocol_version", "type", "request_id", ...fields]);
      if (!message || typeof message !== "object" || Array.isArray(message)) throw new Protocol.ProtocolError("Invalid server message.");
      if (message.protocol_version !== 1 || message.type !== type || typeof message.request_id !== "string") throw new Protocol.ProtocolError("Invalid server message.");
      if (Object.keys(message).some((key) => !keys.has(key)) || fields.some((field) => !(field in message))) throw new Protocol.ProtocolError("Invalid server fields.", "UNKNOWN_FIELD");
    }

    async function handleBridgeMessage(raw) {
      if (typeof raw !== "string" || raw.length > 262144) throw new Protocol.ProtocolError("Bridge message is too large.");
      let message;
      try { message = JSON.parse(raw); } catch (_) { throw new Protocol.ProtocolError("Malformed bridge JSON."); }
      if (message.type === "registered") {
        strictServerEnvelope(message, "registered", ["profile_id", "profile_name"]);
        if (message.profile_id !== state.settings.profileId) throw new Protocol.ProtocolError("Profile mismatch.");
        state.connected = true;
        state.reconnectAttempt = 0;
        state.lastError = null;
        startHeartbeat();
        return;
      }
      if (message.type === "heartbeat_ack") {
        strictServerEnvelope(message, "heartbeat_ack", ["profile_id"]);
        return;
      }
      const command = Protocol.validateCommand(message);
      if (command.profile_id !== state.settings.profileId) {
        const mismatch = Protocol.responseMessage(
          command,
          false,
          {},
          { code: "PROFILE_MISMATCH", message: "Command profile does not match this extension." }
        );
        if (state.socket?.readyState === 1) state.socket.send(JSON.stringify(mismatch));
        return;
      }
      let response;
      if (state.stopped && command.action !== "browser_stop_task") {
        response = Protocol.responseMessage(command, false, {}, { code: "CONTROL_STOPPED", message: "Browser control is stopped." });
      } else {
        const generation = state.controlGeneration;
        state.currentTask = "Browser action in progress";
        try {
          const result = await executeCommand(command.action, command.arguments);
          if (generation !== state.controlGeneration || state.stopped) {
            response = Protocol.responseMessage(command, false, {}, { code: "CANCELLED", message: "Browser control was stopped." });
          } else {
            response = Protocol.responseMessage(command, true, result, null);
          }
        } catch (error) {
          response = Protocol.responseMessage(command, false, {}, { code: error.code || "BROWSER_ACTION_FAILED", message: error.message || "Browser action failed." });
        } finally {
          if (generation === state.controlGeneration) state.currentTask = null;
        }
      }
      if (state.socket?.readyState === 1) state.socket.send(JSON.stringify(response));
    }

    function tabMetadata(tab) {
      return { tab_id: tab.id, window_id: tab.windowId, title: String(tab.title || "").slice(0, 300), url: String(tab.url || "").slice(0, 4096), active: Boolean(tab.active) };
    }

    function navigationMetadata(tab) {
      const metadata = tabMetadata(tab);
      const acceptedDestination = String(tab?.pendingUrl || tab?.url || "").slice(0, 4096);
      return { ...metadata, url: acceptedDestination };
    }

    async function focusTabWindow(tab) {
      if (Number.isInteger(tab?.windowId) && chromeApi.windows?.update) {
        try { await chromeApi.windows.update(tab.windowId, { focused: true }); } catch (_) {}
      }
      return tab;
    }

    async function requireAllowedTab(tabId) {
      const tab = await chromeApi.tabs.get(tabId);
      if (!tab?.url || !Security.domainAllowed(tab.url, state.settings.allowedSites)) {
        throw Object.assign(new Error("This page is outside the extension's allowed site list."), { code: "SITE_NOT_ALLOWED" });
      }
      const parsed = new URL(tab.url);
      const pattern = `${parsed.protocol}//${parsed.hostname}/*`;
      const permitted = await chromeApi.permissions.contains({ origins: [pattern] });
      if (!permitted) throw Object.assign(new Error("Chrome site permission has not been granted for this page."), { code: "SITE_PERMISSION_REQUIRED" });
      return tab;
    }

    async function sendPageAction(tabId, action, argumentsObject) {
      await requireAllowedTab(tabId);
      let response;
      try {
        response = await chromeApi.tabs.sendMessage(tabId, { action, arguments: argumentsObject });
      } catch (_) {
        await chromeApi.scripting.executeScript({ target: { tabId }, files: ["security.js", "content-script.js"] });
        response = await chromeApi.tabs.sendMessage(tabId, { action, arguments: argumentsObject });
      }
      if (!response?.success) {
        const error = response?.error || {};
        throw Object.assign(new Error(error.message || "The page action failed."), { code: error.code || "PAGE_ACTION_FAILED" });
      }
      return response.result;
    }

    async function validateDestination(url) {
      const normalized = Protocol.validateHttpUrl(url);
      if (!Security.domainAllowed(normalized, state.settings.allowedSites)) {
        throw Object.assign(new Error("The destination is outside the configured allowed sites."), { code: "SITE_NOT_ALLOWED" });
      }
      const parsed = new URL(normalized);
      const pattern = `${parsed.protocol}//${parsed.hostname}/*`;
      const permitted = await chromeApi.permissions.contains({ origins: [pattern] });
      if (!permitted) throw Object.assign(new Error("Chrome site permission is required for this destination."), { code: "SITE_PERMISSION_REQUIRED" });
      return normalized;
    }

    async function executeCommand(action, args) {
      if (action === "browser_list_profiles") return { profiles: [{ profile_id: state.settings.profileId, profile_name: state.settings.profileName }] };
      if (action === "browser_list_tabs") return { tabs: (await chromeApi.tabs.query({})).map(tabMetadata) };
      if (action === "browser_get_active_tab") {
        const [tab] = await chromeApi.tabs.query({ active: true, currentWindow: true });
        if (!tab) throw Object.assign(new Error("There is no active browser tab."), { code: "TAB_NOT_FOUND" });
        return tabMetadata(tab);
      }
      if (action === "browser_open_tab") {
        const url = await validateDestination(args.url);
        const tab = await chromeApi.tabs.create({ url, active: args.active !== false });
        await focusTabWindow(tab);
        return navigationMetadata(tab);
      }
      if (action === "browser_navigate") {
        const url = await validateDestination(args.url);
        let tab;
        try {
          tab = await chromeApi.tabs.update(args.tab_id, { url });
        } catch (_) {
          throw Object.assign(new Error("The requested tab is no longer available."), { code: "TAB_NOT_FOUND" });
        }
        await focusTabWindow(tab);
        return navigationMetadata(tab);
      }
      if (action === "browser_activate_tab") {
        const tab = await chromeApi.tabs.update(args.tab_id, { active: true });
        await focusTabWindow(tab);
        return tabMetadata(tab);
      }
      if (action === "browser_close_tab") { await chromeApi.tabs.remove(args.tab_id); return { tab_id: args.tab_id, closed: true }; }
      if (action === "browser_go_back") { await chromeApi.tabs.goBack(args.tab_id); return { tab_id: args.tab_id, executed: true, verified: false }; }
      if (["browser_get_snapshot", "browser_click", "browser_type", "browser_select", "browser_scroll", "browser_press_key"].includes(action)) {
        return sendPageAction(args.tab_id, action, args);
      }
      if (action === "browser_stop_task") {
        state.stopped = true;
        state.currentTask = null;
        return { stopped: true, verified: true };
      }
      throw Object.assign(new Error("Unknown browser action."), { code: "UNKNOWN_ACTION" });
    }

    async function stopBrowserControl() {
      state.stopped = true;
      state.controlGeneration += 1;
      state.currentTask = null;
      if (state.socket?.readyState === 1 && state.settings) {
        state.socket.send(JSON.stringify({ protocol_version: 1, type: "event", request_id: `event-${randomId()}`, profile_id: state.settings.profileId, event: "browser_control_stopped", payload: {} }));
      }
    }

    async function reconnect() {
      state.stopped = false;
      if (state.reconnectTimer) clearTimeoutFn(state.reconnectTimer);
      state.reconnectTimer = null;
      if (state.socket && state.socket.readyState <= 1) state.socket.close(1000, "Reconnect requested");
      state.socket = null;
      return connect();
    }

    function status() {
      return { connected: state.connected, stopped: state.stopped, profileId: state.settings?.profileId || null, profileName: state.settings?.profileName || null, allowedSites: state.settings?.allowedSites?.length || 0, currentTask: state.currentTask, lastError: state.lastError };
    }

    return { state, connect, reconnect, stopBrowserControl, status, handleBridgeMessage, executeCommand, loadSettings, startHeartbeat };
  }

  const api = { createBridgeController };
  root.SabelServiceWorker = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (typeof chrome !== "undefined" && chrome.runtime?.onInstalled) {
    const controller = createBridgeController();
    chrome.runtime.onInstalled.addListener(() => controller.connect());
    chrome.runtime.onStartup.addListener(() => controller.connect());
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === "get_status") sendResponse(controller.status());
      else if (message?.type === "stop_browser_control") controller.stopBrowserControl().then(() => sendResponse(controller.status()));
      else if (message?.type === "reconnect") controller.reconnect().then(() => sendResponse(controller.status()));
      else return false;
      return true;
    });
    controller.connect();
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
