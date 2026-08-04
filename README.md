# SABEL — Hybrid Local/Cloud Phase

SABEL is a typed macOS assistant. A local Ollama language model interprets every
normal request and selects from a small, explicit tool allowlist. Ordinary Mac
actions stay local. Difficult, current, multi-source research may be delegated to
OpenAI only after Python applies the configured cloud policy.

Voice, wake words, a GUI, remote access, sleep control, and background listening
are intentionally postponed.

## The central security rule

```text
Models propose.
Python validates.
Reviewed local code executes.
```

Neither Ollama nor OpenAI receives a shell, generated Python, generated
AppleScript, or direct `subprocess.run()` access. Unknown tools, extra arguments,
unsupported URL schemes, and confirmation bypasses are rejected.

## Hybrid architecture

```text
Raw typed request
        ↓
Pending destructive-action check
        ↓
Local Ollama router (native tool calling)
        ↓
Python tool allowlist and validation
        ↓
┌──────────────────────────┬──────────────────────────────────┐
│ Approved local operation │ delegate_to_openai proposal      │
│                          │                                  │
│ Website / app / search   │ Python cloud mode: off/ask/auto │
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
Search Google for the NYU academic calendar.
What can you do?
Show your status.
Clear my Trash.
```

Ollama performs local **inference**—processing the sentence to identify its
intent. It can request these local tools:

- `open_website`
- `open_application`
- `open_youtube_search`
- `open_web_search`
- `empty_trash`
- `show_capabilities`
- `show_status`
- `exit_assistant`
- `open_research_source`
- `delegate_to_openai` (a proposal, not authority to spend credits)

The wording is not matched against a large collection of phrases. The tool
schemas describe capabilities; the local model interprets normal language.

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
│   ├── cloud_router.py           off/ask/auto policy
│   ├── config.py                 Environment and CLI settings
│   ├── confirmations.py          Destructive confirmation rules
│   ├── conversation_state.py     Bounded in-memory state
│   ├── errors.py                 Project errors
│   ├── local_router.py           Ollama routing
│   ├── ollama_client.py          Reusable native tool-call client
│   ├── openai_research.py        Responses API hosted web research
│   ├── tool_dispatcher.py        Local allowlist and validation
│   └── tool_schemas.py           Ollama-native tool schemas
└── tests/
    ├── test_actions.py
    ├── test_assistant.py
    ├── test_cloud_router.py
    ├── test_config.py
    ├── test_conversation_state.py
    ├── test_local_router.py
    ├── test_ollama_client.py
    ├── test_openai_research.py
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

The dependencies are the official `ollama` and `openai` Python packages.

### Ollama setup

Install/start Ollama using its normal macOS application or CLI setup, then pull
the default local model once:

```bash
ollama pull qwen3:1.7b
```

Verify it:

```bash
ollama list
```

This repository was verified with Ollama CLI 0.32.5 and `qwen3:1.7b` installed.
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
export OLLAMA_MODEL="qwen3:1.7b"
export OLLAMA_BASE_URL="http://localhost:11434"
export OLLAMA_KEEP_ALIVE="1m"
export SABEL_HISTORY_LIMIT="10"
export SABEL_PENDING_ACTION_TTL="60"
```

- `OLLAMA_MODEL` selects the local router model.
- `OLLAMA_BASE_URL` locates the local Ollama service.
- `OLLAMA_KEEP_ALIVE` controls how long Ollama keeps the model loaded after a
  request. The API request passes this setting directly.
- `SABEL_HISTORY_LIMIT` bounds recent message context so it cannot grow forever.
- `SABEL_PENDING_ACTION_TTL` cancels an unconfirmed destructive action after the
  configured number of seconds.

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

## Cloud modes

### Local-only mode

```bash
python3 main.py --cloud off
```

OpenAI can never be called. If Ollama requests research, SABEL explains that cloud
processing is disabled and offers a normal browser search instead.

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

Debug output may show models, selected tool, validated arguments, routing mode,
durations, and returned token-usage metadata. It never shows API keys, environment
dumps, passwords, hidden reasoning, or sensitive files.

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
- Application names remain one subprocess argument, including spaces.
- Unexpected fields and unknown tools are rejected.
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

SABEL stores only a bounded recent conversation, the latest research excerpt,
and at most 20 validated source URLs in memory. New research predictably replaces
the previous research state. There is no database or persistent memory.

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

## Run the tests

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
source .venv/bin/activate
python3 -B -m unittest discover -v
```

The suite uses **mocks**—controlled substitutes—for Ollama, OpenAI, macOS `open`,
and Trash emptying. It contacts neither provider, spends no credits, opens no real
windows, and deletes nothing.

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
