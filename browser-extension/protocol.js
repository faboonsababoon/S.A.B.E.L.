(function (root) {
  "use strict";

  const PROTOCOL_VERSION = 1;
  const PROFILE_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/;
  const REQUEST_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
  const SAFE_KEYS = new Set(["Enter", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Tab"]);
  const ACTION_FIELDS = Object.freeze({
    browser_list_profiles: [[], []],
    browser_list_tabs: [[], []],
    browser_get_active_tab: [[], []],
    browser_open_tab: [["url"], ["active"]],
    browser_navigate: [["tab_id", "url"], []],
    browser_activate_tab: [["tab_id"], []],
    browser_close_tab: [["tab_id"], []],
    browser_get_snapshot: [["tab_id"], []],
    browser_click: [["tab_id", "snapshot_id", "element_id"], []],
    browser_type: [["tab_id", "snapshot_id", "element_id", "text"], ["clear"]],
    browser_select: [["tab_id", "snapshot_id", "element_id", "value"], []],
    browser_scroll: [["tab_id", "delta_y"], []],
    browser_press_key: [["tab_id", "key"], []],
    browser_go_back: [["tab_id"], []],
    browser_stop_task: [[], ["task_id"]]
  });

  class ProtocolError extends Error {
    constructor(message, code = "INVALID_MESSAGE") {
      super(message);
      this.name = "ProtocolError";
      this.code = code;
    }
  }

  function assertPlainObject(value, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new ProtocolError(`${label} must be an object.`);
    }
  }

  function strictFields(value, required, optional = []) {
    const allowed = new Set([...required, ...optional]);
    for (const key of required) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) {
        throw new ProtocolError(`Missing required field: ${key}.`, "MISSING_FIELD");
      }
    }
    for (const key of Object.keys(value)) {
      if (!allowed.has(key)) {
        throw new ProtocolError("Unknown fields are not allowed.", "UNKNOWN_FIELD");
      }
    }
  }

  function validateProfileId(value) {
    if (typeof value !== "string" || !PROFILE_PATTERN.test(value)) {
      throw new ProtocolError("Invalid profile ID.", "INVALID_PROFILE");
    }
    return value;
  }

  function validateHttpUrl(value) {
    if (typeof value !== "string" || !value || value.length > 4096) {
      throw new ProtocolError("Invalid browser URL.", "INVALID_URL");
    }
    let parsed;
    try {
      parsed = new URL(value);
    } catch (_) {
      throw new ProtocolError("Invalid browser URL.", "INVALID_URL");
    }
    if (!new Set(["http:", "https:"]).has(parsed.protocol) || parsed.username || parsed.password) {
      throw new ProtocolError("Only credential-free HTTP and HTTPS URLs are allowed.", "UNSAFE_URL");
    }
    return parsed.href;
  }

  function validateSettings(value) {
    assertPlainObject(value, "Settings");
    const profileId = validateProfileId(value.profileId);
    if (!new Set(["personal", "nyu"]).has(profileId)) {
      throw new ProtocolError("Profile ID must be personal or nyu.", "INVALID_PROFILE");
    }
    if (typeof value.profileName !== "string" || !value.profileName.trim() || value.profileName.length > 64) {
      throw new ProtocolError("Profile name is required.");
    }
    if (!Number.isInteger(value.port) || value.port < 1 || value.port > 65535) {
      throw new ProtocolError("Bridge port must be between 1 and 65535.");
    }
    if (typeof value.token !== "string" || !value.token || value.token.length > 256) {
      throw new ProtocolError("Authentication token is required.");
    }
    if (!Array.isArray(value.allowedSites) || value.allowedSites.length > 100) {
      throw new ProtocolError("Allowed sites must be a bounded list.");
    }
    const allowedSites = [...new Set(value.allowedSites.map((site) => {
      if (typeof site !== "string") throw new ProtocolError("Invalid allowed site.");
      const normalized = site.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^\*\./, "");
      if (!/^[a-z0-9.-]+$/.test(normalized) || !normalized.includes(".")) {
        throw new ProtocolError("Invalid allowed site.");
      }
      const security = root.SabelSecurity || (typeof require !== "undefined" ? require("./security.js") : null);
      if (!security?.siteAccessAllowed(normalized)) {
        throw new ProtocolError("Banking, medical, payment, and password-manager sites are outside Browser Copilot v1 scope.", "RESTRICTED_SITE");
      }
      return normalized;
    }))];
    return { profileId, profileName: value.profileName.trim(), port: value.port, token: value.token, allowedSites };
  }

  function registrationMessage(settings, instanceId, requestId) {
    const validated = validateSettings(settings);
    if (typeof instanceId !== "string" || !instanceId || instanceId.length > 128) {
      throw new ProtocolError("Invalid instance ID.");
    }
    if (!REQUEST_PATTERN.test(requestId)) throw new ProtocolError("Invalid request ID.");
    return {
      protocol_version: PROTOCOL_VERSION,
      type: "register",
      request_id: requestId,
      token: validated.token,
      profile_id: validated.profileId,
      profile_name: validated.profileName,
      extension_version: "0.1.0",
      instance_id: instanceId
    };
  }

  function validateCommand(message) {
    assertPlainObject(message, "Message");
    strictFields(message, ["protocol_version", "type", "request_id", "profile_id", "action", "arguments"]);
    if (message.protocol_version !== PROTOCOL_VERSION) throw new ProtocolError("Unsupported protocol version.", "UNSUPPORTED_VERSION");
    if (message.type !== "command") throw new ProtocolError("Unknown message type.", "UNKNOWN_MESSAGE_TYPE");
    if (typeof message.request_id !== "string" || !REQUEST_PATTERN.test(message.request_id)) throw new ProtocolError("Invalid request ID.");
    validateProfileId(message.profile_id);
    if (!Object.prototype.hasOwnProperty.call(ACTION_FIELDS, message.action)) throw new ProtocolError("Unknown browser action.", "UNKNOWN_ACTION");
    assertPlainObject(message.arguments, "Arguments");
    const [required, optional] = ACTION_FIELDS[message.action];
    strictFields(message.arguments, required, optional);
    for (const key of ["tab_id", "delta_y"]) {
      if (key in message.arguments && !Number.isInteger(message.arguments[key])) throw new ProtocolError(`${key} must be an integer.`);
    }
    for (const key of ["url", "snapshot_id", "element_id", "value", "text", "task_id"]) {
      if (key in message.arguments && (typeof message.arguments[key] !== "string" || !message.arguments[key] || message.arguments[key].length > (key === "text" ? 2000 : 4096))) {
        throw new ProtocolError(`Invalid ${key}.`);
      }
    }
    if ("url" in message.arguments) validateHttpUrl(message.arguments.url);
    if ("key" in message.arguments && !SAFE_KEYS.has(message.arguments.key)) throw new ProtocolError("Unsafe key.", "UNSAFE_KEY");
    for (const key of ["active", "clear"]) {
      if (key in message.arguments && typeof message.arguments[key] !== "boolean") throw new ProtocolError(`${key} must be boolean.`);
    }
    return message;
  }

  function responseMessage(command, success, result = {}, error = null) {
    return {
      protocol_version: PROTOCOL_VERSION,
      type: "response",
      request_id: command.request_id,
      profile_id: command.profile_id,
      success: Boolean(success),
      result: result && typeof result === "object" ? result : {},
      error: error && typeof error === "object" ? error : null
    };
  }

  const api = { PROTOCOL_VERSION, ACTION_FIELDS, ProtocolError, validateProfileId, validateHttpUrl, validateSettings, registrationMessage, validateCommand, responseMessage };
  root.SabelProtocol = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
