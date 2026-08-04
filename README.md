# SABEL — Phase 1

SABEL Phase 1 is a small, local macOS command assistant. It accepts a limited set
of typed commands, opens HTTP/HTTPS websites in the default browser, opens named
applications with macOS's standard `open` command, shows help, and exits cleanly.

This phase intentionally does **not** include voice recognition, wake-word
detection, an OpenAI API integration, a graphical interface, background
listening, Wake-on-LAN, sleep or shutdown control, long-term memory, or arbitrary
Terminal command execution.

## Project structure

```text
S.A.B.E.L./
├── README.md                 Project guide
├── main.py                   CLI entry point and command loop
├── sabel/
│   ├── __init__.py           Marks the SABEL Python package
│   ├── parser.py             Converts text into structured commands
│   └── actions.py            Validates targets and runs approved actions
└── tests/
    ├── __init__.py           Marks the test package
    ├── test_parser.py        Parser unit tests
    └── test_actions.py       Validation and action-boundary unit tests
```

A **module** is a single Python file. A **package** groups related modules in a
directory containing `__init__.py`. The **entry point** is the file used to start
the program—in this project, `main.py`.

## Setup and running SABEL

Requirements: macOS and Python 3. No external packages are needed.

In Terminal, move into this project directory and run:

```bash
cd /Users/fabeun/Documents/S.A.B.E.L.
python3 main.py
```

Run every test from the same directory with:

```bash
python3 -m unittest discover -v
```

## Commands and expected behavior

```text
help
```

Lists every supported command with examples.

```text
open website youtube.com
```

Adds `https://` and opens `https://youtube.com` in the default browser.

```text
open site https://www.nyu.edu
```

Opens the existing HTTPS address unchanged.

```text
open application Visual Studio Code
```

Asks macOS to open an installed application with that full name.

```text
quit
```

Prints a closing message and ends the command loop. `exit` does the same thing.
Unknown or empty commands produce readable guidance instead of crashing SABEL.

## How a request flows

A **CLI** (command-line interface) is a text-based way to interact with a program.
Its **command loop** repeatedly reads a command, handles it, and prompts again.

```text
Typed text
→ parser
→ structured command
→ validation
→ action executor
→ macOS
→ result message
```

The **parser** recognizes SABEL's small command grammar. It returns a **structured
command** with predictable named fields. Here that object is a **dataclass**, a
Python class intended mainly to store related data, such as:

```python
Command(action="open_website", target="youtube.com")
```

The **action executor** is the layer that performs an approved operation. Before
doing so, **input validation** checks that the target obeys SABEL's rules. For a
website, validation allows only HTTP or HTTPS and requires a usable host name.

The executor uses `subprocess.run()` to start the macOS `open` program as a
**subprocess**—a separate process launched by Python. A **process return code** is
the number that process reports when it ends: zero normally means success and a
nonzero value means failure. Opening an app or browser is a **side effect**, which
means it changes something outside the Python function itself.

## Tests

A **unit test** checks a small piece of behavior independently. Parser and URL
tests have no operating-system side effects. Executor tests use **mocking**, which
replaces `subprocess.run()` with a controllable test object, so the tests can
verify the exact safe arguments without repeatedly opening real windows.

## Security

SABEL maps input only to explicitly approved actions. It passes each argument to
`subprocess.run()` as a separate list item and never enables `shell=True`. It
does not treat the user's text as a Terminal command. As a result, a user can
select a supported action and its target, but cannot ask Phase 1 to execute an
arbitrary shell-command string.

## Next Phase (not implemented)

A later phase could let an OpenAI model supplement or replace the simple parser.
The model could translate natural language into a structured tool call such as
`open_website(address="youtube.com")`. SABEL should still validate that structure
and send it through the same small approved action layer. The model would decide
which offered tool to request; it would not receive unrestricted Terminal access.
