"""SABEL hybrid typed assistant entry point."""

import argparse
import getpass

from sabel.assistant import SabelAssistant
from sabel.config import load_settings
from sabel.keychain import KeychainError, store_openai_api_key


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run the SABEL macOS assistant.")
    parser.add_argument(
        "--cloud",
        choices=("off", "ask", "auto"),
        help="Override SABEL_CLOUD_MODE for this run.",
    )
    parser.add_argument("--debug", action="store_true", help="Show safe timing and routing details.")
    parser.add_argument(
        "--store-openai-key",
        action="store_true",
        help="Securely add or replace SABEL's OpenAI key in macOS Keychain.",
    )
    return parser.parse_args()


def store_key_interactively() -> None:
    """Collect the key without terminal echo, save it, and immediately exit."""
    first = getpass.getpass("New OpenAI API key (input hidden): ")
    second = getpass.getpass("Confirm OpenAI API key: ")
    if first != second:
        print("The keys did not match. Nothing was saved.")
        return
    try:
        store_openai_api_key(first)
    except KeychainError as error:
        print(f"Keychain error: {error}")
        return
    print("OpenAI API key saved securely in macOS Keychain.")


def main() -> None:
    args = parse_arguments()
    if args.store_openai_key:
        store_key_interactively()
        return
    settings = load_settings(cloud_mode=args.cloud, debug=args.debug)
    assistant = SabelAssistant(settings)

    print("SABEL is online.")
    print(f"Local model: {settings.ollama_model}")
    print(f"Cloud mode: {settings.cloud_mode}\n")

    while True:
        try:
            typed_text = input("SABEL > ")
        except (EOFError, KeyboardInterrupt):
            print("\nSABEL is going offline.")
            break

        result = assistant.handle(typed_text, approval_callback=input)
        print(f"SABEL: {result.message}")
        if result.should_exit:
            break


if __name__ == "__main__":
    main()
