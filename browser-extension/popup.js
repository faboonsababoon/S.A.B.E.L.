"use strict";

function render(status) {
  document.getElementById("profile").textContent = status?.profileName || "Not configured";
  document.getElementById("connection").textContent = status?.connected ? "Connected" : status?.stopped ? "Stopped" : "Disconnected";
  document.getElementById("allowed-sites").textContent = String(status?.allowedSites || 0);
  document.getElementById("current-task").textContent = status?.currentTask || "None";
  const error = document.getElementById("error");
  error.textContent = status?.lastError || "";
  error.hidden = !status?.lastError;
}

chrome.runtime.sendMessage({ type: "get_status" }).then(render);
document.getElementById("stop").addEventListener("click", () => chrome.runtime.sendMessage({ type: "stop_browser_control" }).then(render));
document.getElementById("reconnect").addEventListener("click", () => chrome.runtime.sendMessage({ type: "reconnect" }).then(render));
document.getElementById("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
