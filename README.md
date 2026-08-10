# SABEL — Local macOS Assistant with a Constrained Browser Agent

SABEL is a typed macOS assistant. A local Ollama language model interprets every
normal request and selects from a small, explicit tool allowlist. Ordinary Mac
actions stay local. Difficult, current, multi-source research may be delegated to
OpenAI only after Python applies the configured cloud policy. Browser Copilot
adds an authenticated, localhost-only Chrome extension bridge for profile-aware
navigation, constrained page observation, controlled interaction, and
objective-specific verification. Multi-step browser goals use a bounded local
observe–plan–validate–act loop; simple opens and searches remain deterministic
one-step tools.

Voice, wake words, a GUI, remote access, sleep control, and background listening
are intentionally postponed.

## The central security rule

```text
The model proposes.
Python validates.
The extension executes only approved commands.
SABEL observes the result.
The user authorizes scope expansion and consequential actions.
```

Neither Ollama nor OpenAI receives a shell, generated Python, generated
AppleScript, or direct `subprocess.run()` access. Unknown tools, extra arguments,
unsupported URL schemes, and confirmation bypasses are rejected.

## Browser Copilot terms

- **WebSocket:** a persistent, two-way message connection used by Python and the
  extension after a normal HTTP handshake.
- **Localhost / loopback interface:** this Mac talking to itself. SABEL binds the
  bridge specifically to `127.0.0.1`, so another computer cannot connect.
- **Chrome Manifest V3:** Chrome's current extension format and security model.
- **Service worker:** the extension's background controller for the bridge and
  Chrome tab APIs; Chrome may suspend it while idle.
- **Content script:** reviewed extension code injected only into an allowed page
  to observe or interact with that page.
- **DOM:** Chrome's structured in-memory representation of the rendered webpage.
- **Browser profile:** one separately configured Chrome context, such as Personal
  or NYU, with Chrome—not SABEL—owning its login sessions.
- **Message protocol:** the exact versioned JSON envelope and command vocabulary
  both sides must validate before accepting a message.
- **Request ID:** a unique label that pairs one command with its response and
  prevents unknown or duplicate responses from being accepted.
- **Authentication token:** a random local secret proving that an extension knows
  the bridge credential; it is used together with origin checking.
- **Schema validation:** rejecting missing, extra, incorrectly typed, oversized,
  or otherwise invalid fields before using their values.
- **Origin:** the identity sent during the WebSocket handshake. Only explicitly
  registered `chrome-extension://` origins pass.
- **Allowlist:** a deliberately small set of permitted profiles, domains, tools,
  actions, or values; everything else is denied by default.
- **Task scope:** the Python-owned objective plus authorized profile, domains,
  actions, step count, and direct user-approved expansions.
- **Least privilege:** granting only the access needed for the current task and
  no broader browser or operating-system capability.
- **Prompt injection:** text attempting to make a model disregard its real rules
  or objective.
- **Indirect prompt injection:** that attempt arriving through untrusted content,
  such as instructions embedded in a webpage.
- **Consequential action:** an action with an external effect—sending, submitting,
  purchasing, enrolling, deleting, uploading, or disclosing information.
- **Verification:** observing objective-specific evidence after execution instead
  of assuming that a requested result happened.
- **Stale element:** a page control whose snapshot expired or became invalid after
  navigation or page changes; SABEL must observe again before acting.
- **Snapshot:** a bounded summary of the current page and its visible interactive
  elements, not raw HTML or unrestricted DOM access.
- **Transport abstraction:** the interface separating browser-task logic from the
  WebSocket implementation so Native Messaging can replace it later.

## Hybrid architecture

```text
Raw typed request
        ↓
Deterministic exit/cancel controls
        ↓
Pending destructive / clarification check
        ↓
Typed local result
response / tool_calls / clarification / delegation / error
        ↓
Python validation and user-facing rendering
        ↓
┌──────────────────────────┬──────────────────────────────────┐
│ Approved local operation │ delegate_to_openai proposal      │
│                          │                                  │
│ Service / app / search   │ Python cloud mode: off/ask/auto │
│ Status / capabilities    │              ↓                   │
│ Confirmed Trash action   │ OpenAI Responses API            │
│ Stored research source   │ hosted web_search only           │
└──────────────────────────┴──────────────────────────────────┘
        ↓                                  ↓
Concise local result             Sourced research answer
        └──────────────────┬───────────────┘
                           ↓
                    SABEL command loop
```

