"""Real qwen3:1.7b production-router conformance and consistency suite."""

from dataclasses import dataclass
import subprocess

from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.local_router import LocalRouter, RouterResultType
from sabel.ollama_client import OllamaClient
from sabel.request_models import Intent


@dataclass(frozen=True)
class Scenario:
    text: str
    intent: Intent
    application: str | None = None
    service: str | None = None
    provider: str | None = None
    query: str | None = None
    profile: str | None = None


SCENARIOS = (
    Scenario("open roblox studios", Intent.OPEN_APPLICATION, application="Roblox Studio"),
    Scenario(
        'wrong application. Open "roblox studios" not roblox',
        Intent.OPEN_APPLICATION,
        application="Roblox Studio",
    ),
    Scenario("open codex", Intent.OPEN_APPLICATION, application="codex"),
    Scenario(
        "no, open codex. it is an application on my laptop",
        Intent.OPEN_APPLICATION,
        application="codex",
    ),
    Scenario(
        "search black moss candles on YouTube in my NYU profile",
        Intent.SEARCH_YOUTUBE,
        service="youtube",
        provider="youtube",
        query="black moss candles",
        profile="nyu",
    ),
    Scenario(
        "search fried chickpeas on Google in my Personal profile",
        Intent.SEARCH_WEB,
        service="google",
        provider="google",
        query="fried chickpeas",
        profile="personal",
    ),
    Scenario(
        "go to YouTube on my NYU profile",
        Intent.OPEN_SERVICE,
        service="youtube",
        profile="nyu",
    ),
    Scenario(
        "open Circles on YouTube on my NYU profile",
        Intent.SEARCH_YOUTUBE,
        service="youtube",
        provider="youtube",
        query="Circles",
        profile="nyu",
    ),
    Scenario(
        "open circles on spotify",
        Intent.OPEN_SPOTIFY_SEARCH,
        service="spotify",
        query="circles",
    ),
    Scenario(
        "search circles on spotify",
        Intent.OPEN_SPOTIFY_SEARCH,
        service="spotify",
        query="circles",
    ),
    Scenario(
        "is vscode installed",
        Intent.CHECK_APPLICATION,
        application="vscode",
    ),
    Scenario(
        "what applications are installed",
        Intent.LIST_APPLICATIONS,
    ),
)


def _assert_scenario(scenario: Scenario, result) -> tuple:
    assert result.result_type == RouterResultType.TOOL_CALLS, result
    assert len(result.tool_calls) == 1, result
    request = result.resolved_request
    assert request is not None, result
    assert request.intent == scenario.intent, (scenario, request)
    if scenario.application is not None:
        assert request.application_name is not None
        assert request.application_name.casefold() == scenario.application.casefold()
        assert request.profile_id is None
    if scenario.service is not None:
        assert request.service == scenario.service
    if scenario.provider is not None:
        assert request.search_engine == scenario.provider
    if scenario.query is not None:
        assert request.query == scenario.query
        folded = request.query.casefold()
        assert "profile" not in folded and " on youtube" not in folded and " on google" not in folded
    if scenario.profile is not None:
        assert request.profile_id == scenario.profile
    return (
        request.intent.value,
        request.application_name,
        request.service,
        request.search_engine,
        request.query,
        request.profile_id,
    )


def main() -> int:
    settings = Settings(cloud_mode="off")
    installed = subprocess.run(
        ["ollama", "list"], capture_output=True, text=True, check=False, timeout=15
    )
    if installed.returncode != 0:
        raise RuntimeError(f"ollama list failed: {installed.stderr.strip()}")
    if settings.ollama_model not in installed.stdout:
        raise RuntimeError(
            f'The required local model "{settings.ollama_model}" is not installed.'
        )

    repetitions = 3
    total = len(SCENARIOS) * repetitions
    observed: dict[str, list[tuple]] = {scenario.text: [] for scenario in SCENARIOS}
    for repetition in range(repetitions):
        for scenario in SCENARIOS:
            state = ConversationState(settings.history_limit)
            router = LocalRouter(OllamaClient(settings), state)
            result = router.route(scenario.text)
            observed[scenario.text].append(_assert_scenario(scenario, result))
            print(
                f"PASS {repetition + 1}/{repetitions}: {scenario.text} -> "
                f"{observed[scenario.text][-1]}"
            )

    inconsistent = {
        text: values for text, values in observed.items() if len(set(values)) != 1
    }
    if inconsistent:
        raise AssertionError(f"Temperature-zero routing was inconsistent: {inconsistent}")
    print(
        f"ROUTER_CONFORMANCE=PASS runs={total}/{total} "
        f"scenarios={len(SCENARIOS)} repetitions={repetitions} consistency=100%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
