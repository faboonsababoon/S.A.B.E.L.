"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { SnapshotController, MAX_ELEMENTS, MAX_SUMMARY } = require("../content-script.js");

function fakeElement(values = {}) {
  const attributes = values.attributes || {};
  return {
    tagName: values.tagName || "BUTTON",
    type: values.type || "",
    name: values.name || "",
    id: values.id || "",
    autocomplete: values.autocomplete || "",
    placeholder: values.placeholder || "",
    innerText: values.innerText || "",
    textContent: values.textContent || values.innerText || "",
    href: values.href || "",
    disabled: Boolean(values.disabled),
    checked: Boolean(values.checked),
    selected: Boolean(values.selected),
    options: values.options || [],
    value: values.value || "",
    isConnected: values.isConnected !== false,
    styleValues: values.styleValues || { display: "block", visibility: "visible", opacity: "1" },
    rect: values.rect || { x: 1, y: 2, width: 100, height: 20 },
    clicked: false,
    getAttribute(name) { return attributes[name] || ""; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(attributes, name); },
    getBoundingClientRect() { return this.rect; },
    click() { this.clicked = true; },
    focus() {},
    dispatchEvent() { return true; }
  };
}

function harness(elements, options = {}) {
  const documentObject = {
    title: options.title || "Test page",
    body: { innerText: options.bodyText || "Visible page text", dispatchEvent() {} },
    activeElement: null,
    querySelectorAll() { return elements; }
  };
  const windowObject = {
    location: { href: options.url || "https://www.youtube.com/results" },
    getComputedStyle(element) { return element.styleValues; },
    Event: class {},
    KeyboardEvent: class {},
    scrollBy() {}
  };
  let next = 0;
  let now = options.now || 1000;
  const controller = new SnapshotController(documentObject, windowObject, { idFactory: () => String(++next), clock: () => now });
  return { controller, documentObject, windowObject, setNow(value) { now = value; } };
}

test("snapshot includes visible links and buttons but excludes hidden and password elements", () => {
  const visibleButton = fakeElement({ innerText: "Open channel" });
  const visibleLink = fakeElement({ tagName: "A", innerText: "Taz Skylar", href: "https://www.youtube.com/@TazSkylar" });
  const hidden = fakeElement({ innerText: "Hidden", styleValues: { display: "none", visibility: "visible", opacity: "1" } });
  const password = fakeElement({ tagName: "INPUT", type: "password", value: "never-expose-this" });
  const snapshot = harness([visibleButton, visibleLink, hidden, password]).controller.buildSnapshot(7);
  assert.equal(snapshot.interactive_elements.length, 2);
  assert.ok(snapshot.interactive_elements.some((item) => item.visible_text === "Taz Skylar"));
  assert.ok(!JSON.stringify(snapshot).includes("never-expose-this"));
});

test("snapshot caps element count, element IDs are unique, and summary is bounded", () => {
  const elements = Array.from({ length: MAX_ELEMENTS + 20 }, (_, index) => fakeElement({ innerText: `Button ${index}` }));
  const snapshot = harness(elements, { bodyText: "x".repeat(MAX_SUMMARY + 500) }).controller.buildSnapshot(1);
  assert.equal(snapshot.interactive_elements.length, MAX_ELEMENTS);
  const ids = snapshot.interactive_elements.map((item) => item.element_id);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(snapshot.visible_text_summary.length, MAX_SUMMARY);
});

test("element references expire after a new snapshot or page change", async () => {
  const button = fakeElement({ innerText: "Channel" });
  const { controller, windowObject, setNow } = harness([button]);
  const first = controller.buildSnapshot(1);
  const firstElement = first.interactive_elements[0].element_id;
  controller.buildSnapshot(1);
  await assert.rejects(() => controller.execute("browser_click", { tab_id: 1, snapshot_id: first.snapshot_id, element_id: firstElement }), (error) => error.code === "STALE_SNAPSHOT");

  const current = controller.buildSnapshot(1);
  windowObject.location.href = "https://www.youtube.com/watch?v=changed";
  await assert.rejects(() => controller.execute("browser_click", { tab_id: 1, snapshot_id: current.snapshot_id, element_id: current.interactive_elements[0].element_id }), (error) => error.code === "STALE_SNAPSHOT");

  const aged = controller.buildSnapshot(1);
  setNow(40001);
  await assert.rejects(() => controller.execute("browser_click", { tab_id: 1, snapshot_id: aged.snapshot_id, element_id: aged.interactive_elements[0].element_id }), (error) => error.code === "STALE_SNAPSHOT");
});

test("typing is allowed for ordinary inputs and blocked for sensitive inputs", async () => {
  const search = fakeElement({ tagName: "INPUT", type: "search" });
  const normal = harness([search]).controller;
  const snapshot = normal.buildSnapshot(1);
  const result = await normal.execute("browser_type", { tab_id: 1, snapshot_id: snapshot.snapshot_id, element_id: snapshot.interactive_elements[0].element_id, text: "Taz Skylar", clear: true });
  assert.equal(result.verified, true);
  assert.equal(search.value, "Taz Skylar");

  const creditCard = fakeElement({ tagName: "INPUT", type: "text", name: "credit_card_number" });
  const sensitiveSnapshot = harness([creditCard]).controller.buildSnapshot(1);
  assert.equal(sensitiveSnapshot.interactive_elements.length, 0);
});

test("fixed interactive selector never accepts a model-provided selector", () => {
  const source = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "content-script.js"), "utf8");
  assert.ok(!source.includes("querySelector(argumentsObject"));
  assert.ok(!source.includes("eval("));
  assert.ok(!source.includes("new Function"));
});

test("download links carry filename metadata for Python confirmation policy", () => {
  const download = fakeElement({
    tagName: "A",
    innerText: "Download installer",
    href: "https://www.youtube.com/setup.dmg",
    attributes: { download: "setup.dmg" }
  });
  const snapshot = harness([download]).controller.buildSnapshot(1);
  assert.equal(snapshot.interactive_elements[0].download, "setup.dmg");
});

test("select options are bounded structured data and file fields are omitted", () => {
  const select = fakeElement({
    tagName: "SELECT",
    options: Array.from({ length: 40 }, (_, index) => ({
      value: `value-${index}`,
      textContent: `Option ${index}`,
      selected: index === 2,
      disabled: false
    }))
  });
  const upload = fakeElement({ tagName: "INPUT", type: "file", name: "upload" });
  const snapshot = harness([select, upload]).controller.buildSnapshot(1);
  assert.equal(snapshot.interactive_elements.length, 1);
  assert.equal(snapshot.interactive_elements[0].options.length, 30);
  assert.deepEqual(snapshot.interactive_elements[0].options[2], {
    value: "value-2",
    label: "Option 2",
    selected: true,
    disabled: false
  });
});
