# SABEL Browser Copilot extension

This is an unpacked Chrome Manifest V3 extension for the local SABEL assistant.
It connects only to `ws://127.0.0.1:<configured-port>`, authenticates with the
local token, and accepts the strict protocol implemented in `protocol.js`.

Load this directory separately in the Personal and NYU Chrome profiles. Configure
the profile ID/name, port, hidden token, and an explicit domain allowlist in the
options page. Chrome optional host permissions must also be granted for those
domains. The popup reports connection state and provides an immediate stop.

Each Chrome installation must have a distinct configured identity: Personal uses
profile ID `personal` and NYU uses `nyu`. A reconnect from the same installation
replaces only its stale socket. A second extension instance that claims an
already-connected profile ID is rejected, so a misconfigured NYU installation
cannot take over Personal. Commands whose `profile_id` does not exactly match the
receiving extension are returned as `PROFILE_MISMATCH` and are not executed.

Banking, medical, payment, and password-manager domains are intentionally outside
Browser Copilot scope and are rejected by settings validation. File URLs and
other non-HTTP(S) schemes are never requested.

The content script returns bounded snapshots and uses short-lived element IDs. It
does not expose passwords, hidden inputs, scripts, styles, cookies, storage, or
arbitrary DOM access. It never evaluates model-generated JavaScript or selectors.

Full setup and safety instructions are in `../README.md`; the manual procedure is
`../docs/MANUAL_BROWSER_TEST.md`.

Run the isolated mocked extension tests with:

```bash
npm test
```
