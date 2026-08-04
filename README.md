# SABEL — Phase 2

SABEL is a typed macOS command assistant written in Python 3.9 or newer. Phase 1 provided a
deterministic command-line interface and two safe local actions. Phase 2 adds
flexible natural-language interpretation through the OpenAI Responses API while
keeping validation and macOS control in local Python code.

SABEL can currently:

- Open HTTP or HTTPS websites in the default browser.
- Open installed macOS applications by name.
- Understand exact local commands and flexible typed requests.
- Show help and exit without contacting OpenAI.

SABEL does not have arbitrary Terminal access. It does not include voice input,
speech output, a GUI, background listening, memory, file operations, web search,
email, shutdown control, computer use, the Agents SDK, or the Realtime API.

## Architecture

```text
S.A.B.E.L./
├── .gitignore
├── README.md
├── main.py                    CLI entry point and command loop
├── requirements.txt          The one runtime dependency
├── sabel/
│   ├── __init__.py
│   ├── actions.py             Existing safe macOS actions
│   ├── ai_router.py           Responses API tool-calling lifecycle
│   ├── config.py              Environment-based settings
│   ├── parser.py              Deterministic local parsing
│   ├── prompts.py             SABEL's model instructions
│   └── tool_executor.py       Tool allowlist and local validation boundary
└── tests/
    ├── __init__.py
    ├── test_actions.py
    ├── test_ai_router.py
    ├── test_config.py
    ├── test_main.py
    ├── test_parser.py
    └── test_tool_executor.py
```

This separation of concerns means each module has one focused responsibility.
The OpenAI SDK is absent from `actions.py`, and `main.py` does not contain
API-specific error handling.

## Request flow

Local built-ins and exact Phase 1 commands take the short path:

```text
help / quit / exit / exact open command
        ↓
deterministic local parser
        ↓
local response or validated action
        ↓
macOS (only for an open action)
```

Flexible requests take the AI path:

```text
Typed request
    ↓
local command loop
    ↓
OpenAI Responses API
    ↓
zero or one structured function call
    ↓
local tool-name and JSON-argument checks
    ↓
existing local validation
    ↓
existing action executor
    ↓
macOS open command
    ↓
structured tool result with the original call_id
    ↓
one OpenAI continuation
    ↓
final concise text shown by the CLI
```

For each flexible request, SABEL permits at most one local action, one tool-result
continuation, and one final model response. Parallel tool calls are disabled. If
the second response asks for another action, SABEL refuses it rather than
continuing a loop.

## Deterministic parsing, AI interpretation, and execution

**Deterministic local parsing** follows fixed rules. `help` always maps to help,
and `open app Notes` always maps to the local application action. It is fast,
predictable, and needs no network request.

**AI-based intent interpretation** handles flexible wording such as “Can you
start Notes?” The model identifies the intended approved tool and proposes
structured arguments.

**Local execution** happens only after local code checks the proposed tool name
and arguments. The model never calls `subprocess.run()` and cannot directly
control macOS.

The security rule is:

```text
The model proposes. Local code validates. Local code executes.
```

## Installation on macOS

Create a virtual environment from the repository directory:

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
python3 -m venv .venv
```

A **virtual environment** is an isolated Python installation for one project. It
keeps SABEL's dependency from changing packages used by other projects.

Activate it:

```bash
source .venv/bin/activate
```

Install the official OpenAI Python SDK:

```bash
python3 -m pip install -r requirements.txt
```

The SDK is SABEL's only runtime dependency. A dependency is code maintained
outside this repository that the project needs to run.

## API key setup

Create an API key in your OpenAI API project, then set it only in the current
Terminal session:

```bash
export OPENAI_API_KEY="your_api_key_here"
```

An **API key** is a secret credential used to authenticate API requests. Never
commit it, paste it into Python code, or include it in screenshots or logs. This
environment-variable setting disappears when that Terminal session closes.

An **environment variable** is a named value provided to a program by its
operating environment. To set the key for future zsh sessions, you may manually
add the export line to `~/.zshrc`, then open a new Terminal. SABEL never modifies
that file automatically.

The optional model setting is:

```bash
export SABEL_OPENAI_MODEL="gpt-5.6-luna"
```

When it is absent, SABEL uses `gpt-5.6-luna`. The setting is defined in one place,
`sabel/config.py`. The API account must have access to whichever model you name.

## Run SABEL

With the virtual environment active:

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
python3 main.py
```

