"""SABEL typed command-line entry point."""

from typing import Callable, Tuple

from sabel.ai_router import RouterResult, route_request
from sabel.actions import open_application, open_website
from sabel.parser import Command, parse_command


WELCOME = 'SABEL is online.\nType "help" to see available commands.\n'
UNKNOWN_MESSAGE = (
    'I do not understand that command yet. Type "help" for available commands.'
)
HELP_TEXT = """Available commands:
  help                              Show this help
  open website youtube.com          Open a website in the default browser
  open site https://www.nyu.edu     Open an HTTP or HTTPS address
  open app Safari                   Open a macOS application
  open application Visual Studio Code
                                    Application names may contain spaces
  Could you open YouTube for me?     Use AI interpretation for flexible wording
  quit or exit                      Close SABEL"""


def handle_command(command: Command) -> Tuple[str, bool]:
    """Execute one structured command and return (message, should_quit)."""
    if command.action == "help":
        return HELP_TEXT, False
    if command.action == "quit":
        return "SABEL is going offline.", True
    if command.action == "empty":
        return 'Please type a command, or type "help" for available commands.', False
    if command.action == "open_website":
        result = open_website(command.target or "")
        return result.message, False
    if command.action == "open_application":
        result = open_application(command.target or "")
        return result.message, False
    return UNKNOWN_MESSAGE, False


AIRouter = Callable[[str], RouterResult]


def handle_text(typed_text: str, ai_router: AIRouter = route_request) -> Tuple[str, bool]:
    """Keep built-ins local and send only unknown natural language to the AI router."""
    command = parse_command(typed_text)
    if command.action != "unknown":
        return handle_command(command)
    return ai_router(command.target or typed_text).message, False


def main() -> None:
    """Run the interactive command loop until the user quits."""
    print(WELCOME)
    while True:
        try:
            typed_text = input("SABEL > ")
        except (EOFError, KeyboardInterrupt):
            print("\nSABEL is going offline.")
            break

        message, should_quit = handle_text(typed_text)
        print(message)
        if should_quit:
            break


if __name__ == "__main__":
    main()
