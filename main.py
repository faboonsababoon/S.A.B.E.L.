"""SABEL hybrid typed assistant entry point."""

import argparse
import getpass

from sabel.assistant import AssistantResult, AssistantResultType, SabelAssistant
from sabel.browser_runtime import BrowserBridgeRuntime
from sabel.browser_security import BrowserSecurityError
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
    utilities = parser.add_mutually_exclusive_group()
    utilities.add_argument(
        "--store-openai-key",
        action="store_true",
        help="Securely add or replace SABEL's OpenAI key in macOS Keychain.",
    )
    utilities.add_argument(
        "--register-extension-id",
        metavar="EXTENSION_ID",
        help="Add one unpacked Chrome extension ID to the local origin allowlist and exit.",
    )
    utilities.add_argument(
        "--rotate-browser-token",
        action="store_true",
        help="Rotate the local browser bridge token without printing it and exit.",
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


def render_result(result: AssistantResult) -> str:
    """Render only final typed assistant outcomes, never router internals."""
    if not isinstance(result.result_type, AssistantResultType):
        return "SABEL: I could not safely render that result."
    return f"SABEL: {result.message}"


def main() -> None:
    args = parse_arguments()
    if args.store_openai_key:
        store_key_interactively()
        return
    utility_only = bool(args.register_extension_id or args.rotate_browser_token)
    settings = load_settings(
        cloud_mode=args.cloud,
        debug=args.debug,
        credential_loader=(lambda: None) if utility_only else None,
    )
    browser_runtime = BrowserBridgeRuntime(settings)
    if args.register_extension_id:
        try:
            origin = browser_runtime.register_extension_id(args.register_extension_id)
        except BrowserSecurityError as error:
            print(f"Extension registration failed: {error}")
            return
        print(f"Registered extension origin: {origin}")
        print("Restart SABEL if the browser bridge is already running.")
        return
    if args.rotate_browser_token:
        try:
            browser_runtime.rotate_token()
        except (BrowserSecurityError, OSError, RuntimeError) as error:
            print(f"Browser token rotation failed: {error}")
            return
        print("Browser authentication token rotated. Existing extension settings must be updated.")
        return

    bridge_error = None
    try:
        browser_runtime.start()
    except (BrowserSecurityError, OSError, RuntimeError, ValueError) as error:
        bridge_error = str(error)
    assistant = SabelAssistant(settings, browser_runtime=browser_runtime)

    print("SABEL is online.")
    print(f"Local model: {settings.ollama_model}")
    print(f"Cloud mode: {settings.cloud_mode}\n")
    if bridge_error:
        print(f"Browser bridge: Unavailable — {bridge_error}\n")
    else:
        print(
            f"Browser bridge: Listening on 127.0.0.1:{settings.browser_bridge_port}\n"
        )

    try:
        while True:
            try:
                typed_text = input("SABEL > ")
            except (EOFError, KeyboardInterrupt):
                print("\nSABEL is going offline.")
                break

            result = assistant.handle(typed_text, approval_callback=input)
            print(render_result(result))
            if result.should_exit:
                break
    finally:
        browser_runtime.stop()


if __name__ == "__main__":
    main()