Examples:

```text
help
open website youtube.com
Could you pull up youtube.com for me?
Launch Safari.
I need Visual Studio Code.
Please open the NYU website.
Can you start Notes?
exit
```

`help`, `quit`, `exit`, and the exact Phase 1 open commands stay local. Flexible
wording uses the API.

If the key or SDK is missing, SABEL explains how to fix it and keeps accepting
local commands instead of crashing.

## Run the tests

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
python3 -B -m unittest discover -v
```

`-B` prevents Python from writing bytecode caches. Tests use dependency injection
and mocks: fake OpenAI clients and fake action functions are supplied to the code.
This verifies request fields, tool calls, validation, errors, and `call_id`
preservation without an API key, internet access, API spending, browser windows,
or application launches.

## The two model-visible tools

SABEL exposes exactly two strict function tools:

```text
open_website(url: string)
open_application(application_name: string)
```

Each JSON Schema requires its single field and sets `additionalProperties` to
false. Local code repeats the important checks because model output must still be
treated as untrusted input.

Website validation adds `https://` when appropriate and rejects unsupported
schemes including `file:`, `javascript:`, `ftp:`, and `data:`. Application names
remain individual subprocess arguments even when they contain spaces.

The only operating-system calls remain equivalent to:

```python
subprocess.run(["open", validated_url], ...)
subprocess.run(["open", "-a", validated_application_name], ...)
```

SABEL never uses `shell=True` and never executes a model-generated command string.

## Function-call lifecycle

1. SABEL sends one request containing the typed text, model instructions, and the
   two tool definitions.
2. It inspects the response for ordinary text or one function call.
3. It verifies that the function name is on the local allowlist.
4. It parses the arguments as JSON and requires the exact expected fields.
5. It performs local URL or application-name validation.
6. It executes the approved local action.
7. It serializes success or failure into a structured tool output.
8. It returns that output with the original `call_id`.
9. It makes one final Responses API request and displays the resulting text.

The **function-call ID** (`call_id`) is the model-generated identifier connecting
one requested function with its result. Preserving it tells the model which call
the local output answers.

SABEL uses low reasoning effort because this is a small intent-routing task. SDK
automatic retries are disabled: hidden repeat requests could add latency and
cost, while the command loop already lets the user try again deliberately.

## Privacy: what is sent to OpenAI

For a flexible request, SABEL sends only:

- The text you typed.
- SABEL's concise model instructions.
- The schemas and descriptions of the two approved tools.
- On continuation, the first model output and the local tool success/failure
  result associated with its `call_id`.

SABEL does not send:

- Your API key as prompt content.
- Environment variables.
- Passwords.
- Unrelated files or computer data.
- Directory listings or application inventories.
- A general shell, file, web-search, or computer-use tool.

There is no conversation memory in Phase 2. Each typed request is routed
independently.

## Troubleshooting

### `OPENAI_API_KEY is not set`

Set it in the same Terminal where you run SABEL:

```bash
export OPENAI_API_KEY="your_api_key_here"
```

### `The OpenAI package is not installed`

Activate the environment and install the requirements:

