"""Typer CLI for the `task` tool.

Phase 1: stubs only. Each command prints a "not implemented yet" message and
exits cleanly. Flags, help text, and structure are final; bodies are not.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from rich.table import Table

from taskbundle import bundle as bundle_mod
from taskbundle import container as container_mod
from taskbundle import db
from taskbundle import masker as masker_mod
from taskbundle import runner as runner_mod
from taskbundle import solver as solver_mod
from taskbundle.dataset import DatasetError, find_row

app = typer.Typer(
    name="task",
    help="Package SWE-bench-style coding tasks into Docker containers and run/score LLM solutions.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


class Solver(str, Enum):
    """Available solver backends."""

    noop = "noop"
    gold = "gold"
    command = "command"
    anthropic = "anthropic"


class Mask(str, Enum):
    """Available test-hiding strategies."""

    file = "file"
    function = "function"


def _not_implemented(command: str) -> None:
    """Print the standard stub message and exit 0."""
    console.print(f"[yellow]{command} not implemented yet[/yellow]")
    raise typer.Exit(code=0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.command()
def init(
    bundle: Optional[Path] = typer.Option(
        None, "--bundle", help="Output bundle directory (with --from-dataset) or task bundle directory."
    ),
    repo: Optional[str] = typer.Option(None, "--repo", help="Git repo URL."),
    commit: Optional[str] = typer.Option(None, "--commit", help="Base commit SHA."),
    image: Optional[str] = typer.Option(None, "--image", help="Prebuilt docker image reference."),
    from_dataset: Optional[str] = typer.Option(
        None,
        "--from-dataset",
        help="SWE-Bench Pro instance_id to scaffold a bundle from.",
    ),
) -> None:
    """Create or scaffold a task bundle."""
    if from_dataset is not None:
        _init_from_dataset(from_dataset, bundle)
        return
    if bundle is None:
        console.print("[red]init: --bundle is required (or use --from-dataset).[/red]")
        raise typer.Exit(code=2)
    _init_from_bundle(Path(bundle))


def _init_from_dataset(instance_id: str, bundle: Optional[Path]) -> None:
    """Build a task bundle from a SWE-Bench Pro dataset row."""
    out_dir = Path(bundle) if bundle else bundle_mod.default_bundle_dir(instance_id)
    command_id = uuid.uuid4().hex
    started_at = _now_iso()
    args_json = json.dumps(
        {"from_dataset": instance_id, "bundle": str(out_dir)}, sort_keys=True
    )

    db.init_db()
    try:
        console.print(f"Fetching dataset row for [cyan]{instance_id}[/cyan] …")
        item = find_row(instance_id)
        summary = bundle_mod.build_bundle(item, out_dir)
    except (DatasetError, ValueError, KeyError) as e:
        db.record_command(
            command_id=command_id,
            command="init",
            args_json=args_json,
            bundle=str(out_dir),
            status="error",
            message=str(e)[:500],
            started_at=started_at,
            finished_at=_now_iso(),
        )
        console.print(f"[red]init failed:[/red] {e}")
        raise typer.Exit(code=1)

    db.record_command(
        command_id=command_id,
        command="init",
        args_json=args_json,
        bundle=summary["bundle_dir"],
        status="success",
        message=f"bundle built: {summary['n_f2p']} F2P / {summary['n_p2p']} P2P",
        started_at=started_at,
        finished_at=_now_iso(),
    )
    console.print(f"[green]✓ bundle written to[/green] [bold]{summary['bundle_dir']}[/bold]")
    console.print(f"  image: {summary['image']}")
    console.print(
        f"  fail_to_pass: {summary['n_f2p']}  pass_to_pass: {summary['n_p2p']}"
        f"  test_files: {summary['n_test_files']}"
    )


def _instance_commit_from_id(instance_id: str) -> Optional[str]:
    """Extract the 40-hex instance commit embedded in a SWE-Bench Pro id."""
    m = re.search(r"-([0-9a-f]{40})-v", instance_id)
    return m.group(1) if m else None


def _discover_repo_path(c: "container_mod.ContainerHandle", base_commit: str) -> tuple[Optional[str], str]:
    """Find the repo root in the container whose history contains base_commit.

    Returns (repo_path or None, method-description).
    """
    candidates: list[tuple[str, str]] = []
    for fast in ("/testbed", "/app"):
        rc, _, _ = c.exec(f"test -d {fast}/.git")
        if rc == 0:
            candidates.append((fast, f"fast-path {fast}/.git"))
    if not candidates:
        rc, out, _ = c.exec(
            "find / -maxdepth 5 -type d -name .git 2>/dev/null | head -20"
        )
        for gitdir in out.split():
            root = gitdir.rsplit("/.git", 1)[0]
            if root:
                candidates.append((root, "find / -name .git"))

    for root, method in candidates:
        rc, _, _ = c.exec(f"git -C {root} cat-file -e {base_commit}^{{commit}}")
        if rc == 0:
            return root, method
    return None, "no candidate contained base_commit"


def _init_from_bundle(bundle_dir: Path) -> None:
    """Container-side init: verify the image/env, discover the repo, record metadata."""
    command_id = uuid.uuid4().hex
    started_at = _now_iso()
    args_json = json.dumps({"bundle": str(bundle_dir)}, sort_keys=True)
    db.init_db()

    def fail(msg: str, code: int = 1):
        db.record_command(
            command_id=command_id, command="init", args_json=args_json,
            bundle=str(bundle_dir), status="error", message=msg[:500],
            started_at=started_at, finished_at=_now_iso(),
        )
        console.print(f"[red]init failed:[/red] {msg}")
        raise typer.Exit(code=code)

    task_path = bundle_dir / "task.json"
    if not task_path.exists():
        fail(f"task.json not found in bundle: {task_path}")
    task = json.loads(task_path.read_text(encoding="utf-8"))

    image = task["image"]
    base_commit = task["base_commit"]
    instance_id = task["instance_id"]
    selected_test_files = task.get("test", {}).get("selected_test_files", [])
    scored = _load_scored(bundle_dir)
    instance_commit = _instance_commit_from_id(instance_id)

    if not container_mod.image_exists(image):
        fail(f"image not present locally: {image}")

    console.print(f"Initializing bundle [cyan]{instance_id}[/cyan]")
    console.print(f"  image: {image}")
    console.print("  network: default (trusted setup; --network none is for validate/run)")

    try:
        with container_mod.container_session(image) as c:
            # (a) discover repo path
            repo_path, method = _discover_repo_path(c, base_commit)
            if not repo_path:
                fail(f"could not locate repo containing base_commit {base_commit} ({method})")
            console.print(f"[green]✓[/green] repo path: [bold]{repo_path}[/bold]  (via {method})")

            # (b) normalize to clean baseline
            for cmd in (
                f"git -C {repo_path} reset --hard {base_commit}",
                f"git -C {repo_path} clean -fd",
                f"git -C {repo_path} checkout {base_commit}",
            ):
                rc, out, err = c.exec(cmd)
                if rc != 0:
                    fail(f"baseline normalization failed: {cmd}\n{err.strip()}")
            console.print(f"[green]✓[/green] baseline normalized to {base_commit[:12]}")

            # (c) pytest collect-only on each selected file
            collected: list[str] = []
            for tf in selected_test_files:
                rc, out, err = c.exec(
                    f"python -m pytest --collect-only -q {tf}", workdir=repo_path,
                    timeout=600,
                )
                if rc != 0:
                    fail(f"pytest --collect-only failed (rc={rc}) for {tf}\n"
                         f"{(out + err).strip()[-1500:]}")
                collected += _parse_collected(out, tf)
            console.print(f"[green]✓[/green] pytest collected {len(collected)} item(s)")
            missing = [n for n in scored if n not in collected]
            if missing:
                fail(f"scored node IDs missing from collection: {missing}")
            console.print(f"[green]✓[/green] all {len(scored)} scored node IDs present in collection")

            # (d) git apply --check for gold + test patch
            apply_results = {}
            for fname in ("gold_patch.diff", "test_patch.diff"):
                host = bundle_dir / fname
                c.cp_to(str(host), f"/tmp/{fname}")
                rc, out, err = c.exec(
                    f"git -C {repo_path} apply --check /tmp/{fname}"
                )
                apply_results[fname] = (rc == 0, err.strip())
                tag = "[green]PASS[/green]" if rc == 0 else "[red]FAIL[/red]"
                console.print(f"  git apply --check {fname}: {tag}")
                if rc != 0:
                    console.print(f"    {err.strip()}")

            # (e) diagnostics
            tf0 = selected_test_files[0] if selected_test_files else ""
            rc, out, _ = c.exec(
                f"grep -nE '^(diff --git|\\+\\+\\+).*{re.escape(tf0)}' /tmp/test_patch.diff"
            )
            tp_touches = rc == 0
            tp_headers = out.strip()
            ic_present = None
            if instance_commit:
                rc, _, _ = c.exec(
                    f"git -C {repo_path} cat-file -e {instance_commit}^{{commit}}"
                )
                ic_present = (rc == 0)

            # (f) record metadata into task.json (preserve key order)
            digest = container_mod.image_digest(image)
            task["repo_path_in_container"] = repo_path
            task["image_digest"] = digest
            task_path.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    except container_mod.ContainerError as e:
        fail(f"container error: {e}")

    # Report diagnostics
    console.print("\n[bold]Diagnostics[/bold]")
    console.print(f"  test_patch touches {tf0}? "
                  f"{'[green]yes[/green]' if tp_touches else '[yellow]no[/yellow]'}")
    if tp_headers:
        for line in tp_headers.splitlines():
            console.print(f"    {line}")
    if instance_commit:
        present = "[green]yes[/green]" if ic_present else "[yellow]no[/yellow]"
        console.print(f"  instance commit {instance_commit[:12]} present in history? {present}")
    else:
        console.print("  instance commit: unknown")
    console.print(f"  image_digest: {digest}")

    gold_ok = apply_results.get("gold_patch.diff", (False, ""))[0]
    test_ok = apply_results.get("test_patch.diff", (False, ""))[0]
    msg = (f"repo={repo_path}; collected={len(collected)}; "
           f"gold_apply={gold_ok}; test_apply={test_ok}")
    db.record_command(
        command_id=command_id, command="init", args_json=args_json,
        bundle=str(bundle_dir), status="success", message=msg[:500],
        started_at=started_at, finished_at=_now_iso(),
    )
    console.print(f"\n[green]✓ init complete[/green] — metadata written to {task_path}")


def _load_scored(bundle_dir: Path) -> list[str]:
    f2p = json.loads((bundle_dir / "tests" / "fail_to_pass.json").read_text())
    p2p = json.loads((bundle_dir / "tests" / "pass_to_pass.json").read_text())
    return list(f2p) + list(p2p)


def _parse_collected(output: str, test_file: str) -> list[str]:
    """Extract pytest node IDs from `--collect-only -q` output.

    Handles both newer pytest (flat node IDs, one per line) and pytest 6.x,
    which prints an indented tree of <Module>/<Class>/<Function ...> entries.
    Tree entries are reconstructed against the known `test_file` path.
    """
    lines = output.splitlines()
    flat = [
        ln.strip() for ln in lines
        if "::" in ln and not ln.strip().startswith(("=", "<"))
    ]
    if flat:
        return flat

    ids: list[str] = []
    # (indent, name) stack of Class components beneath the current Module.
    class_stack: list[tuple[int, str]] = []
    node_re = re.compile(r"^(\s*)<(\w+)\s+(.+?)>\s*$")
    in_module = False
    for ln in lines:
        m = node_re.match(ln)
        if not m:
            continue
        indent, kind, name = len(m.group(1)), m.group(2), m.group(3)
        if kind in ("Module", "Package"):
            in_module = kind == "Module" or in_module
            if kind == "Module":
                class_stack = []
            continue
        if kind in ("Class", "Instance", "UnitTestCase"):
            while class_stack and class_stack[-1][0] >= indent:
                class_stack.pop()
            if kind != "Instance":  # pytest's <Instance> node has no node-id segment
                class_stack.append((indent, name))
            continue
        if kind in ("Function", "TestCaseFunction"):
            while class_stack and class_stack[-1][0] >= indent:
                class_stack.pop()
            parts = [c[1] for c in class_stack] + [name]
            ids.append(f"{test_file}::" + "::".join(parts))
    return ids


@app.command()
def validate(
    bundle: Path = typer.Option(..., "--bundle", help="Path to a task bundle directory."),
    json_path: Optional[Path] = typer.Option(
        None, "--json", help="Write a machine-readable result to this path."
    ),
    keep_container: bool = typer.Option(
        False,
        "--keep-container/--rm-container",
        help="Keep the container after validation instead of removing it.",
    ),
) -> None:
    """Validate a task bundle (baseline + gold patch reproduce the expected results)."""
    _validate_bundle(Path(bundle), json_path, keep_container)


def _clean_baseline(c, repo_path: str, base_commit: str) -> None:
    """Reset the working tree to a clean baseline at base_commit."""
    for cmd in (
        f"git -C {repo_path} reset --hard {base_commit}",
        f"git -C {repo_path} clean -fd",
        f"git -C {repo_path} checkout {base_commit}",
    ):
        rc, _, err = c.exec(cmd)
        if rc != 0:
            raise runner_mod.RunnerError(f"baseline reset failed: {cmd}\n{err.strip()}")


def _check_expectations(by_node, f2p, p2p, f2p_should_pass):
    """Return (met: bool, violations: list).

    P2P must always be passed. F2P must be passed iff f2p_should_pass (Phase C
    with gold applied); otherwise F2P must be non-passed (Phase B baseline).
    """
    violations = []
    for node in f2p:
        actual = by_node.get(node, "missing")
        ok = (actual == "passed") if f2p_should_pass else (actual != "passed")
        if not ok:
            violations.append({"node": node, "bucket": "F2P",
                               "expected": "passed" if f2p_should_pass else "not-passed",
                               "actual": actual})
    for node in p2p:
        actual = by_node.get(node, "missing")
        if actual != "passed":
            violations.append({"node": node, "bucket": "P2P",
                               "expected": "passed", "actual": actual})
    return (len(violations) == 0, violations)


def _phase_table(title, by_node, f2p, p2p, f2p_should_pass):
    table = Table(title=title)
    table.add_column("node")
    table.add_column("bucket")
    table.add_column("expected")
    table.add_column("actual")
    table.add_column("OK")
    for node in f2p + p2p:
        bucket = "F2P" if node in f2p else "P2P"
        if bucket == "F2P":
            expected = "passed" if f2p_should_pass else "not-passed"
        else:
            expected = "passed"
        actual = by_node.get(node, "missing")
        ok = (actual == "passed") if expected == "passed" else (actual != "passed")
        leaf = node.split("::", 1)[-1]
        table.add_row(leaf, bucket, expected, actual,
                      "[green]OK[/green]" if ok else "[red]✗[/red]")
    return table


def _validate_bundle(bundle_dir: Path, json_path: Optional[Path], keep_container: bool) -> None:
    command_id = uuid.uuid4().hex
    started_at = _now_iso()
    args_json = json.dumps(
        {"bundle": str(bundle_dir), "json": str(json_path) if json_path else None,
         "keep_container": keep_container}, sort_keys=True
    )
    db.init_db()

    def record(status: str, message: str) -> None:
        db.record_command(
            command_id=command_id, command="validate", args_json=args_json,
            bundle=str(bundle_dir), status=status, message=message[:500],
            started_at=started_at, finished_at=_now_iso(),
        )

    def error_exit(msg: str):
        record("error", msg)
        console.print(f"[red]validate errored:[/red] {msg}")
        raise typer.Exit(code=2)

    task_path = bundle_dir / "task.json"
    if not task_path.exists():
        error_exit(f"task.json not found: {task_path}")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    image = task["image"]
    base_commit = task["base_commit"]
    repo_path = task.get("repo_path_in_container") or "/app"
    instance_id = task["instance_id"]
    selected_test_files = task.get("test", {}).get("selected_test_files", [])

    f2p = json.loads((bundle_dir / "tests" / "fail_to_pass.json").read_text())
    p2p = json.loads((bundle_dir / "tests" / "pass_to_pass.json").read_text())
    scored = list(f2p) + list(p2p)

    try:
        instance_commit = runner_mod.instance_commit_from_id(instance_id)
    except runner_mod.RunnerError as e:
        error_exit(str(e))

    if not container_mod.image_exists(image):
        error_exit(f"image not present locally: {image}")

    console.print(f"Validating [cyan]{instance_id}[/cyan]")
    console.print(f"  image: {image}")
    console.print(f"  network: none  |  repo: {repo_path}  |  instance_commit: {instance_commit[:12]}")

    result = {
        "instance_id": instance_id, "image": image, "base_commit": base_commit,
        "instance_commit": instance_commit,
        "phase_baseline": None, "phase_gold": None,
        "verdict": None, "checked_at": _now_iso(),
    }
    kept_name = None
    try:
        cm = container_mod.container_session(image, network="none")
        c = cm.__enter__()
        try:
            kept_name = c.name
            # PHASE B: baseline + staged tests, no gold
            _clean_baseline(c, repo_path, base_commit)
            runner_mod.stage_tests(c, repo_path, instance_commit, selected_test_files)
            res_b = runner_mod.run_pytest(c, repo_path, scored)
            if res_b.get("error"):
                error_exit(f"phase B pytest could not run: {res_b['error']}\n"
                           f"stdout:\n{res_b['stdout_tail']}\nstderr:\n{res_b['stderr_tail']}")
            met_b, viol_b = _check_expectations(res_b["by_node"], f2p, p2p, f2p_should_pass=False)
            result["phase_baseline"] = {
                "by_node": res_b["by_node"], "expectations_met": met_b, "violations": viol_b,
            }

            # PHASE C: baseline + gold patch + staged tests
            _clean_baseline(c, repo_path, base_commit)
            c.cp_to(str(bundle_dir / "gold_patch.diff"), "/tmp/gold_patch.diff")
            rc, out, err = c.exec(f"git -C {repo_path} apply /tmp/gold_patch.diff")
            if rc != 0:
                error_exit(f"gold patch failed to apply in phase C:\n{err.strip()}")
            runner_mod.stage_tests(c, repo_path, instance_commit, selected_test_files)
            res_c = runner_mod.run_pytest(c, repo_path, scored)
            if res_c.get("error"):
                error_exit(f"phase C pytest could not run: {res_c['error']}\n"
                           f"stdout:\n{res_c['stdout_tail']}\nstderr:\n{res_c['stderr_tail']}")
            met_c, viol_c = _check_expectations(res_c["by_node"], f2p, p2p, f2p_should_pass=True)
            result["phase_gold"] = {
                "by_node": res_c["by_node"], "expectations_met": met_c, "violations": viol_c,
            }
        finally:
            if keep_container:
                console.print(f"[yellow]--keep-container:[/yellow] left container "
                              f"[bold]{kept_name}[/bold] running (docker rm -f {kept_name} to remove).")
            else:
                cm.__exit__(None, None, None)
    except container_mod.ContainerError as e:
        error_exit(f"container error: {e}")
    except runner_mod.RunnerError as e:
        error_exit(str(e))

    verdict = "VALID" if (result["phase_baseline"]["expectations_met"]
                          and result["phase_gold"]["expectations_met"]) else "INVALID"
    result["verdict"] = verdict

    console.print(_phase_table("Phase B — baseline (expect F2P fail, P2P pass)",
                               res_b["by_node"], f2p, p2p, f2p_should_pass=False))
    console.print(_phase_table("Phase C — gold patch (expect all pass)",
                               res_c["by_node"], f2p, p2p, f2p_should_pass=True))

    if json_path:
        Path(json_path).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        console.print(f"  wrote JSON result to {json_path}")

    b_f2p_fail = sum(1 for n in f2p if res_b["by_node"].get(n) != "passed")
    b_p2p_pass = sum(1 for n in p2p if res_b["by_node"].get(n) == "passed")
    c_pass = sum(1 for n in scored if res_c["by_node"].get(n) == "passed")
    summary = (f"{verdict} (B: {b_f2p_fail}/{len(f2p)} F2P fail, "
               f"{b_p2p_pass}/{len(p2p)} P2P pass; gold: {c_pass}/{len(scored)} pass)")

    if verdict == "VALID":
        console.print(f"\n[bold green]VERDICT: VALID[/bold green] — {summary}")
        record("success", summary)
        raise typer.Exit(code=0)
    else:
        console.print(f"\n[bold red]VERDICT: INVALID[/bold red] — {summary}")
        for ph, key in (("Phase B", "phase_baseline"), ("Phase C", "phase_gold")):
            for v in result[key]["violations"]:
                console.print(f"  [red]{ph} violation:[/red] {v}")
        # Show pytest tails to aid diagnosis.
        console.print(f"[dim]Phase B stdout tail:[/dim]\n{res_b['stdout_tail']}")
        console.print(f"[dim]Phase C stdout tail:[/dim]\n{res_c['stdout_tail']}")
        record("success", summary)
        raise typer.Exit(code=1)


@app.command()
def run(
    bundle: Path = typer.Option(..., "--bundle", help="Path to a task bundle directory."),
    solver: Solver = typer.Option(
        Solver.noop, "--solver", help="Solver backend to produce a solution."
    ),
    solver_cmd: Optional[str] = typer.Option(
        None, "--solver-cmd", help="Command to run for the 'command' solver."
    ),
    mask: Mask = typer.Option(
        Mask.file, "--mask", help="Test-hiding strategy shown to the solver."
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Write the JSON report to this path."
    ),
    no_network: bool = typer.Option(
        True,
        "--no-network/--network",
        help="Disable container networking during the run.",
    ),
    keep_container: bool = typer.Option(
        False,
        "--keep-container/--rm-container",
        help="Keep the container after the run instead of removing it.",
    ),
) -> None:
    """Run a solver against a bundle and score the result."""
    _run_bundle(Path(bundle), solver.value, solver_cmd, mask.value, out,
                no_network, keep_container)


def _run_bundle(bundle_dir, solver_name, solver_cmd, mask_strategy, out,
                no_network, keep_container) -> None:
    run_id = uuid.uuid4().hex
    command_id = uuid.uuid4().hex
    started_at = _now_iso()
    args_json = json.dumps({
        "bundle": str(bundle_dir), "solver": solver_name, "solver_cmd": solver_cmd,
        "mask": mask_strategy, "out": str(out) if out else None,
        "no_network": no_network, "keep_container": keep_container,
    }, sort_keys=True)
    db.init_db()

    def record_cmd(status: str, message: str) -> None:
        db.record_command(
            command_id=command_id, command="run", args_json=args_json,
            bundle=str(bundle_dir), status=status, message=message[:500],
            started_at=started_at, finished_at=_now_iso(),
        )

    def error_exit(msg: str):
        record_cmd("error", msg)
        console.print(f"[red]run errored:[/red] {msg}")
        raise typer.Exit(code=2)

    # Resolve masker + solver early; NotImplementedError -> clean exit 0.
    try:
        masker = masker_mod.get_masker(mask_strategy)
    except NotImplementedError as e:
        console.print(f"[yellow]mask '{mask_strategy}' not implemented yet:[/yellow] {e}")
        raise typer.Exit(code=0)
    try:
        solver = solver_mod.get_solver(solver_name, solver_cmd)
    except NotImplementedError as e:
        console.print(f"[yellow]solver '{solver_name}' not implemented yet:[/yellow] {e}")
        raise typer.Exit(code=0)

    task_path = bundle_dir / "task.json"
    if not task_path.exists():
        error_exit(f"task.json not found: {task_path}")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    image = task["image"]
    base_commit = task["base_commit"]
    repo_path = task.get("repo_path_in_container") or "/app"
    instance_id = task["instance_id"]
    image_digest = task.get("image_digest")
    selected_test_files = task.get("test", {}).get("selected_test_files", [])
    f2p = json.loads((bundle_dir / "tests" / "fail_to_pass.json").read_text())
    p2p = json.loads((bundle_dir / "tests" / "pass_to_pass.json").read_text())
    scored = list(f2p) + list(p2p)

    try:
        instance_commit = runner_mod.instance_commit_from_id(instance_id)
    except runner_mod.RunnerError as e:
        error_exit(str(e))

    if not container_mod.image_exists(image):
        error_exit(f"image not present locally: {image}")

    network = "none" if no_network else None
    artifacts_dir = bundle_dir / "artifacts" / run_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"Running [cyan]{instance_id}[/cyan]")
    console.print(f"  solver: {solver_name}  |  mask: {mask_strategy}  |  "
                  f"network: {network or 'default'}  |  run_id: {run_id[:12]}")

    report = {
        "run_id": run_id, "command_id": command_id, "instance_id": instance_id,
        "image": image, "image_digest": image_digest, "base_commit": base_commit,
        "instance_commit": instance_commit,
        "solver": {"name": solver_name, "meta": {}},
        "mask": None, "mask_verification": {"scored_visible_after_mask": None},
        "patch": {"applied": False, "apply_error": None, "lines": 0},
        "scoring": {"by_node": {}, "f2p": {"total": len(f2p), "passed": 0},
                    "p2p": {"total": len(p2p), "passed": 0}},
        "resolved": False, "reason": None,
        "started_at": started_at, "finished_at": None,
    }

    def clean_baseline(c):
        for cmd in (f"git -C {repo_path} reset --hard {base_commit}",
                    f"git -C {repo_path} clean -fd",
                    f"git -C {repo_path} checkout {base_commit}"):
            rc, _, err = c.exec(cmd)
            if rc != 0:
                raise runner_mod.RunnerError(f"baseline reset failed: {cmd}\n{err.strip()}")

    scoring = None
    try:
        cm = container_mod.container_session(
            image, network=network, memory="4g", cpus="2", pids_limit=512)
        c = cm.__enter__()
        kept_name = c.name
        try:
            # ---- A) SOLVE ----
            clean_baseline(c)
            mask_res = masker.mask(c, repo_path, selected_test_files, scored)
            report["mask"] = mask_res.as_dict()

            # MASK VERIFICATION: measure the masked tree the SOLVER will see,
            # BEFORE the solver runs (a command solver may restore files during
            # patch capture). How many scored node IDs remain collectable?
            visible = 0
            for tf in selected_test_files:
                rc, out_c, _ = c.exec(
                    f"python -m pytest --collect-only -q {tf}", workdir=repo_path)
                if rc == 0:
                    ids = _parse_collected(out_c, tf)
                    visible += sum(1 for n in scored if n in ids)
            report["mask_verification"]["scored_visible_after_mask"] = visible

            solve_res = solver.solve(c, repo_path, base_commit, bundle_dir,
                                     mask_res, selected_test_files)
            report["solver"]["meta"] = solve_res.meta
            if solve_res.error:
                error_exit(f"solver error: {solve_res.error}")
            (artifacts_dir / "solver_patch.diff").write_text(
                solve_res.patch, encoding="utf-8")

            # ---- B) SCORE ----
            clean_baseline(c)
            patch = solve_res.patch
            if patch.strip():
                c.cp_to(str(artifacts_dir / "solver_patch.diff"), "/tmp/solver_patch.diff")
                rc, _, err = c.exec(f"git -C {repo_path} apply --check /tmp/solver_patch.diff")
                if rc != 0:
                    report["patch"]["applied"] = False
                    report["patch"]["apply_error"] = err.strip()
                    report["reason"] = "patch_apply_failed"
                else:
                    rc2, _, err2 = c.exec(f"git -C {repo_path} apply /tmp/solver_patch.diff")
                    if rc2 != 0:
                        report["patch"]["applied"] = False
                        report["patch"]["apply_error"] = err2.strip()
                        report["reason"] = "patch_apply_failed"
                    else:
                        report["patch"]["applied"] = True
            else:
                report["patch"]["applied"] = True  # empty no-op patch
            report["patch"]["lines"] = patch.count("\n")

            if report["patch"]["applied"]:
                runner_mod.stage_tests(c, repo_path, instance_commit, selected_test_files)
                scoring = runner_mod.run_pytest(c, repo_path, scored)
                if scoring.get("error"):
                    error_exit(f"scoring pytest could not run: {scoring['error']}\n"
                               f"{scoring['stdout_tail']}\n{scoring['stderr_tail']}")
                report["scoring"]["by_node"] = scoring["by_node"]
                report["scoring"]["f2p"]["passed"] = sum(
                    1 for n in f2p if scoring["by_node"].get(n) == "passed")
                report["scoring"]["p2p"]["passed"] = sum(
                    1 for n in p2p if scoring["by_node"].get(n) == "passed")
        finally:
            if keep_container:
                console.print(f"[yellow]--keep-container:[/yellow] left container "
                              f"[bold]{kept_name}[/bold] (docker rm -f {kept_name}).")
            else:
                cm.__exit__(None, None, None)
    except container_mod.ContainerError as e:
        error_exit(f"container error: {e}")
    except runner_mod.RunnerError as e:
        error_exit(str(e))

    # ---- VERDICT ----
    f2p_passed = report["scoring"]["f2p"]["passed"]
    p2p_passed = report["scoring"]["p2p"]["passed"]
    applied = report["patch"]["applied"]
    f2p_ok = applied and f2p_passed == len(f2p)
    p2p_ok = applied and p2p_passed == len(p2p)
    resolved = bool(applied and f2p_ok and p2p_ok)
    report["resolved"] = resolved
    if report["reason"] is None:
        if resolved:
            report["reason"] = "resolved"
        else:
            reasons = []
            if not f2p_ok:
                reasons.append("f2p_not_passed")
            if not p2p_ok:
                reasons.append("p2p_regressed")
            report["reason"] = "+".join(reasons) if reasons else "not_resolved"
    report["finished_at"] = _now_iso()

    # ---- WRITE REPORT ----
    report_path = artifacts_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if out:
        Path(out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    # ---- DB ----
    db.record_run(
        run_id=run_id, command_id=command_id, instance_id=instance_id,
        solver=solver_name, image=image, commit_sha=base_commit,
        resolved=1 if resolved else 0,
        f2p_total=len(f2p), f2p_passed=f2p_passed,
        p2p_total=len(p2p), p2p_passed=p2p_passed,
        results_json=json.dumps(report["scoring"]["by_node"]),
        patch_applied=1 if applied else 0, report_path=str(report_path),
        started_at=started_at, finished_at=report["finished_at"],
    )
    record_cmd("success", f"{'RESOLVED' if resolved else 'NOT RESOLVED'} "
               f"({report['reason']}); F2P {f2p_passed}/{len(f2p)}, P2P {p2p_passed}/{len(p2p)}")

    # ---- RICH OUTPUT ----
    mr = report["mask"]
    console.print(f"\n[bold]Mask[/bold] ({mr['strategy']}): hid files {mr['masked_files']}; "
                  f"scored_hidden={mr['scored_hidden']}, "
                  f"non_scored_hidden={mr['non_scored_hidden']} {mr['non_scored_names']}; "
                  f"scored_visible_after_mask={report['mask_verification']['scored_visible_after_mask']}")
    if not applied:
        console.print(f"[red]patch did not apply:[/red] {report['patch']['apply_error']}")
    else:
        table = Table(title="Scoring")
        table.add_column("node"); table.add_column("bucket")
        table.add_column("outcome"); table.add_column("OK")
        for node in scored:
            bucket = "F2P" if node in f2p else "P2P"
            outcome = report["scoring"]["by_node"].get(node, "missing")
            ok = outcome == "passed"
            table.add_row(node.split("::", 1)[-1], bucket, outcome,
                          "[green]OK[/green]" if ok else "[red]✗[/red]")
        console.print(table)
    console.print(f"  report: {report_path}")
    if resolved:
        console.print(f"[bold green]RESOLVED[/bold green] (reason: {report['reason']})")
    else:
        console.print(f"[bold red]NOT RESOLVED[/bold red] (reason: {report['reason']})")
    raise typer.Exit(code=0)


@app.command()
def log(
    id: str = typer.Option(..., "--id", help="A command_id or run_id to show the log for."),
) -> None:
    """Show the recorded log for a command or run."""
    db.init_db()
    run = db.get_run(id) or _prefix_lookup(db.list_runs(1000), "run_id", id)
    if run:
        console.print(f"[bold]run[/bold] {run['run_id']}")
        for k in ("command_id", "instance_id", "solver", "image", "commit_sha",
                  "resolved", "f2p_total", "f2p_passed", "p2p_total", "p2p_passed",
                  "patch_applied", "report_path", "started_at", "finished_at"):
            console.print(f"  {k}: {run[k]}")
        try:
            by_node = json.loads(run["results_json"] or "{}")
        except (TypeError, ValueError):
            by_node = {}
        if by_node:
            table = Table(title="Per-node results")
            table.add_column("node"); table.add_column("outcome"); table.add_column("OK")
            for node, outcome in by_node.items():
                ok = outcome == "passed"
                table.add_row(node.split("::", 1)[-1], outcome,
                              "[green]OK[/green]" if ok else "[red]✗[/red]")
            console.print(table)
        raise typer.Exit(code=0)

    cmd = db.get_command(id) or _prefix_lookup(db.list_commands(1000), "command_id", id)
    if cmd:
        console.print(f"[bold]command[/bold] {cmd['command_id']}")
        for k in ("command", "args_json", "bundle", "status", "message",
                  "started_at", "finished_at"):
            console.print(f"  {k}: {cmd[k]}")
        raise typer.Exit(code=0)

    console.print(f"[red]no command or run with id {id}[/red]")
    raise typer.Exit(code=1)


def _prefix_lookup(rows, key, value):
    """Return the single row whose key starts with value, else None."""
    matches = [r for r in rows if str(r[key]).startswith(value)]
    return matches[0] if len(matches) == 1 else None


@app.command()
def runs(
    limit: int = typer.Option(20, "--limit", help="Maximum number of runs to list."),
) -> None:
    """List recent runs."""
    db.init_db()
    rows = db.list_runs(limit)
    if not rows:
        console.print("[yellow]no runs recorded yet[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"runs (latest {len(rows)})")
    for col in ("run_id", "instance", "solver", "resolved", "F2P", "P2P", "finished_at"):
        table.add_column(col)
    for r in rows:
        resolved = "[green]✓[/green]" if r["resolved"] else "[red]✗[/red]"
        inst = (r["instance_id"] or "")
        inst_short = inst[:28] + "…" if len(inst) > 29 else inst
        table.add_row(
            (r["run_id"] or "")[:12], inst_short, r["solver"] or "", resolved,
            f"{r['f2p_passed']}/{r['f2p_total']}", f"{r['p2p_passed']}/{r['p2p_total']}",
            r["finished_at"] or "",
        )
    console.print(table)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
