#!/usr/bin/env python3
"""Prepare and verify repository enrollment; apply the Linear project label last."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote

from common import configuration
from urllib.request import urlopen
from common.clients import Linear, RequestError
from common.routing import Registry, eligibility, uuid_string
from common.policy import PROTOCOL, digest
from common.accounts import WORKER_REPOSITORY
from common.reporting import STAGE_LABELS
from controller.release import local_manifest,INPUT_ROOTS
from controller.workflows import WORKFLOW as CONTROLLER_WORKFLOW

CI_WORKFLOW = "clusterfork-validation.yml"
CI_CHECK = "Clusterfork validation"
REGISTRY = configuration.REGISTRY
CONTROLLER_HEALTH='https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/health'


class SetupError(RuntimeError):
    pass


class GitHubAccessError(SetupError):
    def __init__(self, path, status=None):
        self.status = status
        super().__init__(f"GitHub access failed for {path}" + (f" (HTTP {status})" if status else "")
                         + "; use the existing operator's repository access; no authentication was changed")


def command(args, *, cwd=None, data=None):
    result = subprocess.run(args, cwd=cwd, input=data, capture_output=True, text=True)
    if result.returncode:
        raise SetupError(f"{args[0]} failed; verify the checkout and existing operator access")
    return result.stdout.strip()


def gh(path, *, method="GET", data=None):
    args = ["gh", "api", path, "--method", method]
    if data is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(data) if data is not None else None,
                            capture_output=True, text=True)
    if result.returncode:
        match = re.search(r"HTTP (\d{3})", result.stderr)
        raise GitHubAccessError(path, int(match[1]) if match else None)
    return json.loads(result.stdout) if result.stdout.strip() else None


def gh_pages(path, field=None):
    page = 1
    while True:
        result = gh(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        rows = result[field] if field else result
        yield from rows
        if len(rows) < 100:
            return
        page += 1


def repository(checkout):
    remote = command(["git", "remote", "get-url", "origin"], cwd=checkout)
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                         r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", remote)
    if not match:
        raise SetupError("The checkout's origin must identify a GitHub owner/repository")
    info = gh(f"repos/{match[1]}")
    if info.get("archived") or info.get("disabled") or not info.get("default_branch"):
        raise SetupError("The target repository must be active and have a default branch")
    return info["full_name"], info["default_branch"]


def connection(linear, query, variables, *path):
    cursor = None
    while True:
        result = linear.query(query, {**variables, "after": cursor})
        for key in path:
            result = result[key]
        yield from result["nodes"]
        if not result["pageInfo"]["hasNextPage"]:
            return
        after = result["pageInfo"]["endCursor"]
        if not after or after == cursor:
            raise SetupError("Linear pagination did not advance; enrollment remains disabled")
        cursor = after


def project(linear, project_id):
    result = linear.query("""query($id: String!) {
      project(id: $id) { id name archivedAt teams(first: 100) {
        nodes { id name } pageInfo { hasNextPage }
      } }
    }""", {"id": uuid_string(project_id)})["project"]
    if not result or result["archivedAt"] or result["teams"]["pageInfo"]["hasNextPage"]:
        raise SetupError("Select an accessible, active existing Linear project with a complete team listing")
    return result


def team_states(linear, team_id):
    return list(connection(linear, """query($id: String!, $after: String) {
      team(id: $id) { states(first: 100, after: $after) {
        nodes { id name type } pageInfo { hasNextPage endCursor }
      } }
    }""", {"id": uuid_string(team_id)}, "team", "states"))


def validate_states(linear, project_id, team_id, states):
    selected = project(linear, project_id)
    if team_id not in {team["id"] for team in selected["teams"]["nodes"]}:
        raise SetupError("Select a team belonging to the explicitly selected project")
    available = {state["id"]: state for state in team_states(linear, team_id)}
    if set(states) != {"ready", "in_progress", "blocked", "done"} or not isinstance(states["ready"], list) or not states["ready"]:
        raise SetupError("States need ready (a nonempty UUID array), in_progress, blocked, and done")
    expected = [(identifier, "unstarted") for identifier in states["ready"]]
    expected += [(states["in_progress"], "started"), (states["done"], "completed"), (states["blocked"], None)]
    for identifier, kind in expected:
        state = available.get(uuid_string(identifier))
        if not state or (kind and state["type"] != kind) or state["type"] in {"backlog", "canceled"}:
            raise SetupError("Configured states must belong to the selected team and have the intended lifecycle types")
    if available[states["blocked"]]["type"] == "completed":
        raise SetupError("The blocked lifecycle state cannot be completed")




def validation_workflow(commands):
    return """# Managed by Clusterfork setup-remote-development.