The OpenAI client is not created during startup or for a local command. Ollama
must classify a request as cloud-worthy first, then cloud policy must permit it.

## What stays local and free of OpenAI API charges

Examples:

```text
Open YouTube.
Take me to github.com.
Launch Visual Studio Code.
Go to SSundee's YouTube channel.
Find the latest MrBeast video on YouTube.
Open GitHub in Personal and find the S.A.B.E.L. repository.
Go to https://docs.python.org/3/library/subprocess.html and find subprocess.run.
Search Google for the NYU academic calendar.
Is my Trash empty?
What can you do?
Show your status.
Clear my Trash.
```

Ollama performs local **inference**—processing the sentence to identify its
intent. It can request these local tools:

- `show_browser_profiles`
- `show_browser_tabs`
- `open_service`
- `search_web`
- `search_youtube`
- `browser_copilot_task`
- `stop_browser_task`
- `show_recent_browser_actions`
- `open_website`
- `open_application`
- `open_spotify_search`
- `open_youtube_search`
- `open_web_search`
- `empty_trash`
- `get_trash_status`
- `show_capabilities`
- `show_status`
- `exit_assistant`
- `open_research_source`
- `delegate_to_openai` (a proposal, not authority to spend credits)
- `request_clarification` (state transition, not an OS action)

The wording is not matched against a large collection of phrases. The tool
schemas describe capabilities; the local model interprets normal language.
Greetings, acknowledgements, explanations, and casual conversation return as
typed conversational responses without requiring a tool.

## Clarifications and typed results

Router output is represented explicitly as `response`, `tool_calls`,
`clarification`, `cloud_delegation`, or `error`. Native tool calls cannot be
mistaken for visible text. One renderer is the final boundary for every router
result and action result. It rejects internal tool identifiers, raw dictionaries,
dataclass/SDK representations, and application paths. Only the final rendered
message enters conversation history.

A clarification is a separate expiring state containing the original request,
question, intended action, collected slots, missing slots, proposed values, and
creation time. Replies are classified as supplying information, confirming,
rejecting/correcting, cancelling, asking a question, or replacing the request.
For example, `play circles` can retain the query and a proposed Spotify search;
`yes`, `yes, circles on Spotify`, and `no, use YouTube` then resolve from that
Python-owned state rather than starting a context-free request.

## Browser search versus cloud research

These are intentionally different:

```text
Search Google for the best laptops
→ opens a normal Google results page locally
→ no OpenAI request
```

```text
Research the five best laptops currently on the market and explain why
→ Ollama proposes cloud delegation
→ Python applies off/ask/auto policy
→ OpenAI uses hosted web search if permitted
→ API credits may be used
```

OpenAI handles current multi-source research, comparisons, recent developments,
and longer synthesis. It receives no Mac-action tools and cannot directly open or
change anything on this computer.

## Project structure

```text
S.A.B.E.L./
├── .env.example
├── .gitignore
├── README.md
├── main.py
├── requirements.txt
├── sabel/
│   ├── __init__.py
│   ├── actions.py                Reviewed macOS operations
│   ├── assistant.py              Hybrid coordinator
│   ├── browser_copilot.py        One-action planner and verification
│   ├── browser_models.py         Profile, result, task, and audit types
│   ├── browser_policy.py         Scope and prompt-injection policy
│   ├── browser_protocol.py       Strict versioned bridge schemas
│   ├── browser_routing.py        Search slots, URL building, and verification
│   ├── browser_runtime.py        Background async bridge runtime
│   ├── browser_security.py       Token, origin, URL, and rate limits
│   ├── browser_tasks.py          Python-owned task and audit state
│   ├── browser_transport.py      Transport interface and WebSocket server
│   ├── cloud_router.py           off/ask/auto policy
│   ├── config.py                 Environment and CLI settings
│   ├── confirmations.py          Destructive confirmation rules
│   ├── conversation_state.py     Bounded in-memory state
│   ├── errors.py                 Project errors
│   ├── keychain.py               Secure OpenAI credential storage
│   ├── local_router.py           Ollama routing
│   ├── media.py                  Media slot extraction and defaults
│   ├── ollama_client.py          Reusable native tool-call client
│   ├── openai_research.py        Responses API hosted web research
│   ├── response_renderer.py      Central visible-response boundary
│   ├── services.py               Reviewed service registry
│   ├── tool_dispatcher.py        Local allowlist and validation
│   └── tool_schemas.py           Ollama-native tool schemas
├── browser-extension/            Unpacked Manifest V3 extension
├── docs/
│   └── MANUAL_BROWSER_TEST.md    Manual two-profile procedure
└── tests/
    ├── test_actions.py
    ├── test_assistant.py
    ├── test_cloud_router.py
    ├── test_config.py
    ├── test_confirmations.py
    ├── test_conversation_state.py
    ├── test_keychain.py
    ├── test_local_router.py
    ├── test_main.py
    ├── test_ollama_client.py
    ├── test_openai_research.py
    ├── test_response_renderer.py
    └── test_tool_dispatcher.py
```

