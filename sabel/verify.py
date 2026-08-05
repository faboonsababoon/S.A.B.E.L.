"""One-command SABEL verification stack."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SuiteResult:
    label: str
    status: str
    detail: str = ""


def _python() -> str:
    project_python = ROOT / ".venv" / "bin" / "python"
    return str(project_python) if project_python.is_file() else sys.executable


def _run(label: str, command: list[str], *, cwd: Path = ROOT, timeout: int = 600):
    print(f"\n=== {label} ===")
    environment = dict(os.environ)
    environment.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/sabel-verify-pycache")
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + (error.stderr or "")
        print(output)
        return 124, output + "\nTimed out."
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if output:
        print(output)
    return completed.returncode, output


def main() -> int:
    python = _python()
    results: list[SuiteResult] = []

    code, output = _run(
        "Unit tests",
        [python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
    )
    count = re.search(r"Ran\s+(\d+)\s+tests?", output)
    results.append(
        SuiteResult(
            "Unit tests",
            "PASS" if code == 0 else "FAIL",
            f"{count.group(1)} tests" if count else "",
        )
    )

    code, output = _run(
        "Real Ollama router conformance",
        [python, "-m", "tests.conformance.router_conformance"],
        timeout=900,
    )
    match = re.search(r"runs=(\d+/\d+).*consistency=(\d+%)", output)
    detail = f"{match.group(1)} runs, {match.group(2)} consistent" if match else ""
    results.append(
        SuiteResult("Router conformance", "PASS" if code == 0 else "FAIL", detail)
    )

    code, output = _run(
        "Black-box CLI + dual-profile WebSocket acceptance",
        [python, "-m", "tests.acceptance.run_cli_scenarios"],
        timeout=900,
    )
    cli_pass = code == 0 and "CLI_BLACK_BOX=PASS" in output
    websocket_pass = code == 0 and "DUAL_PROFILE_WEBSOCKET=PASS" in output
    results.append(SuiteResult("CLI black-box acceptance", "PASS" if cli_pass else "FAIL"))
    results.append(
        SuiteResult(
            "Dual-profile WebSocket acceptance",
            "PASS" if websocket_pass else "FAIL",
        )
    )

    code, output = _run(
        "Actual macOS application catalog",
        [python, "-m", "tests.integration.application_catalog_check"],
    )
    results.append(
        SuiteResult(
            "Application catalog integration",
            "PASS" if code == 0 and "APPLICATION_CATALOG_INTEGRATION=PASS" in output else "FAIL",
        )
    )

    code, output = _run(
        "Extension JavaScript tests", ["npm", "test"], cwd=ROOT / "browser-extension"
    )
    results.append(SuiteResult("Extension tests", "PASS" if code == 0 else "FAIL"))

    code, output = _run(
        "Optional safe live browser acceptance",
        [python, "-m", "sabel.self_test", "--browser-live"],
        timeout=60,
    )
    if "Live browser acceptance: PASS" in output and code == 0:
        live = SuiteResult("Live browser acceptance", "PASS")
    elif "Live Chrome acceptance test not run:" in output and code == 0:
        reason = next(
            (line for line in output.splitlines() if line.startswith("Live Chrome acceptance test not run:")),
            "required profiles were not connected",
        )
        live = SuiteResult("Live browser acceptance", "SKIPPED", reason)
    else:
        live = SuiteResult("Live browser acceptance", "FAIL")
    results.append(live)

    required = [result for result in results if result.label != "Live browser acceptance"]
    overall = "PASS" if all(result.status == "PASS" for result in required) and live.status != "FAIL" else "FAIL"

    print("\nSABEL Verification Report\n")
    for result in results:
        suffix = f" — {result.detail}" if result.detail else ""
        print(f"{result.label}: {result.status}{suffix}")
    print(f"\nOverall: {overall}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