name: Clusterfork repository validation
on: [push, pull_request]
permissions:
  contents: read
jobs:
  validate:
    name: Clusterfork validation
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Run repository validation
        env:
          CLUSTERFORK_VALIDATION_COMMANDS: """ + json.dumps(json.dumps(commands)) + """
        run: |
          python3 - <<'PYTHON'
          import json, os, subprocess
          for argv in json.loads(os.environ['CLUSTERFORK_VALIDATION_COMMANDS']):
              subprocess.run(argv, check=True)
          PYTHON
"""


def validate_registry(data):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "projects.json"
        path.write_text(json.dumps(data))
        return Registry.load(path)


def mapping(data, project_id):
    row = next((row for row in data["projects"] if row["project_id"] == project_id), None)
    if row is None:
        raise SetupError("Configure and publish the explicit project UUID mapping in the central registry first")
    return row


def prepare_configuration(data, project_id, repo, branch, team_id, states, commands, checks):
    result = copy.deepcopy(data)
    validate_registry(result)  # Reject pre-existing duplicate/invalid mappings before editing.
    existing = next((row for row in result["projects"] if row["project_id"] == project_id), None)
    if existing and existing["repository"].lower() != repo.lower():
        raise SetupError("This project UUID already maps to another repository; resolve that mapping explicitly")
    row = existing if existing is not None else {"project_id": uuid_string(project_id), "auto_merge": True}
    row.update(repository=repo, workflow=CONTROLLER_WORKFLOW, ref=branch, linear_team_id=uuid_string(team_id),
               ready_state_ids=states["ready"], linear_states={k: states[k] for k in ("in_progress", "blocked", "done")},
               validation_commands=commands,
               required_checks=list(dict.fromkeys([*row.get("required_checks", []), *checks])))
    row.pop('worker_revision',None)
    if existing is None:
        result["projects"].append(row)
    validate_registry(result)
    return result


def configure(checkout, registry_path, data, project_id, updated, *, install_ci=False, apply=False):
    row = next(row for row in updated["projects"] if row["project_id"] == project_id)
    previous = next((row for row in data["projects"] if row["project_id"] == project_id), {})
    changes = []
    workflow_dir = checkout / ".github/workflows"
    files = []
    if install_ci:
        files.append((CI_WORKFLOW, validation_workflow(row["validation_commands"]),
                      validation_workflow(previous.get("validation_commands", []))))
    for name, content, old_content in files:
        path = workflow_dir / name
        if path.is_symlink() or workflow_dir.is_symlink() or workflow_dir.parent.is_symlink():
            raise SetupError("Refusing to replace a symlinked workflow path")
        current = path.read_text() if path.exists() else None
        if current == content:
            continue
        if current is not None and current != old_content:
            raise SetupError(f"Preserve and reconcile the existing {path}; it is not the expected managed workflow")
        changes.append((path, content, current))
    # Install files before recording their mapping. An interrupted application
    # can then finish on rerun even when only some workflow writes succeeded.
    if updated != data:
        changes.append((registry_path, json.dumps(updated, indent=2) + "\n", None))
    if apply:
        # All collisions are checked before the first write. Identical reruns write nothing.
        if json.loads(registry_path.read_text()) != data:
            raise SetupError("The central registry changed during setup; reread it before applying this mapping")
        for path, content, _ in changes:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as target:
                target.write(content)
                temporary = target.name
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
    return [str(path) for path, _, _ in changes]


def effective_checks(repo, branch):
    checks = set()
    path = f"repos/{repo}/branches/{quote(branch, safe='')}/protection/required_status_checks"
    try:
        classic = gh(path)
    except GitHubAccessError as exc:
        if exc.status != 404:
            raise
    else:
        checks.update(classic.get("contexts", []))
        checks.update(check["context"] for check in classic.get("checks", []))
    for rule in gh_pages(f"repos/{repo}/rules/branches/{quote(branch, safe='')}"):
        if rule["type"] == "required_status_checks":
            checks.update(check["context"] for check in rule["parameters"]["required_status_checks"])
    return checks


def configure_merge(row, *, apply=False):
    repo, branch = row["repository"], row["ref"]
    required = set(row["required_checks"])
    if not required or not row.get("auto_merge", True):
        raise SetupError("Resolve the existing merge policy and configure meaningful required CI checks")
    changes = []
    info = gh(f"repos/{repo}")
    settings = {key: True for key in ("allow_squash_merge",) if info.get(key) is not True}
    missing = required - effective_checks(repo, branch)
    ruleset = None
    body = None
    if missing:
        name = f"clusterfork-remote-development:{branch}"
        found = [r for r in gh_pages(f"repos/{repo}/rulesets?includes_parents=false") if r["name"] == name]
        if len(found) > 1:
            raise SetupError("Resolve duplicate Clusterfork rulesets before proceeding")
        conditions = {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}}
        if found:
            ruleset = gh(f"repos/{repo}/rulesets/{found[0]['id']}")
            if ruleset["conditions"] != conditions or ruleset.get("bypass_actors"):
                raise SetupError("Review the existing Clusterfork ruleset scope/bypasses explicitly")
            body = {key: copy.deepcopy(ruleset[key]) for key in ("name", "target", "conditions", "rules")}
        else:
            body = {"name": name, "target": "branch", "conditions": conditions, "rules": []}
        body.update(enforcement="active", bypass_actors=[])
        rule = next((rule for rule in body["rules"] if rule["type"] == "required_status_checks"), None)
        if rule is None:
            rule = {"type": "required_status_checks", "parameters": {"required_status_checks": []}}
            body["rules"].append(rule)
        params = rule["parameters"]
        present = {check["context"] for check in params["required_status_checks"]}
        params["required_status_checks"] += [{"context": name} for name in sorted(required - present)]
        params.update(strict_required_status_checks_policy=True, do_not_enforce_on_create=False)
    if settings:
        changes.append("enable repository squash merge for controller-managed merging")
    if body:
        changes.append("add required checks without replacing existing protections: " + ", ".join(sorted(missing)))
    if apply:
        if body:
            path = f"repos/{repo}/rulesets" + (f"/{ruleset['id']}" if ruleset else "")
            gh(path, method="PUT" if ruleset else "POST", data=body)
        if settings:
            gh(f"repos/{repo}", method="PATCH", data=settings)
    return changes




def project_labels(linear, project_id):
    return list(connection(linear, """query($id: String!, $after: String) {
      project(id: $id) { labels(first: 100, after: $after) {
        nodes { id name } pageInfo { hasNextPage endCursor }
      } }
    }""", {"id": project_id}, "project", "labels"))


def enrollment_label(linear, row, name, label_id=None):
    labels = list(connection(linear, """query($after: String) {
      projectLabels(first: 100, after: $after) {
        nodes { id name isGroup retiredAt parent { id } team { id } }
        pageInfo { hasNextPage endCursor }
      }
    }""", {}, "projectLabels"))
    choices = [label for label in labels if label["name"] == name and not label["isGroup"]
               and not label["parent"] and not label["retiredAt"]
               and (not label["team"] or label["team"]["id"] == row["linear_team_id"])
               and (label_id is None or label["id"] == label_id)]
    if len(choices) != 1:
        raise SetupError(f"Select/provision one ungrouped project label named {name}; pass --enrollment-label-id if ambiguous")
    return choices[0]["id"]


def controller_health(project_id):
    with urlopen(CONTROLLER_HEALTH+'?setup_project_id='+uuid_string(project_id),timeout=15) as response:
        return json.load(response)


def verify(checkout, registry_path, project_id, linear, *, label_id=None):
    data = json.loads(registry_path.read_text())
    registry = Registry.load(registry_path)
    row = mapping(data, project_id)
    repo, branch = repository(checkout)
    if (repo, branch) != (row["repository"], row["ref"]):
        raise SetupError("The explicit mapping must use this checkout's repository and default branch")
    validate_states(linear, project_id, row["linear_team_id"], {"ready": row["ready_state_ids"], **row["linear_states"]})
    health = controller_health(project_id)
    if health.get("mode")!="single-controller":
        raise SetupError("Deploy the central task controller before enrollment")
    revision=command(['git','rev-parse','HEAD'],cwd=configuration.ROOT)
    if command(['git','status','--porcelain','--',*INPUT_ROOTS],cwd=configuration.ROOT):
        raise SetupError('Deploy the committed runtime inputs before enrollment')
    if (health.get('missing')!=[] or health.get('deployment_paused') is not False
            or health.get('serial_admission') is not True or health.get('phase_isolation') is not True
            or health.get('registry_digest')!=digest(data) or health.get('protocol')!=PROTOCOL
            or health.get('deployment',{}).get('digest')!=local_manifest(configuration.ROOT,revision)['digest']):
        raise SetupError('Deploy the exact central registry/runtime, verify its protocol, and resume admissions')
    evidence = health.get("repository_setup", {})
    if any(evidence.get(key) != value for key, value in
           (("project_id", project_id), ("repository", repo), ("ref", branch))):
        raise SetupError("The deployed broker must verify App access for this exact project/repository/ref")
    if evidence.get("app_access") is not True:
        raise SetupError(f"Verify the existing GitHub App installation has access to {repo} in the broker")
    missing = [name for name in ("actions", "contents", "pull_requests", "workflows")
               if evidence.get("permissions", {}).get(name) != "write"]
    if missing:
        raise SetupError("Approve the existing App installation's missing write grants: " + ", ".join(missing))
    permissions = gh(f"repos/{repo}/actions/permissions")
    if permissions.get("enabled") is not True:
        raise SetupError("Enable GitHub Actions for the target repository with existing operator access")
    source = gh(f"repos/{WORKER_REPOSITORY}")
    if source.get("private"):
        raise SetupError("The caller's Actions token cannot check out a private central engine; central runtime read access must be resolved without adding target repository secrets")
    if (evidence.get('workflow_repository')!=WORKER_REPOSITORY or evidence.get('workflow')!=CONTROLLER_WORKFLOW
            or evidence.get('runtime_sha')!=health.get('deployment',{}).get('revision')
            or evidence.get('runtime_tag_verified') is not True or evidence.get('workflow_verified') is not True):
        raise SetupError('Verify the central task workflow and immutable deployed runtime tag')
    if gh(f'repos/{WORKER_REPOSITORY}/actions/permissions').get('enabled') is not True:
        raise SetupError('Enable GitHub Actions for the central task engine')
    if evidence.get("workflow_active") is not True:
        raise SetupError("The configured worker workflow must be active in GitHub Actions")
    info = gh(f"repos/{repo}")
    if not row["auto_merge"] or not info.get("allow_squash_merge"):
        raise SetupError("Configure automatic squash merging without overriding the existing merge policy")
    required = set(row["required_checks"])
    if not required or not required.issubset(effective_checks(repo, branch)):
        raise SetupError("Protect the default branch with all configured required CI checks")
    head = gh(f"repos/{repo}/commits/{quote(branch, safe='')}")["sha"]
    if head != evidence.get("branch_sha"):
        raise SetupError("The default branch changed during setup verification; verify its workflow and CI again")
    latest = {}
    for run in sorted(gh_pages(f"repos/{repo}/commits/{head}/check-runs", "check_runs"), key=lambda r: r["id"], reverse=True):
        latest.setdefault(run["name"], run["conclusion"] == "success")
    statuses = {}
    for status in sorted(gh_pages(f"repos/{repo}/commits/{head}/statuses"), key=lambda r: r["id"], reverse=True):
        statuses.setdefault(status["context"], status["state"] == "success")
    for name, passed in statuses.items():
        latest[name] = latest.get(name, True) and passed
    successful = {name for name, passed in latest.items() if passed}
    if not required.issubset(successful):
        raise SetupError("Run meaningful configured CI successfully on the current default branch before enrollment")
    labels = list(connection(linear, """query($team: ID!, $after: String) {
      issueLabels(first: 100, after: $after, filter: {team: {id: {eq: $team}}}) {
        nodes { name parent { id } isGroup } pageInfo { hasNextPage endCursor }
      }
    }""", {"team": row["linear_team_id"]}, "issueLabels"))
    names = Counter(label["name"] for label in labels if not label["parent"] and not label["isGroup"])
    needed = set(STAGE_LABELS.values()) | {"clusterfork: queued", "clusterfork: selected", "clusterfork: needs clarification", "clusterfork: auto-repair"}
    if needed - names.keys():
        raise SetupError("A team label operator must provision independent issue labels: " + ", ".join(sorted(needed - names.keys())))
    if any(names[name] != 1 for name in needed):
        raise SetupError("Resolve duplicate Clusterfork team issue labels before enrollment")
    selected_label = enrollment_label(linear, row, data["enrollment_label"], label_id)
    candidates = []
    for issue in linear.project_issues(project_id):
        candidate = copy.deepcopy(issue)
        candidate["project"]["labels"]["nodes"].append({"name": registry.enrollment_label})
        if eligibility(candidate, registry)[0]:
            candidates.append({key: issue[key] for key in ("id", "identifier", "title")})
    if gh(f"repos/{repo}/commits/{quote(branch, safe='')}")["sha"] != head:
        raise SetupError("The default branch changed during setup verification; verify its workflow and CI again")
    return {"repository": repo, "ref": branch, "project_id": project_id, "project_label_id": selected_label,
            "enrollment_label": registry.enrollment_label,
            "eligible_todo": candidates, "registry_digest": digest(data), "protocol": health["protocol"]}


def enroll(checkout, registry_path, project_id, linear, *, label_id=None):
    result = verify(checkout, registry_path, project_id, linear, label_id=label_id)
    print(json.dumps(result, indent=2), flush=True)
    labels = project_labels(linear, project_id)
    if any(label["name"] == result["enrollment_label"] for label in labels):
        return "already enrolled; no changes"
    # Additive mutation preserves concurrent/unrelated labels. Never use labelIds replacement.
    response = linear.query("""mutation($id: String!, $label: String!) {
      projectAddLabel(id: $id, labelId: $label) { success }
    }""", {"id": project_id, "label": result["project_label_id"]})
    if not response["projectAddLabel"]["success"]:
        raise SetupError("Project label application failed; verify current enrollment before retrying")
    if result["project_label_id"] not in {label["id"] for label in project_labels(linear, project_id)}:
        raise SetupError("Project label application is unverified; do not claim enrollment is complete")
    return "enrolled; project label + Todo now enters the repository queue and readiness review"


def linear_client(env_file):
    values = configuration.settings(env_file)
    if not values.get("LINEAR_API_KEY") and not (values.get("LINEAR_OAUTH_CLIENT_ID") and values.get("LINEAR_OAUTH_CLIENT_SECRET")):
        raise SetupError("An existing central Linear operator credential is required; keep it outside the target repository")
    linear = Linear(values.get("LINEAR_API_KEY"), client_id=values.get("LINEAR_OAUTH_CLIENT_ID"),
                    client_secret=values.get("LINEAR_OAUTH_CLIENT_SECRET"))
    return linear


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("discover", "configure", "merge", "verify", "enroll"))
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--env-file", type=Path, default=configuration.ROOT / ".env")
    parser.add_argument("--project-id", type=uuid_string)
    parser.add_argument("--team-id", type=uuid_string)
    parser.add_argument("--states-json", type=json.loads)
    parser.add_argument("--validation-commands", type=json.loads)
    parser.add_argument("--required-check", action="append", default=[])
    parser.add_argument("--install-ci", action="store_true")
    parser.add_argument("--apply", action="store_true", help="apply local configuration or merge settings; enroll always applies last")
    parser.add_argument("--enrollment-label-id", type=uuid_string)
    args = parser.parse_args()
    repo, branch = repository(args.checkout)
    if args.action != "discover" and not args.project_id:
        parser.error("An explicit --project-id UUID is required; names are never used for mapping")
    if args.action == "merge":
        Registry.load(args.registry)
        row = mapping(json.loads(args.registry.read_text()), args.project_id)
        if (repo, branch) != (row["repository"], row["ref"]):
            raise SetupError("The selected mapping must match this checkout and default branch")
        print(json.dumps(configure_merge(row, apply=args.apply), indent=2))
        return
    linear = linear_client(args.env_file)
    if args.action == "discover":
        if args.project_id:
            selected = project(linear, args.project_id)
            result = {"project": selected, "team_states": {team["id"]: team_states(linear, team["id"])
                      for team in selected["teams"]["nodes"]}}
        else:
            result = {"projects": list(connection(linear, """query($after: String) {
              projects(first: 100, after: $after) { nodes { id name } pageInfo { hasNextPage endCursor } }
            }""", {}, "projects"))}
        print(json.dumps({"repository": repo, "default_branch": branch, **result}, indent=2))
    elif args.action == "configure":
        if not all((args.team_id, args.states_json, args.validation_commands)):
            parser.error("configure requires --team-id, --states-json, and --validation-commands")
        checks = args.required_check + ([CI_CHECK] if args.install_ci else [])
        if not checks:
            parser.error("choose existing --required-check names or --install-ci")
        validate_states(linear, args.project_id, args.team_id, args.states_json)
        data = json.loads(args.registry.read_text())
        updated = prepare_configuration(data, args.project_id, repo, branch, args.team_id, args.states_json,
                                        args.validation_commands, checks)
        print(json.dumps({"mapping": next(row for row in updated["projects"] if row["project_id"] == args.project_id),
                          "files": configure(args.checkout, args.registry, data, args.project_id, updated,
                                             install_ci=args.install_ci, apply=args.apply),
                          "applied": args.apply, "enrolled": False}, indent=2))
    elif args.action == "verify":
        print(json.dumps(verify(args.checkout, args.registry, args.project_id, linear,
                                label_id=args.enrollment_label_id), indent=2))
    else:
        print(enroll(args.checkout, args.registry, args.project_id, linear, label_id=args.enrollment_label_id))


if __name__ == "__main__":
    try:
        main()
    except (SetupError, RequestError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, RequestError):
            message = "Linear access failed; verify the existing operator's read/write access to the selected project/team"
        else:
            message = str(exc) if isinstance(exc, SetupError) else f"{type(exc).__name__}; check the explicit mapping and existing operator access"
        print(f"Setup incomplete: {message}", file=sys.stderr)
        raise SystemExit(1)