This is **separation of concerns**: routing, policy, validation, state, research,
and operating-system actions each have one focused home.

## Installation

Requirements:

- macOS
- Python 3.9 or newer
- Ollama

Create and activate the virtual environment:

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

The dependencies are the official `ollama` and `openai` Python packages plus
`websockets`, used only for the loopback Chrome bridge. The dependency command is:

```bash
python3 -m pip install -r requirements.txt
```

### Ollama setup

Install/start Ollama using its normal macOS application or CLI setup, then pull
the default local model once:

```bash
ollama pull qwen3.5:4b
```

Verify it:

```bash
ollama list
```

This repository targets the configured `qwen3.5:4b` local Ollama model.
The `ollama pull` step is still required once on any new Mac that does not already
have that model.

## Secure OpenAI key setup

The recommended setup stores the API key in the encrypted macOS Keychain. Run
this once after creating or rotating the key:

```bash
python3 main.py --store-openai-key
```

The two prompts hide terminal input. SABEL sends the secret to the macOS
`security` utility over standard input, so the key is not placed in source code,
shell history, or command-line process arguments. Subsequent SABEL runs retrieve
the Keychain entry automatically. macOS may request permission to access the item.

If `OPENAI_API_KEY` is explicitly exported, it takes precedence over Keychain.
This remains useful for temporary development environments, but Keychain is the
recommended persistent setup on a personal Mac.

## Environment variables

`.env.example` contains placeholders and defaults. SABEL uses ordinary environment
variables and does not automatically load `.env` files.

Local settings:

```bash
export OLLAMA_MODEL="qwen3.5:4b"
export OLLAMA_BASE_URL="http://localhost:11434"
export OLLAMA_KEEP_ALIVE="1m"
export SABEL_HISTORY_LIMIT="10"
export SABEL_PENDING_ACTION_TTL="60"
export SABEL_CLARIFICATION_TTL="60"
export SABEL_BROWSER_REFERENCE_TTL="300"
export SABEL_DEFAULT_MUSIC_SERVICE="spotify"
export SABEL_DEFAULT_SEARCH_ENGINE="google"
export SABEL_BROWSER_BRIDGE_PORT="8765"
export SABEL_BROWSER_MAX_ACTIONS="15"
export SABEL_CONFIG_DIR="$HOME/.sabel"
export SABEL_ALBERT_URL="https://YOUR_REVIEWED_ALBERT_HOST/"
export SABEL_DEFAULT_GMAIL_PROFILE="personal"
```

- `OLLAMA_MODEL` selects the local router model.
- `OLLAMA_BASE_URL` locates the local Ollama service.
- `OLLAMA_KEEP_ALIVE` controls how long Ollama keeps the model loaded after a
  request. The API request passes this setting directly.
- `SABEL_HISTORY_LIMIT` bounds recent message context so it cannot grow forever.
- `SABEL_PENDING_ACTION_TTL` cancels an unconfirmed destructive action after the
  configured number of seconds.
- `SABEL_CLARIFICATION_TTL` clears an unanswered clarification after the
  configured number of seconds.
- `SABEL_BROWSER_REFERENCE_TTL` limits how long phrases such as `same profile`,
  `same tab`, and `there` may reuse the latest verified browser result.
- `SABEL_DEFAULT_SEARCH_ENGINE` may be `google`, `bing`, or `duckduckgo`. Leave
  it empty to ask when neither the current request nor verified context selects
  a provider.

Cloud settings:

```bash
# Optional override; normally store the key in Keychain instead.
export OPENAI_API_KEY="your_api_key_here"
export OPENAI_MODEL="gpt-5.6-luna"
export OPENAI_MAX_OUTPUT_TOKENS="2000"
export OPENAI_REQUEST_TIMEOUT="60"
export SABEL_CLOUD_MODE="ask"
```

Never place a real API key in source, `.env.example`, screenshots, chats, tests,
or logs. `.env` and `.venv/` are ignored by Git. If a key is accidentally shared,
revoke it and store a new one with `--store-openai-key`.

