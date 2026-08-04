"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const Protocol = require("../protocol.js");
const Security = require("../security.js");

test("settings validation accepts Personal and NYU profiles", () => {
  for (const profileId of ["personal", "nyu"]) {
    const value = Protocol.validateSettings({ profileId, profileName: "Profile", port: 8765, token: "secret", allowedSites: ["youtube.com", "google.com"] });
    assert.equal(value.profileId, profileId);
  }
  assert.throws(() => Protocol.validateSettings({ profileId: "work!", profileName: "Work", port: 8765, token: "secret", allowedSites: [] }), /Invalid profile/);
});

test("registration message has the strict authenticated shape", () => {
  const message = Protocol.registrationMessage({ profileId: "personal", profileName: "Personal", port: 8765, token: "secret", allowedSites: ["youtube.com"] }, "instance-1", "register-1");
  assert.deepEqual(Object.keys(message).sort(), ["extension_version", "instance_id", "profile_id", "profile_name", "protocol_version", "request_id", "token", "type"]);
  assert.equal(message.protocol_version, 1);
  assert.equal(message.type, "register");
});

test("wrong protocol, unknown actions, arbitrary selectors, and arbitrary JavaScript are rejected", () => {
  const base = { protocol_version: 1, type: "command", request_id: "request-1", profile_id: "personal", action: "browser_list_tabs", arguments: {} };
  assert.throws(() => Protocol.validateCommand({ ...base, protocol_version: 2 }), /Unsupported/);
  assert.throws(() => Protocol.validateCommand({ ...base, action: "evaluate_javascript", arguments: { code: "alert(1)" } }), /Unknown browser action/);
  assert.throws(() => Protocol.validateCommand({ ...base, action: "browser_click", arguments: { tab_id: 1, snapshot_id: "s", element_id: "e", selector: "#unsafe" } }), /Unknown fields/);
});

test("URL and allowed-domain validation reject restricted pages", () => {
  for (const url of ["file:///tmp/a", "javascript:alert(1)", "chrome://settings", "chrome-extension://abc/page.html"]) {
    assert.throws(() => Protocol.validateHttpUrl(url));
  }
  assert.equal(Security.domainAllowed("https://www.youtube.com/watch?v=1", ["youtube.com"]), true);
  assert.equal(Security.domainAllowed("https://youtube.example.com/", ["youtube.com"]), false);
});

test("permission patterns remain domain-specific optional grants", () => {
  const patterns = Security.permissionPatterns("youtube.com");
  assert.ok(patterns.includes("https://youtube.com/*"));
  assert.ok(patterns.includes("https://*.youtube.com/*"));
  assert.ok(!patterns.includes("<all_urls>"));
});

test("restricted high-risk site categories cannot enter the extension allowlist", () => {
  const settings = { profileId: "personal", profileName: "Personal", port: 8765, token: "secret", allowedSites: ["youtube.com"] };
  for (const site of ["chase.com", "patient.mychart.com", "checkout.stripe.com", "1password.com"]) {
    assert.throws(() => Protocol.validateSettings({ ...settings, allowedSites: [site] }), /outside Browser Copilot/);
  }
  assert.equal(Security.siteAccessAllowed("youtube.com"), true);
});
