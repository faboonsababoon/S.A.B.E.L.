(function (root) {
  "use strict";

  const Security = root.SabelSecurity || (typeof require !== "undefined" ? require("./security.js") : null);
  const MAX_ELEMENTS = 120;
  const MAX_SUMMARY = 6000;
  const MAX_ELEMENT_TEXT = 240;
  const MAX_SELECT_OPTIONS = 30;
  const SNAPSHOT_TTL_MS = 30000;
  const INTERACTIVE_SELECTOR = [
    "a[href]", "button", "input", "textarea", "select",
    "[role='button']", "[role='link']", "[role='checkbox']", "[role='radio']"
  ].join(",");

  const browserContentContext = typeof chrome !== "undefined" && chrome.runtime?.onMessage && typeof document !== "undefined";
  if (browserContentContext && root.__SABEL_CONTENT_INSTALLED__) return;
  if (browserContentContext) root.__SABEL_CONTENT_INSTALLED__ = true;

  class SnapshotController {
    constructor(documentObject, windowObject, options = {}) {
      this.document = documentObject;
      this.window = windowObject;
      this.clock = options.clock || (() => Date.now());
      this.idFactory = options.idFactory || (() => root.crypto?.randomUUID?.() || `id-${Math.random().toString(16).slice(2)}`);
      this.current = null;
    }

    visible(element) {
      const style = this.window.getComputedStyle(element);
      if (!style || style.display === "none" || ["hidden", "collapse"].includes(style.visibility) || Number(style.opacity) === 0) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }

    accessibleName(element) {
      return Security.sanitizeText(
        element.getAttribute?.("aria-label") || element.getAttribute?.("title") || element.placeholder || "",
        MAX_ELEMENT_TEXT
      );
    }

    buildSnapshot(tabId) {
      const snapshotId = `snapshot-${this.idFactory()}`;
      const elements = new Map();
      const interactive = [];
      const candidates = Array.from(this.document.querySelectorAll(INTERACTIVE_SELECTOR));
      for (const element of candidates) {
        if (interactive.length >= MAX_ELEMENTS) break;
        if (!element || element.disabled || !this.visible(element)) continue;
        const type = String(element.type || "").toLowerCase();
        if (type === "hidden" || Security.isSensitiveField(element)) continue;
        const elementId = `element-${this.idFactory()}`;
        const rect = element.getBoundingClientRect();
        const optionValues = String(element.tagName || "").toLowerCase() === "select"
          ? Array.from(element.options || []).slice(0, MAX_SELECT_OPTIONS).map((option) => ({
              value: Security.sanitizeText(option.value || "", 200),
              label: Security.sanitizeText(option.textContent || option.label || "", MAX_ELEMENT_TEXT),
              selected: Boolean(option.selected),
              disabled: Boolean(option.disabled)
            }))
          : [];
        elements.set(elementId, element);
        interactive.push({
          element_id: elementId,
          role: Security.sanitizeText(element.getAttribute?.("role") || "", 40),
          tag: String(element.tagName || "").toLowerCase(),
          visible_text: Security.sanitizeText(element.innerText || element.textContent || "", MAX_ELEMENT_TEXT),
          accessible_name: this.accessibleName(element),
          input_type: type,
          href: element.href && /^https?:/i.test(element.href) ? String(element.href).slice(0, 2048) : null,
          download: element.hasAttribute?.("download")
            ? Security.sanitizeText(element.getAttribute?.("download") || "unknown filename", 240)
            : null,
          submits_form: type === "submit" || Boolean(element.form && ["button", "input"].includes(String(element.tagName || "").toLowerCase())),
          disabled: false,
          checked: Boolean(element.checked),
          selected: Boolean(element.selected),
          options: optionValues,
          bounding: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) }
        });
      }
      const summary = Security.sanitizeText(this.document.body?.innerText || "", MAX_SUMMARY);
      this.current = { snapshotId, tabId, url: String(this.window.location.href), createdAt: this.clock(), elements };
      return {
        snapshot_id: snapshotId,
        tab_id: tabId,
        title: Security.sanitizeText(this.document.title || "", 300),
        url: String(this.window.location.href),
        visible_text_summary: summary,
        interactive_elements: interactive,
        timestamp: new Date(this.clock()).toISOString()
      };
    }

    resolve(argumentsObject, expectedKinds) {
      const current = this.current;
      if (!current || argumentsObject.snapshot_id !== current.snapshotId || argumentsObject.tab_id !== current.tabId) {
        throw Object.assign(new Error("The snapshot is stale."), { code: "STALE_SNAPSHOT" });
      }
      if (this.clock() - current.createdAt > SNAPSHOT_TTL_MS || String(this.window.location.href) !== current.url) {
        this.current = null;
        throw Object.assign(new Error("The page changed after the snapshot."), { code: "STALE_SNAPSHOT" });
      }
      const element = current.elements.get(argumentsObject.element_id);
      if (!element || !element.isConnected || element.disabled || !this.visible(element)) {
        throw Object.assign(new Error("The page element is no longer available."), { code: "STALE_SNAPSHOT" });
      }
      const tag = String(element.tagName || "").toLowerCase();
      const role = String(element.getAttribute?.("role") || "").toLowerCase();
      if (!expectedKinds.has(tag) && !expectedKinds.has(role)) {
        throw Object.assign(new Error("That action does not match the element type."), { code: "ELEMENT_TYPE_MISMATCH" });
      }
      return element;
    }

    async execute(action, args) {
      if (action === "browser_get_snapshot") return this.buildSnapshot(args.tab_id);
      if (action === "browser_click") {
        const element = this.resolve(args, new Set(["a", "button", "input", "select", "link", "checkbox", "radio"]));
        element.click();
        return { executed: true, verified: false };
      }
      if (action === "browser_type") {
        const element = this.resolve(args, new Set(["input", "textarea", "textbox"]));
        if (Security.isSensitiveField(element)) throw Object.assign(new Error("Typing into sensitive fields is blocked."), { code: "SENSITIVE_FIELD" });
        element.focus();
        if (args.clear) element.value = "";
        element.value = `${element.value || ""}${args.text}`;
        element.dispatchEvent(new this.window.Event("input", { bubbles: true }));
        element.dispatchEvent(new this.window.Event("change", { bubbles: true }));
        return { executed: true, verified: element.value.endsWith(args.text) };
      }
      if (action === "browser_select") {
        const element = this.resolve(args, new Set(["select"]));
        const option = Array.from(element.options || []).find((item) => item.value === args.value);
        if (!option) throw Object.assign(new Error("That option is not available."), { code: "OPTION_NOT_FOUND" });
        element.value = option.value;
        element.dispatchEvent(new this.window.Event("change", { bubbles: true }));
        return { executed: true, verified: element.value === args.value };
      }
      if (action === "browser_scroll") {
        this.window.scrollBy({ top: args.delta_y, behavior: "smooth" });
        return { executed: true, verified: false };
      }
      if (action === "browser_press_key") {
        const active = this.document.activeElement || this.document.body;
        active.dispatchEvent(new this.window.KeyboardEvent("keydown", { key: args.key, bubbles: true }));
        active.dispatchEvent(new this.window.KeyboardEvent("keyup", { key: args.key, bubbles: true }));
        return { executed: true, verified: false };
      }
      throw Object.assign(new Error("Unknown page action."), { code: "UNKNOWN_ACTION" });
    }
  }

  const api = { SnapshotController, MAX_ELEMENTS, MAX_SUMMARY, MAX_ELEMENT_TEXT, MAX_SELECT_OPTIONS, SNAPSHOT_TTL_MS, INTERACTIVE_SELECTOR };
  root.SabelContent = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (browserContentContext) {
    const controller = new SnapshotController(document, window);
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      controller.execute(message.action, message.arguments || {})
        .then((result) => sendResponse({ success: true, result }))
        .catch((error) => sendResponse({ success: false, error: { code: error.code || "PAGE_ACTION_FAILED", message: error.message } }));
      return true;
    });
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