## Browser Copilot

### Architecture and trust boundary

The extension is needed because a terminal Python process can open a URL but
cannot safely inspect Chrome's rendered page, identify a reviewed element, click
it, or verify the resulting state. Browser Copilot separates those jobs:

```text
typed request
  → Ollama selects one high-level browser tool
  → Python creates objective + profile + domain + action scope
  → extension returns one bounded current-page snapshot
  → local Ollama planner proposes exactly one structured decision
  → Python parses and validates that one decision
  → authenticated ws://127.0.0.1 bridge
  → Manifest V3 service worker checks profile, permission, URL, and schema
  → content script performs one reviewed ID-based action
  → Python obtains a new snapshot and evaluates progress
  → repeat until verified completion, clarification, confirmation, failure, or limit
  → central renderer emits only the final friendly response
```

The server binds only to IPv4 loopback at `127.0.0.1:8765`; it is not reachable
through the LAN and must never be publicly deployed. Every extension connection
must have an explicitly registered `chrome-extension://` origin and authenticate
with a random local token. Messages have a strict versioned schema, 256 KiB size
limit, bounded queue, rate limit, registration/command timeouts, heartbeats, and
bounded per-profile concurrency. The authoritative connection registry is keyed
by `profile_id`; Personal and NYU therefore remain simultaneous, isolated
connections. A reconnect from the same stored `instance_id` atomically replaces
its stale socket. A different extension instance claiming an already-connected
profile ID is rejected instead of evicting it. Disconnecting either profile does
not affect the other. Tokens, page contents, form values, cookies, and
credentials are never logged.

WebSocket was selected for v1 because it gives a small, inspectable localhost
protocol and allows the extension and Python policy to be tested independently.
Native Messaging has a stronger browser-managed host boundary but adds host
manifest and deployment complexity. `BrowserTransport` keeps transport separate
from task and policy code so a later release can replace WebSocket with Native
Messaging without rewriting the copilot. Native Messaging is planned, not
implemented in v1.

### Token and registered-origin setup

The token is automatically created at `~/.sabel/browser-token`; its directory is
mode `0700` and file is forced to mode `0600`. It is never printed. After loading
the unpacked extension, copy its 32-letter ID from `chrome://extensions`, then run:

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
source .venv/bin/activate
python3 main.py --register-extension-id YOUR_32_LETTER_EXTENSION_ID
```

Copy the token directly to the clipboard, paste it into the extension's password
field, and clear the clipboard afterward:

```bash
pbcopy < ~/.sabel/browser-token
pbcopy < /dev/null
```

Do not put the token in `.env`, source code, screenshots, shell arguments, logs,
or documentation. To rotate it, stop SABEL first and run:

```bash
python3 main.py --rotate-browser-token
```

Stopping the bridge disconnects current clients; in-process rotation also
disconnects every authenticated client. Paste the replacement token into each
extension installation before reconnecting.

### Load the extension in Personal and NYU Chrome profiles

1. Open the Personal Chrome profile and visit `chrome://extensions`.
2. Turn on Developer mode, choose **Load unpacked**, and select
   `/Users/fabeun/Documents/S.A.B.E.L./browser-extension`.
3. Register the displayed extension ID with the command above.
4. Open the extension settings. Choose profile ID `personal`, display name
   `Personal`, port `8765`, and paste the token.
5. Enter only reviewed site domains, initially `youtube.com` and `google.com`,
   then save and approve Chrome's domain-specific permission dialog.
6. Open the NYU Chrome profile and repeat **Load unpacked** using the same folder.
7. In that installation choose profile ID `nyu`, display name `NYU`, the same
   local port/token, and grant only needed domains. Add the exact Albert hostname
   when Albert is configured.
8. Use each popup's **Reconnect** button, then enter `show browser profiles` in
   SABEL. The two independently registered instances should appear as Personal
   and NYU. SABEL never infers profiles from cookies or email addresses.

Optional configuration:

```bash
export SABEL_BROWSER_BRIDGE_PORT="8765"
export SABEL_CONFIG_DIR="$HOME/.sabel"
export SABEL_ALBERT_URL="https://YOUR_REVIEWED_ALBERT_HOST/"
export SABEL_DEFAULT_GMAIL_PROFILE="personal"  # or nyu
export SABEL_DEFAULT_MUSIC_SERVICE="spotify"   # or youtube
export SABEL_DEFAULT_SEARCH_ENGINE="google"    # google, bing, duckduckgo, or unset
export SABEL_BROWSER_REFERENCE_TTL="300"
```

