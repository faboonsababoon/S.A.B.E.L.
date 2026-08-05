"""Safe actual-macOS application catalog check; never launches an app."""

from sabel.actions import ActionResult
from sabel.application_catalog import ApplicationCatalog
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.request_resolution import RequestResolver, extract_locked_constraints
from sabel.tool_dispatcher import ToolDispatcher


def main() -> int:
    catalog = ApplicationCatalog(refresh_seconds=999)
    applications = catalog.refresh()
    names = ("Codex", "Roblox", "Roblox Studio")
    found = {}
    print("macOS application catalog")
    for name in names:
        match = catalog.resolve(name)
        found[name] = match.application
        if match.application:
            app = match.application
            print(
                f"- {name}: INSTALLED — display={app.display_name}; "
                f"bundle_id={app.bundle_identifier or 'unknown'}"
            )
        else:
            print(f"- {name}: NOT INSTALLED")

    studio = catalog.resolve("Roblox Studios")
    if studio.application and studio.application.display_name.casefold() == "roblox":
        raise AssertionError("Roblox was incorrectly substituted for Roblox Studio")
    if found["Roblox Studio"] is not None:
        assert studio.application is not None
        assert studio.application.display_name == found["Roblox Studio"].display_name
    else:
        assert studio.application is None

    state = ConversationState(10)
    resolver = RequestResolver(state, catalog)
    for text, expected in (("open codex", "Codex"), ("open roblox studios", "Roblox Studio")):
        decision = resolver.resolve(text, locked=extract_locked_constraints(text))
        assert decision is not None and decision.request is not None
        assert decision.request.profile_id is None
        installed = found[expected]
        if installed is not None:
            assert decision.request.application_name == installed.display_name

    opened = []

    def dry_open(name: str) -> ActionResult:
        opened.append(name)
        return ActionResult(True, "dry run")

    dispatcher = ToolDispatcher(
        Settings(),
        state,
        lambda: True,
        handlers={"open_application": dry_open},
        application_catalog=catalog,
    )
    if found["Roblox Studio"] is not None:
        result = dispatcher.dispatch(
            "open_application", {"application_name": "Roblox Studios"}, "open roblox studios"
        )
        assert result.action_success is True
        assert result.validated_arguments == {
            "application_name": found["Roblox Studio"].display_name
        }
        assert opened == [found["Roblox Studio"].bundle_name]
    print(f"Scanned {len(applications)} application bundles; no applications were opened.")
    print("APPLICATION_CATALOG_INTEGRATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
