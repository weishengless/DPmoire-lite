import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = REPO_ROOT / "docs" / "superpowers" / "plans" / "2026-07-11-code-review-followup"
EXPECTED_AGENTS = {
    "AGENTS.md",
    "docs/superpowers/plans/2026-07-11-code-review-followup/AGENTS.md",
    "src/dpmoire_lite/AGENTS.md",
    "tests/AGENTS.md",
}
WORKER_PROMPT = PLAN_DIR / "WORKER-EXECUTION-PROMPT.md"
ORCHESTRATOR_PROMPT = PLAN_DIR / "ORCHESTRATOR-EXECUTION-PROMPT.md"
LEGACY_PROMPT = PLAN_DIR / "LOW-REASONING-EXECUTION-PROMPT.md"
PLAN_WORKER = REPO_ROOT / ".codex" / "agents" / "plan-worker.toml"
ROADMAP = PLAN_DIR / "README.md"
PLAN_06R = PLAN_DIR / "06r-route-correction-execution-governance.md"
ROUTE_DESIGN = (
    REPO_ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-07-12-code-review-followup-route-correction-and-low-reasoning-execution-design.md"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing governance file: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8-sig")


def test_exactly_four_scoped_agents_files_exist():
    tracked = subprocess.run(
        ["git", "ls-files", "*AGENTS.md"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    found = {line for line in tracked.stdout.splitlines() if line}
    found.update(
        relative
        for relative in EXPECTED_AGENTS
        if (REPO_ROOT / relative).is_file()
    )

    assert found == EXPECTED_AGENTS


def test_agents_files_respect_line_caps_and_contain_no_concrete_runtime_state():
    required_markers = {
        "AGENTS.md": (
            "authoritative sources",
            "verified python",
            "one green execution unit",
            "exact file list",
            "potcar",
            "narrower agents.md",
        ),
        "docs/superpowers/plans/2026-07-11-code-review-followup/AGENTS.md": (
            "implementation-status.local.md",
            "execution capsule",
            "inspect -> red -> green -> verify -> commit",
            "contradiction",
            "stop after",
        ),
        "src/dpmoire_lite/AGENTS.md": (
            "module ownership",
            "cross-module invariants",
            "authoritative notes",
            "parsers do not publish",
        ),
        "tests/AGENTS.md": (
            "tdd",
            "focused",
            "affected",
            "full suite",
            "fixture provenance",
            "platform skip",
            "external ase",
            "scoped `require_escalated`",
            "non-sandbox",
            "unique, absolute",
            "$env:temp\\dpmoire-pytest-<unit>-<gate>-<nonce>",
            "confirm the basetemp path does not exist",
            "report a blocker",
            "do not change permissions",
            "/pytest-of-*/",
        ),
    }
    forbidden_runtime_patterns = (
        r"\b[0-9a-f]{7,40}\b",
        r"\b20\d{2}-\d{2}-\d{2}\b",
        r"\b\d+\s+(?:passed|skipped)\b",
        r"\bactive\s+(?:plan|execution unit)\s*:\s*\S+",
        r"\bplan[\s_-]*\d+[a-z]?[\s_-]+task[\s_-]*\d+\b",
    )

    for relative, markers in required_markers.items():
        text = _read(REPO_ROOT / relative)
        assert len(text.splitlines()) <= 120, relative
        folded = text.casefold()
        for marker in markers:
            assert marker in folded, f"{relative} is missing {marker!r}"
        for pattern in forbidden_runtime_patterns:
            assert re.search(pattern, text, flags=re.IGNORECASE) is None, (
                f"{relative} contains concrete runtime state matching {pattern!r}"
            )


def test_orchestrator_prompt_is_bounded_and_owns_capsule_review_and_commit():
    text = _read(ORCHESTRATOR_PROMPT)
    assert len(text.splitlines()) <= 120
    folded = text.casefold()
    for marker in (
        "gpt-5.6 sol",
        "max reasoning",
        "standard speed",
        "persistent orchestrator thread",
        "execution capsule",
        "one write-capable thread",
        "review the complete unstaged diff",
        "git diff --cached --check",
        "advance active state",
        "stop after the checkpoint",
    ):
        assert marker in folded


def test_worker_prompt_is_short_and_denies_commit_and_state_advance():
    text = _read(WORKER_PROMPT)
    assert len(text.splitlines()) <= 80
    folded = text.casefold()
    for marker in (
        "gpt-5.6 luna",
        "max reasoning",
        "standard speed",
        "persistent worker thread",
        "orchestrator-issued execution capsule",
        "read active state only to confirm agreement",
        "red-green-refactor",
        "leave all changes unstaged",
        "do not edit plans, specifications, or active state",
        "do not stage, commit, push, or start another unit",
        "do not delegate or spawn another agent",
    ):
        assert marker in folded


def test_two_thread_prompts_define_a_serial_shared_worktree_handoff():
    orchestrator = _read(ORCHESTRATOR_PROMPT).casefold()
    worker = _read(WORKER_PROMPT).casefold()

    for text in (orchestrator, worker):
        assert "shared worktree" in text
        assert "serial handoff" in text
        assert "two persistent threads" in text
        assert "plan_worker" not in text
        assert "plan-worker.toml" not in text
        assert ".codex/agents" not in text


def test_legacy_low_reasoning_prompt_is_absent():
    assert not LEGACY_PROMPT.exists()
    roadmap = _read(ROADMAP)
    assert "LOW-REASONING-EXECUTION-PROMPT.md" not in roadmap
    assert "ORCHESTRATOR-EXECUTION-PROMPT.md" in roadmap
    assert "WORKER-EXECUTION-PROMPT.md" in roadmap


def test_native_custom_agent_config_is_absent_until_runtime_support_is_proven():
    assert not PLAN_WORKER.exists()


def test_governance_authorities_define_two_persistent_threads():
    for path in (PLAN_06R, ROUTE_DESIGN):
        folded = _read(path).casefold()
        for marker in (
            "two persistent threads",
            "sol",
            "luna",
            "serial handoff",
            "orchestrator-execution-prompt.md",
            "worker-execution-prompt.md",
        ):
            assert marker in folded, f"{path.name} is missing {marker!r}"
        assert "plan_worker" not in folded
        assert "plan-worker.toml" not in folded


def test_versioned_status_headers_do_not_name_a_dynamic_active_plan():
    expected_status = {
        ROADMAP: "Status: approved roadmap under implementation.",
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-07-11-code-review-followup-decomposition-design.md": (
            "Status: approved; implementation is under way through the approved roadmap."
        ),
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-07-12-mlff-full-dedup-legacy-collection-design.md": (
            "Status: approved and incorporated into authoritative notes and plans."
        ),
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-07-12-code-review-followup-route-correction-and-low-reasoning-execution-design.md": (
            "Status: approved; Plan 06R is under implementation."
        ),
    }

    for path, status in expected_status.items():
        text = _read(path)
        header = "\n".join(text.splitlines()[:12])
        assert status in header
        assert re.search(
            r"active\s+(?:plan|execution unit)\s*:\s*(?:plan|\d)",
            header,
            flags=re.IGNORECASE,
        ) is None
        for stale in (
            "not yet written",
            "awaiting review",
            "awaits review",
            "before execution",
        ):
            assert stale not in header.casefold()

    roadmap = _read(ROADMAP)
    historical_count = roadmap.index("93 passed and 5 skipped")
    assert "historical" in roadmap[
        max(0, historical_count - 160) : historical_count + 160
    ].casefold()