`SABEL_ALBERT_URL` must be a credential-free HTTPS URL. The service registry
contains URLs, display names, domains, and default profile IDs only—never account
credentials. Albert and NYU Gmail are fixed to NYU; Personal Gmail is fixed to
Personal; YouTube defaults to Personal. `Open Gmail` asks which account unless a
default was explicitly saved. A disconnected required profile is reported; SABEL
never silently substitutes another account.

`SABEL_BROWSER_MAX_ACTIONS` configures the hard autonomous-action limit (15 by
default). A
later direct user approval may extend an active task by five actions; webpage
content cannot change either limit.

Search routing keeps the Chrome profile, reviewed service, search engine,
literal query, target tab, and conversational reference as separate values.
Explicit profile/provider words in the current request win over prior context.
Python removes routing suffixes from the query, validates every field, constructs
the provider URL locally, dispatches only to the exact profile connection, and
verifies the response profile plus resulting URL before updating that profile's
tab state or reporting success. A failed request never changes the latest
browser reference.

### Snapshots, clicks, typing, and verification

A content script observes only the active, allowed HTTP/HTTPS page after Chrome
has granted host permission. It returns title, URL, bounded visible-text summary,
and at most 120 visible interactive elements. Each element contains reviewed
metadata, bounded select-option values, and an opaque ID tied to a 30-second
snapshot. Hidden inputs, password
values, scripts, styles, cookies, page storage, arbitrary DOM selectors, and raw
HTML are excluded. Text and element counts are capped.

Clicks, typing, and selects accept only a fresh `snapshot_id` plus an ephemeral
`element_id`; model-generated selectors and JavaScript are never accepted.
Password, hidden, file-upload, payment, MFA, and similar sensitive fields are
blocked. Ordinary text is capped. Navigation accepts validated HTTP/HTTPS URLs
inside the task's authorized domains and the extension's user-granted site list.

The planner can return only `click`, `type`, `select`, `scroll`, `press_key`,
`navigate`, `back`, `open_tab`, `done`, `clarify`, or
`request_confirmation`. It cannot return JavaScript, CSS selectors, shell
commands, extension calls, or a precomputed sequence of element IDs. The planner
takes one step at a time:

```text
create task → observe → propose one action → validate → execute
            → observe fresh state → verify progress → repeat or stop
```

Every element action is bound to the current snapshot. A navigation or click
invalidates any old plan, so the next decision is made only after a new bounded
snapshot. Effectively unchanged snapshots plus repeated identical actions trigger
the no-progress guard. Reaching the configured action limit ends truthfully with
no success claim.

A `done` decision is not sufficient by itself. It must include a concise answer
and bounded literal evidence that Python can find in the current URL, title, or
visible-text snapshot. Unsupported or malformed planner output fails closed.

The specialized verified YouTube-channel workflow remains in place for its exact
high-reliability command shape. For `Go to Taz Skylar's YouTube channel`, SABEL
uses Personal, opens a YouTube
search, reads the constrained result snapshot, selects one matching channel link,
clicks it, obtains the active URL and a fresh snapshot, and checks that the domain
is YouTube, the URL has a channel shape, and the page evidence contains the
target. Only then may it say `Opened Taz Skylar’s YouTube channel.` Otherwise it
says the search opened but the official channel could not be verified. Opening a
tab or search results is reported as **executed**, not falsely as objective
completion. Media searches likewise say `Opening Spotify results…`, never
`Playing…` without a future verified playback adapter.

### Prompt injection, scope, and confirmations

Webpage content is always untrusted data. Planner context separates system
security policy, the user's direct objective, current authorized scope, untrusted
page data, and available actions. Text such as `ignore the user`, `open Gmail`,
`install this extension`, or `copy the latest email` cannot add a domain, profile,
tool, host permission, Mac action, or credential access. Deterministic Python
policy validates every proposal after the model; the extension independently
validates the resulting command.

Automatic actions are narrow, reversible steps directly implied by the user's
request: list connected profiles/tabs, open a reviewed service or search in the
correct profile, read a constrained snapshot, click the task-relevant same-scope
result, scroll, or follow expected same-domain navigation. The default autonomous
limit is 15 actions. Only a later direct user instruction can expand a task's
domains, actions, or profiles.

