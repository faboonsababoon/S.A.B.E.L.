"""SABEL hybrid typed assistant entry point."""

import argparse

from sabel.assistant import SabelAssistant
from sabel.config import load_settings


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run the SABEL macOS assistant.")
    parser.add_argument(
        "--cloud",
        choices=("off", "ask", "auto"),
        help="Override SABEL_CLOUD_MODE for this run.",
    )
    parser.add_argument("--debug", action="store_true", help="Show safe timing and routing details.")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
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