```bash
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

### Authentication failed

The key may be invalid, revoked, or associated with the wrong API project. Create
or select a valid key and export it again. ChatGPT subscriptions and API billing
are separate.

### Insufficient credits or rate limit

Check the API project's billing, spending limit, and rate limits. A rate limit
restricts how frequently or heavily an API can be used in a period. SABEL does
not automatically make repeated retry requests.

### Network error or timeout

Check the internet connection and try again. A timeout means the operation did
not finish within SABEL's configured waiting period.

### Model unavailable or request rejected

Confirm that `SABEL_OPENAI_MODEL` names a model available to the API project, or
remove the variable to restore the default.

### Website or application does not open

SABEL will show the local validation or macOS error. Confirm that the website is
HTTP/HTTPS and that the application is installed under the requested name.

## API usage factors

API usage depends on:

- The selected model's current pricing.
- Input tokens from the request, instructions, and tool schemas.
- Output and reasoning tokens produced by the model.
- Whether a request needs only ordinary text or also the second tool-result call.
- Request length and response length.

A **token** is a small unit of text processed by a model. **Latency** is the time
between sending a request and receiving its response. Low reasoning and concise
instructions help reduce both token usage and latency, but exact costs should be
checked against current OpenAI API pricing.

## Vocabulary

- **API:** an application programming interface—a defined way for programs to
  communicate. The relevant endpoint is the Responses API, the network operation
  used to submit a request and receive a response.
- **SDK:** a software development kit. The official OpenAI Python SDK is a library
  that provides the `OpenAI` client and typed methods for the API.
- **Client:** the Python object used to send requests to an API endpoint.
- **Request / response:** data sent to an API and the data returned by it.
- **Model:** the system that interprets SABEL's flexible typed request. It proposes
  an intent; it is not the local executor.
- **Prompt:** text supplied to a model. **Model instructions** are the dedicated
  rules describing SABEL's role, available tools, and boundaries.
- **Intent:** the action a user means, even when their exact wording varies.
- **Tool / function calling:** a tool is an operation offered by local code;
  function calling is the API mechanism through which the model requests it.
- **Tool call / tool output:** the structured model request and the local result
  returned for that request.
- **JSON:** a text format for structured values. An **argument** is one named input
  supplied to a function, such as `application_name`.
- **JSON Schema:** rules describing permitted JSON. A **strict schema** requires
  exact adherence rather than best-effort formatting.
- **Validation:** checking data before trusting or using it.
- **Allowlist:** the complete set of explicitly permitted tool names. Everything
  else is rejected.
- **Authentication:** proving an API request is authorized, using the API key.
- **Rate limit:** a cap on API usage over time.
- **Timeout:** the maximum time SABEL waits for one API operation.
- **Side effect:** an external change, such as opening a browser window.
- **Dependency injection:** supplying a dependency—such as a client or action
  function—to code instead of forcing the code to construct the real one.
- **Mock:** a controlled test replacement that records how it was used.
- **Fallback:** the behavior used when the preferred path is unavailable. Exact
  commands remain a local fallback when AI interpretation is unavailable.
- **Separation of concerns:** assigning configuration, interpretation, validation,
  execution, and interaction to different focused modules.

## ChatGPT, Codex, the API, and SABEL's model

- **ChatGPT** is OpenAI's conversational product for end users.
- **Codex** is the coding agent being used to build and review this repository.
- **The OpenAI API** is the developer service SABEL calls from Python using an API
  key and usage-based API billing.
- **SABEL's configured model** is the particular API model selected by
  `SABEL_OPENAI_MODEL`; it interprets commands inside SABEL's restricted workflow.

Using ChatGPT does not automatically configure API access or API billing, and
SABEL is its own local program rather than a ChatGPT conversation.

## Security boundaries and current limitations

The model receives no general shell tool and cannot install software, delete
files, change security settings, use `sudo`, search files, send messages, control
windows, browse the web, or operate the computer directly. An API response alone
never authorizes an action; the local allowlist and validation must approve it.

Natural-language interpretation can still misunderstand ambiguous requests. Ask
clearly for one website or one application. SABEL performs no multi-step routines
and remembers nothing from previous commands.

## Future Phase 3

Phase 3 may add push-to-talk speech input. It is not implemented here. The speech
text should eventually enter the same parser, AI router, validation, and approved
action boundary built in Phases 1 and 2.
