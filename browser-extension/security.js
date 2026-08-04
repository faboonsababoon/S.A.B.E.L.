(function (root) {
  "use strict";

  const SENSITIVE_TYPES = new Set(["password", "hidden"]);
  const SENSITIVE_AUTOCOMPLETE = /(?:cc-|current-password|new-password|one-time-code)/i;
  const SENSITIVE_NAME = /(?:password|passwd|credit.?card|card.?number|security.?code|cvv|cvc|mfa|one.?time.?code|otp|social.?security|ssn)/i;
  const RESTRICTED_SITE_LABEL = /(?:^|[.-])(?:bank|banking|checkout|clinic|credit|dashlane|health|hospital|lastpass|medical|mychart|patient|password|payment|paypal|venmo|wallet)(?:[.-]|$)/i;
  const RESTRICTED_SITES = new Set([
    "1password.com", "bitwarden.com", "chase.com", "bankofamerica.com",
    "wellsfargo.com", "citi.com", "capitalone.com", "stripe.com"
  ]);

  function sanitizeText(value, limit = 240) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function isSensitiveField(element) {
    const type = String(element.type || "").toLowerCase();
    const autocomplete = String(element.autocomplete || "");
    const name = `${element.name || ""} ${element.id || ""} ${element.getAttribute?.("aria-label") || ""}`;
    return SENSITIVE_TYPES.has(type) || SENSITIVE_AUTOCOMPLETE.test(autocomplete) || SENSITIVE_NAME.test(name);
  }

  function domainAllowed(url, allowedSites) {
    let hostname;
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) return false;
      hostname = parsed.hostname.toLowerCase().replace(/\.$/, "");
    } catch (_) {
      return false;
    }
    return allowedSites.some((site) => hostname === site || hostname.endsWith(`.${site}`));
  }

  function permissionPatterns(site) {
    const normalized = String(site).toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^\*\./, "");
    return [
      `https://${normalized}/*`, `https://*.${normalized}/*`,
      `http://${normalized}/*`, `http://*.${normalized}/*`
    ];
  }

  function siteAccessAllowed(site) {
    const normalized = String(site).toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^\*\./, "");
    return !RESTRICTED_SITE_LABEL.test(normalized) && ![...RESTRICTED_SITES].some(
      (restricted) => normalized === restricted || normalized.endsWith(`.${restricted}`)
    );
  }

  const api = { sanitizeText, isSensitiveField, domainAllowed, permissionPatterns, siteAccessAllowed };
  root.SabelSecurity = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