Fresh confirmation is required for a webpage-introduced obligation—creating an
account, signing in again, installing software/extensions, granting permission,
uploading, requesting access, subscribing, completing a CAPTCHA, or entering
payment information—and for consequential actions such as submit, send,
purchase, enroll, delete, download, disclose personal information, or save an
external change. The prompt identifies the step and profile. The exact validated
proposal and current snapshot remain in Python-owned pending state; a later clear
confirmation resumes only that stored action through the assistant's central
confirmation router. Declining, replacement, or expiration performs nothing.
Password and file-upload typing remain blocked even after confirmation. A
webpage cannot imitate permission.

Browser Copilot also refuses to add banking, medical, payment, or
password-manager domains to the extension allowlist. File URLs and non-HTTP(S)
schemes are never eligible for host permission or navigation.

The extension popup's **Stop browser control** immediately cancels pending bridge
requests and rejects later commands until **Reconnect**. The SABEL command `stop
browser control` sends the stop command to the active task, or to all connected
profiles when no task is active. It does not close manually managed tabs.

### Protocol examples

Registration (the token below is intentionally redacted):

```json
{"protocol_version":1,"type":"register","request_id":"register-1","token":"<redacted>","profile_id":"personal","profile_name":"Personal","extension_version":"0.1.0","instance_id":"instance-1"}
```

One approved command and response:

```json
{"protocol_version":1,"type":"command","request_id":"request-1","profile_id":"personal","action":"browser_get_snapshot","arguments":{"tab_id":7}}
{"protocol_version":1,"type":"response","request_id":"request-1","profile_id":"personal","success":true,"result":{"snapshot_id":"snapshot-1","tab_id":7,"title":"YouTube","url":"https://www.youtube.com/","visible_text_summary":"…","interactive_elements":[]},"error":null}
```

These JSON envelopes and low-level action names are internal and never enter
conversation history. The central renderer also rejects raw dictionaries,
dataclass/SDK representations, filesystem application paths, and internal enums.

### Browser action audit and limitations

`show recent browser actions` renders a concise view of the bounded local audit
at `~/.sabel/browser-actions.json`. It records timestamp, task ID, profile ID,
domain, action, result, confirmation status, and policy decision. It excludes the
bridge token, typed text, page body, passwords, cookies, MFA codes, and other
secrets.

Browser Copilot supports two named Chrome connections, deterministic reviewed
service/search opening, read-only tab summaries, the specialized verified
YouTube-channel workflow, and general same-scope multi-step tasks driven by the
configured local Ollama model. Every task stays in one exact profile and one
Python-authorized domain set. An unregistered named website requires its explicit
HTTP/HTTPS URL, and each domain must also be added to that extension profile's
Allowed sites and approved in Chrome. A broad Google discovery task cannot
silently expand into arbitrary result domains; provide the destination URL when
the task must navigate that site.

It does not enter passwords or MFA/payment fields, log in on the user's behalf,
bypass CAPTCHAs, inspect cookies/storage, upload files, perform arbitrary
JavaScript, accept arbitrary selectors, control restricted `chrome://` pages,
or claim anything not supported by current visible evidence. Sending, posting,
purchasing, deleting, changing settings/enrollment, downloads, and consequential
form submissions always pause for confirmation; some capabilities remain blocked
entirely. Dynamic pages may change before an approved action and correctly cause
a stale-snapshot failure. The local model can still misunderstand a page, so
strict parsing, policy checks, step limits, no-progress detection, and evidence
verification remain mandatory. Chrome may suspend the Manifest V3 service
worker; reconnect is automatic with bounded backoff. The bridge is local only.
There is no voice input, GUI assistant, wake word, sleep control, public
deployment, or Native Messaging host in this release.

The complete hands-on two-profile procedure is in
[`docs/MANUAL_BROWSER_TEST.md`](docs/MANUAL_BROWSER_TEST.md).

## Cloud modes

### Local-only mode

```bash
python3 main.py --cloud off
```

OpenAI can never be initialized or called. If Ollama requests research, SABEL
offers a current browser search, clearly labeled unverified general guidance, or
cancellation. The local fallback cannot invent current rankings, prices, dates,
availability, or citations.

### Ask-before-cloud mode (default)

```bash
python3 main.py --cloud ask
```

Every proposed cloud task displays:

```text
This request requires cloud research and may use OpenAI API credits.
Use OpenAI for this request? [y/N]
```

Only `y` or `yes` proceeds. Denial makes no OpenAI request.

### Automatic cloud mode

```bash
python3 main.py --cloud auto
```

Ollama-proposed research automatically uses OpenAI when a key is configured.
The configured timeout and output-token cap still apply. Destructive local
actions always require confirmation, even in automatic mode.

