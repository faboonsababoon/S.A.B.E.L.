"use strict";

const fields = {
  profileId: document.getElementById("profile-id"),
  profileName: document.getElementById("profile-name"),
  port: document.getElementById("port"),
  token: document.getElementById("token"),
  allowedSites: document.getElementById("allowed-sites")
};
const statusElement = document.getElementById("status");
const tokenState = document.getElementById("token-state");

async function load() {
  const stored = await chrome.storage.local.get(["profileId", "profileName", "port", "token", "allowedSites"]);
  fields.profileId.value = stored.profileId || "personal";
  fields.profileName.value = stored.profileName || "Personal";
  fields.port.value = stored.port || 8765;
  fields.token.value = "";
  fields.allowedSites.value = (stored.allowedSites || ["youtube.com", "google.com"]).join("\n");
  tokenState.textContent = stored.token ? "Token saved: •••••••• (enter a value only to replace it)." : "No token saved.";
}

document.getElementById("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const existing = await chrome.storage.local.get(["token"]);
    const settings = SabelProtocol.validateSettings({
      profileId: fields.profileId.value,
      profileName: fields.profileName.value,
      port: Number(fields.port.value),
      token: fields.token.value || existing.token || "",
      allowedSites: fields.allowedSites.value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)
    });
    const origins = settings.allowedSites.flatMap(SabelSecurity.permissionPatterns);
    const granted = await chrome.permissions.request({ origins });
    if (!granted) throw new Error("Chrome did not grant the requested site permissions.");
    await chrome.storage.local.set(settings);
    fields.token.value = "";
    tokenState.textContent = "Token saved: •••••••• (enter a value only to replace it).";
    await chrome.runtime.sendMessage({ type: "reconnect" });
    statusElement.textContent = `Settings saved. Reconnecting as ${settings.profileName} (${settings.profileId})…`;
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.getElementById("clear-token").addEventListener("click", async () => {
  await chrome.storage.local.remove("token");
  fields.token.value = "";
  tokenState.textContent = "No token saved.";
  statusElement.textContent = "Token cleared.";
});

document.getElementById("test").addEventListener("click", async () => {
  const status = await chrome.runtime.sendMessage({ type: "get_status" });
  statusElement.textContent = status?.connected ? `Connected as ${status.profileName}.` : status?.lastError || "Not connected.";
});

load();
