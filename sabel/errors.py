"""Project-specific errors translated into readable CLI messages."""


class OllamaUnavailableError(RuntimeError):
    """The local Ollama service, SDK, or configured model is unavailable."""


class CloudResearchError(RuntimeError):
    """OpenAI research could not be completed."""