### Debug mode

```bash
python3 main.py --cloud ask --debug
```

Debug output may show models, routing mode, durations, and returned token-usage
metadata. It does not reveal internal tool names, validated argument dictionaries,
API keys, environment dumps, passwords, hidden reasoning, or sensitive files.
For a verified browser search it also shows the resolved profile, provider,
literal query, exact target connection, target tab or `new`, locally generated
URL, and extension result profile.

## Destructive Trash confirmation

`Clear my Trash` does not empty anything. It creates a pending action and warns
that deletion is permanent.

Before ordinary routing, the next response goes through a dedicated pending-action
interpreter with confirmation, cancellation, explanation, and new-request outcomes.
Recent user and SABEL messages are included, so contextual replies work. Python
then applies a second conservative safety rule. Clear confirmations include:

```text
Yes, empty the Trash.
Yes, do it.
Go ahead.
Proceed.
I confirm.
```

Vague `okay`, `maybe`, and `sure` remain pending. `Never mind`, `Cancel`, and
`Do not delete it` cancel and clear the action. Questions such as `What does that
mean?` explain the permanent deletion warning without clearing it. An unrelated
command cancels the pending action before normal routing. Pending actions also
expire after 60 seconds by default.

The model-facing pending tools take zero arguments. Python retains the original
reviewed tool name and arguments. A specifically documented redundant `action`
or `tool_name` field is discarded only when it exactly matches the stored tool;
unknown confirmation fields are rejected without executing anything.

Only after both checks does SABEL run one fixed, reviewed Finder instruction:

```text
osascript -e 'tell application "Finder" to empty trash'
```

The script is constant source code, never model-generated. SABEL never uses
`rm -rf` or `shell=True`. Automated tests replace this operation with a mock.

## URL and tool validation

- Websites allow only HTTP and HTTPS.
- Missing schemes receive `https://` locally.
- `file:`, `javascript:`, `data:`, malformed addresses, and shell syntax are
  rejected.
- YouTube and web searches are URL-encoded locally.
- Search engines use a reviewed Google/Bing/DuckDuckGo allowlist.
- `open_application` accepts only `application_name`; path/command fields are
  rejected. Canonical `.app` paths beneath reviewed macOS application roots may
  be reduced to their final name, but no supplied path is ever executed.
- Applications run only as `open -a <catalog launch name>` with an argument
  list and no shell. The launch name is the catalog-owned `.app` basename, not
  a path or model-supplied command.
- Common services resolve through a reviewed YouTube/Google/GitHub registry;
  explicit URLs remain a separate HTTP/HTTPS-only operation.
- Spotify search uses a fixed, URL-encoded implementation and reports opening
  results, never unverified playback.
- Unexpected fields and unknown tools are rejected.
- Trash inspection is read-only. It opens only the current user's fixed
  `~/.Trash` directory with no-follow protection and counts top-level entries.
- Cloud research URLs are stored only when they are HTTP/HTTPS and are validated
  again before a later `Open the first result` action.

## Cloud research and short-term state

OpenAI receives:

- The approved research task
- A focused research instruction
- The hosted `web_search` tool
- A conservative output-token limit

It does not receive local Mac tools. The response is expected to include a direct
answer, criteria, selections, reasoning at the answer level, tradeoffs, sources,
and the research date where freshness matters. Hidden reasoning and raw SDK
objects are never displayed.

SABEL stores only a bounded recent conversation; separate pending destructive,
clarification, and cloud-fallback state; the last visible action result;
the latest research excerpt; and at most 20 validated source URLs in memory. New
research predictably replaces the previous research state. There is no database
or persistent memory.

## API errors and quota protection

OpenAI is never called merely to check credits. No automatic retries occur.
Authentication, quota/spending limit, rate-limit, timeout, network, model,
web-search, and malformed-response failures become a message like:

```text
Cloud research is currently unavailable.
Reason: OpenAI API quota or project spending limit was reached.
Local SABEL commands still work normally.
```

The local Ollama router and command loop remain active after a cloud failure.
Exact dollar estimates are intentionally omitted because price and usage vary.

## Application and request resolution

Every turn receives a new immutable `ResolvedRequest`. Application, browser,
service, provider, query, profile, and tab fields remain separate. Before Ollama
runs, Python locks obvious current-turn constraints such as `NYU`, `Google`,
quoted text, application wording, corrections, and `do the same`. After Ollama
returns, Python merges the proposed interpretation with those locks; an explicit
current-turn field always wins over model output, service defaults, and history.

