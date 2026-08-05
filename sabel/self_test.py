"""Explicit, safe host smoke tests for SABEL."""

import argparse
import time
import uuid

from sabel.application_catalog import ApplicationCatalog
from sabel.browser_routing import build_search_url, verify_search_url
from sabel.browser_runtime import BrowserBridgeRuntime
from sabel.config import load_settings


LIVE_SKIP = "Live Chrome acceptance test not run: required profiles were not connected."


def _arguments():
    parser = argparse.ArgumentParser(description="Run explicit SABEL host smoke tests.")
    parser.add_argument(
        "--browser-live",
        action="store_true",
        help="Use both connected real extensions for disposable, harmless search tabs.",
    )
    parser.add_argument(
        "--applications",
        action="store_true",
        help="Scan and report installed applications without opening them.",
    )
    return parser.parse_args()


def application_check() -> bool:
    catalog = ApplicationCatalog()
    catalog.refresh()
    print("Installed application smoke check (no applications opened)")
    for name in ("Codex", "Roblox", "Roblox Studio"):
        match = catalog.resolve(name)
        if match.application:
            print(
                f"- {name}: {match.application.display_name} "
                f"({match.application.bundle_identifier or 'bundle ID unavailable'})"
            )
        else:
            print(f"- {name}: not installed")
    studio = catalog.resolve("Roblox Studios")
    if studio.application and studio.application.display_name.casefold() == "roblox":
        print("Application smoke check: FAIL — Roblox substituted for Roblox Studio.")
        return False
    print("Application smoke check: PASS")
    return True


def live_browser_check() -> str:
    settings = load_settings(cloud_mode="off", credential_loader=lambda: None)
    runtime = BrowserBridgeRuntime(settings)
    created: list[tuple[str, int]] = []
    try:
        try:
            runtime.start()
        except Exception as error:
            print(f"{LIVE_SKIP} Bridge unavailable: {error}")
            return "skipped"
        deadline = time.monotonic() + 6.0
        profiles = set()
        while time.monotonic() < deadline:
            profiles = {profile.profile_id for profile in runtime.transport.connected_profiles()}
            if {"personal", "nyu"}.issubset(profiles):
                break
            time.sleep(0.2)
        if not {"personal", "nyu"}.issubset(profiles):
            print(LIVE_SKIP)
            return "skipped"

        nonce = uuid.uuid4().hex[:8]
        checks = (
            ("personal", "google", f"SABEL harmless live test {nonce} personal"),
            ("personal", "youtube", f"SABEL harmless live test {nonce} personal"),
            ("nyu", "google", f"SABEL harmless live test {nonce} nyu"),
            ("nyu", "youtube", f"SABEL harmless live test {nonce} nyu"),
        )
        for profile, provider, query in checks:
            url = build_search_url(provider, query)
            result = runtime._run(
                runtime.transport.send_request(
                    profile, "browser_open_tab", {"url": url, "active": False}
                ),
                timeout=20.0,
            )
            tab_id = result.result.get("tab_id")
            result_url = result.result.get("url")
            if (
                not result.success
                or result.profile_id != profile
                or not isinstance(tab_id, int)
                or not verify_search_url(result_url, provider, query)
            ):
                print(
                    f"Live browser acceptance: FAIL — {provider} in {profile} did not verify."
                )
                return "failed"
            created.append((profile, tab_id))
            print(f"- verified disposable {provider.title()} tab in {profile}")
        print("Live browser acceptance: PASS")
        return "passed"
    finally:
        for profile, tab_id in created:
            try:
                runtime._run(
                    runtime.transport.send_request(
                        profile, "browser_close_tab", {"tab_id": tab_id}
                    ),
                    timeout=10.0,
                )
            except Exception:
                pass
        runtime.stop()


def main() -> int:
    args = _arguments()
    if not args.browser_live and not args.applications:
        print("Choose --applications or --browser-live.")
        return 2
    okay = True
    if args.applications:
        okay = application_check() and okay
    if args.browser_live:
        outcome = live_browser_check()
        okay = outcome != "failed" and okay
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main())