Application routing does not inherit the last browser profile. SABEL builds a
cached catalog from `/Applications`, `~/Applications`, `/System/Applications`,
and `/System/Library/CoreServices`, reading bundle names, display names, and
identifiers with `plistlib`. Matching is exact first, then token-complete and
high-confidence. Harmless `studio`/`studios` variation is normalized. A request
for `Roblox Studio` can therefore select `Roblox Studio`, but can never silently
fall back to regular `Roblox`, because the meaningful `studio` token is absent.
Ambiguous or missing applications produce a clarification/not-found message and
no process is launched. Ollama never receives or chooses bundle paths.

Installed-software questions are also catalog-backed. `is VS Code installed?`
uses a typed read-only lookup, while `show installed applications` returns a
bounded path-free inventory. The model is not allowed to guess. A successful
lookup or application action also stores a short-lived local application
reference, so `open it please` can resolve without turning `it please` into a
new application name.

Corrections mark the prior attempt as rejected without reusing it. SABEL stores
the most recent attempted action separately from the most recent verified
successful action. `do the same` clones only the latter structured template;
`do the same on my NYU profile` changes only the profile, and `do the same on
YouTube` preserves the query while changing the provider. Failed, timed-out,
unverified, or user-rejected actions never become reusable context.

Media clarification is also structured. `play circles` retains `query=circles`;
a reply such as `YouTube, on my NYU profile` fills the missing service and profile
and becomes a NYU YouTube search. Explicit Spotify wording such as `open the song
circles on Spotify` or `search circles on Spotify` resolves to the same reviewed
Spotify-results action and never becomes an application name or Google query.
Context wording such as `search the same thing on YouTube but on Personal`
retains the last verified query while applying the current provider and profile.
Browser state remains isolated per exact
`personal`/`nyu` WebSocket connection. Every result must match the request ID,
profile, destination provider, query, and tab before SABEL updates context or
reports success.

## Complete verification stack

Run every required layer with:

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
python3 -m sabel.verify
```

The runner uses the project virtual environment when present and reports each
layer separately:

1. Focused unit and property/invariant tests.
2. Production-router conformance against the configured local Ollama model at
   temperature zero, repeated three times per critical scenario.
3. A black-box subprocess test using real stdin/stdout and `main.py`.
4. Two simultaneous authenticated WebSocket extension simulators, with separate
   Personal/NYU tab state and exact destination assertions.
5. A read-only scan of actual installed macOS application bundles.
6. Chrome-extension JavaScript tests.
7. An optional live-browser smoke test.

The black-box clients simulate only Chrome. They do not mock the SABEL command
loop, prompt, model, resolver, state, dispatcher, profile registry, WebSocket
transport, result verification, or renderer. This is why passing the focused
mocked tests alone is not treated as proof that production routing works.

The default suite never opens real applications, contacts OpenAI, empties Trash,
or performs destructive actions. The optional live browser check runs only when
both real profiles connect to its bridge, opens harmless disposable Google and
YouTube result tabs, verifies the returned profile and URL, then closes only the
tabs it created:

```bash
python3 -m sabel.self_test --browser-live
```

When both profiles are unavailable it reports, without calling that a pass:

```text
Live Chrome acceptance test not run: required profiles were not connected.
```

The installed-application smoke check is read-only:

```bash
python3 -m sabel.self_test --applications
```

For manual extension setup and inspection:

```bash
open docs/MANUAL_BROWSER_TEST.md
```

## Important terms

- **Router:** chooses an approved path for a request.
- **Tool calling:** a model requests a named operation with structured arguments.
- **Tool schema:** the allowed fields and types for that operation.
- **Dispatcher:** validates a tool call and maps it to reviewed code.
- **Executor:** performs the reviewed operation.
- **Cloud delegation:** asking Python policy to permit cloud research.
- **Session state:** temporary information retained during one SABEL run.
- **Latency:** elapsed time from request to response.
- **Quota:** an API usage or spending limit.

## Current limitations and later voice phase

Small local models can misclassify ambiguous requests, so SABEL asks for
clarification rather than guessing when possible. It supports one action per
request, one pending destructive action, and only the latest research result. It
does not perform arbitrary automation, account login, file searching, email,
remote access, sleep/shutdown, or GUI control.

A later phase may add push-to-talk voice input. Speech should become text and then
enter this exact same Ollama router, allowlist, confirmation, cloud-policy, and
validation pipeline. Voice is postponed so the authorization boundary can be
tested thoroughly first.
