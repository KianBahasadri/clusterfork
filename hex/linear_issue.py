"""Draft Linear issues with OpenCode and save them through Clusterfork's client."""

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from http.client import HTTPSConnection, HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from common.clients import Linear, RequestError
from common.configuration import settings
from common.images import EXTENSIONS
from common.issue_metadata import ISSUE_METADATA_POLICY, estimate_values, validate_metadata


WORKSPACE = Path(__file__).resolve().parent
DOTENV = Path.home() / ".config/clusterfork/.env"
MODEL = "opencode-go/gpt-6-luna#max"
AGENT = "linear-issue"
IMAGE_QUESTION = "Copy an image for this request, then click Reply."
ISSUE_KEYS = {"title", "description", "project_id", "team_id", "priority", "estimate",
              "label_ids", "blocked_by", "blocks", "attach_image"}
UPDATE_KEYS = {"issue_id", "updated_at", "changes", "blocked_by", "blocks", "attach_image"}
UPDATE_FIELDS = {"title", "description", "append_description", "priority", "estimate", "stateId",
                 "projectId", "dueDate", "addedLabelIds", "removedLabelIds"}
UPDATE_SELECTION = ("id identifier url updatedAt trashed title description priority estimate dueDate labelIds "
                    "state { id } project { id } team { id }")

CATALOG_FIELDS = {
    "projects": ("first: 50", "id name slugId description status { type } "
                 "teams(first: 30) { nodes { id } pageInfo { hasNextPage endCursor } }"),
    "teams": ("first: 50", "id name key parent { id } inheritIssueEstimation "
              "issueEstimationType issueEstimationExtended issueEstimationAllowZero"),
    "issueLabels": ("first: 100", "id name description isGroup team { id } parent { name }"),
    "workflowStates": ("first: 50", "id name type team { id }"),
}


class FilingError(ValueError):
    """An error message safe to show in a local notification."""


class ReplyNeeded(FilingError):
    def __init__(self, question, *, copy_image=False):
        super().__init__(question)
        self.copy_image = copy_image


@dataclass(frozen=True)
class Dictation:
    transcript: str
    title: str | None
    issue_id: str
    answers: tuple[tuple[str, str], ...] = ()
    image_use: Literal["attach", "reference"] | None = None
    image: tuple[str, bytes] | None = None


@dataclass(frozen=True)
class IssueDraft:
    fields: dict
    blocked_by: list[str]
    blocks: list[str]
    attach_image: bool
    existing: dict | None = None


def configured_client():
    values = settings(DOTENV)
    token = values.get("LINEAR_API_KEY")
    actor, secret = values.get("LINEAR_OAUTH_CLIENT_ID"), values.get("LINEAR_OAUTH_CLIENT_SECRET")
    if not (token or (actor and secret)):
        raise FilingError("Configure the shared Linear credential in Clusterfork before filing issues.")
    return Linear(token, client_id=actor, client_secret=secret)


def next_cursor(connection, previous):
    page = connection["pageInfo"]
    if not page["hasNextPage"]:
        return None
    cursor = page["endCursor"]
    if not cursor or cursor == previous:
        raise FilingError("Linear catalog pagination did not advance.")
    return cursor


def read_catalog(linear):
    rows = {key: [] for key in CATALOG_FIELDS}
    cursors = dict.fromkeys(rows)
    while cursors:
        variables = ", ".join(f"${key}: String" for key in cursors)
        fields = "\n".join(
            f"{key}({args}, after: ${key}) {{ nodes {{ {selection} }} pageInfo {{ hasNextPage endCursor }} }}"
            for key, (args, selection) in CATALOG_FIELDS.items() if key in cursors
        )
        data = linear.query(f"query({variables}) {{ {fields} }}", cursors)
        remaining = {}
        for key, cursor in cursors.items():
            rows[key].extend(data[key]["nodes"])
            following = next_cursor(data[key], cursor)
            if following:
                remaining[key] = following
        cursors = remaining

    states = rows["workflowStates"]
    raw_teams = {team["id"]: team for team in rows["teams"]}
    teams = {}
    for team_id, team in raw_teams.items():
        team_states = [{key: state[key] for key in ("id", "name", "type")}
                       for state in states if state["team"]["id"] == team_id]
        defaults = [state for state in team_states if state["type"] == "backlog"]
        backlog = next((state for state in defaults if state["name"].casefold() == "backlog"), None)
        if backlog is None and len(defaults) == 1:
            backlog = defaults[0]
        scale, seen = team, {team_id}
        while scale and scale["inheritIssueEstimation"] and scale["parent"]:
            parent = scale["parent"]["id"]
            scale = raw_teams.get(parent) if parent not in seen else None
            seen.add(parent)
        try:
            estimates = estimate_values(scale) if scale else linear.team_estimates(team_id)
        except ValueError:
            estimates = []
        teams[team_id] = {"id": team_id, "name": team["name"], "key": team["key"],
                          "estimate_type": scale["issueEstimationType"] if scale else None,
                          "estimates": estimates, "backlog": backlog["id"] if backlog else None,
                          "states": team_states}

    projects = {}
    for project in rows["projects"]:
        conn = project["teams"]
        team_ids = [node["id"] for node in conn["nodes"]]
        cursor = next_cursor(conn, None)
        while cursor:
            conn = linear.query("""query($id: String!, $after: String!) {
              project(id: $id) { teams(first: 30, after: $after) {
                nodes { id } pageInfo { hasNextPage endCursor }
              } }
            }""", {"id": project["id"], "after": cursor})["project"]["teams"]
            team_ids.extend(node["id"] for node in conn["nodes"])
            cursor = next_cursor(conn, cursor)
        projects[project["id"]] = {
            "id": project["id"], "name": project["name"], "slug": project["slugId"],
            "description": (project["description"] or "")[:500], "status": project["status"]["type"],
            "team_ids": [team_id for team_id in team_ids if team_id in teams],
        }

    labels = {
        label["id"]: {"id": label["id"], "name": label["name"], "description": label["description"],
                      "team_id": (label["team"] or {}).get("id"), "group": (label["parent"] or {}).get("name")}
        for label in rows["issueLabels"] if not label["isGroup"]
    }
    return projects, teams, labels


def focused_project(title, projects):
    if title:
        hits = [project["id"] for project in projects.values()
                if any(name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", title, re.IGNORECASE)
                       for name in (project["name"], project["slug"]))]
        if len(hits) == 1:
            return hits[0]
    return None


def opencode_draft(dictation, catalog):
    projects, teams, labels = catalog
    context = {"transcript": dictation.transcript, "focused_project_id": focused_project(dictation.title, projects),
               "clipboard_image_use": dictation.image_use, "local_date": date.today().isoformat(),
               "clarifications": [{"question": question, "answer": answer} for question, answer in dictation.answers],
               "projects": list(projects.values()), "teams": list(teams.values()), "labels": list(labels.values())}
    prompt = ISSUE_METADATA_POLICY + "\nCurrent request and Linear reference data:\n" + json.dumps(context, ensure_ascii=False)
    binary = shutil.which("opencode") or Path.home() / ".opencode/bin/opencode"
    command = [str(binary), "run", "--agent", AGENT, "--model", MODEL, "--format", "json",
               "--title", "Hex Linear dictation", "--archived"]
    with TemporaryDirectory(prefix="hex-linear-") if dictation.image else nullcontext() as directory:
        if dictation.image:
            mime, image = dictation.image
            path = Path(directory) / f"clipboard.{EXTENSIONS[mime]}"
            path.write_bytes(image)
            command.extend(["--file", str(path)])
        # The CLI uses PWD to locate project-level agents. Pass the transcript on stdin.
        result = subprocess.run(command, input=prompt, cwd=WORKSPACE, env={**os.environ, "PWD": str(WORKSPACE)},
                                text=True, capture_output=True, timeout=300)
    if result.returncode:
        raise FilingError("OpenCode failed to draft the issue. See the Hex Linear service journal.")
    return parse_draft(result.stdout)


def parse_draft(output):
    text_parts = {}
    final = None
    try:
        for line in output.splitlines():
            event = json.loads(line)
            if event["type"] == "error":
                raise FilingError("OpenCode did not complete the draft.")
            if event["type"] == "text":
                part = event["part"]
                text_parts.setdefault(part["messageID"], []).append(part["text"])
            if event["type"] == "step_finish":
                final = event["part"]
        if final is None or final["reason"] != "stop":
            raise FilingError("OpenCode did not finish the draft.")
        return json.loads("".join(text_parts[final["messageID"]]))
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
        raise FilingError("OpenCode did not return a valid issue draft.") from error


def valid_ids(values):
    return (isinstance(values, list) and all(isinstance(value, str) for value in values)
            and len(set(values)) == len(values))


def validate_links(draft, issue_id):
    if (type(draft["attach_image"]) is not bool
            or not all(valid_ids(draft[key]) for key in ("blocked_by", "blocks"))):
        raise FilingError("OpenCode returned invalid dependency or attachment fields.")
    refs = draft["blocked_by"] + draft["blocks"]
    try:
        if len(set(refs)) != len(refs) or any(str(UUID(ref)) != ref or ref == issue_id for ref in refs):
            raise ValueError("Invalid dependency IDs")
    except ValueError as error:
        raise FilingError("OpenCode returned an invalid dependency.") from error


def validate_issue(issue, catalog, issue_id):
    if (not isinstance(issue, dict) or set(issue) != ISSUE_KEYS
            or not isinstance(issue["title"], str) or not issue["title"].strip()
            or not isinstance(issue["description"], str) or not issue["description"].strip()
            or not valid_ids(issue["label_ids"])):
        raise FilingError("OpenCode returned incomplete issue fields.")
    validate_links(issue, issue_id)
    projects, teams, labels = catalog
    team_id, project_id = issue["team_id"], issue["project_id"]
    if (not isinstance(team_id, str) or not isinstance(project_id, str)
            or team_id not in teams or project_id not in projects
            or team_id not in projects[project_id]["team_ids"]):
        raise FilingError("OpenCode selected an unavailable project or team.")
    team = teams[team_id]
    if not team["backlog"]:
        raise FilingError("The selected team has no unambiguous Backlog state.")
    for label_id in issue["label_ids"]:
        if label_id not in labels or labels[label_id]["team_id"] not in (None, team_id):
            raise FilingError("OpenCode selected a label unavailable to that team.")
    try:
        validate_metadata(issue, estimates=team["estimates"])
    except (ValueError, OverflowError) as error:
        raise FilingError("OpenCode returned an invalid priority or estimate.") from error
    fields = {"id": issue_id, "title": issue["title"].strip(), "description": issue["description"].strip(),
              "projectId": project_id, "teamId": team_id, "stateId": team["backlog"],
              "labelIds": issue["label_ids"], "priority": issue["priority"], "estimate": int(issue["estimate"])}
    return IssueDraft(fields, issue["blocked_by"], issue["blocks"], issue["attach_image"])


def read_update_issue(linear, issue_id):
    return linear.query(f"query($id: String!) {{ issue(id: $id) {{ {UPDATE_SELECTION} }} }}", {"id": issue_id})["issue"]


def validate_update(linear, draft, catalog):
    if (set(draft) != UPDATE_KEYS or not isinstance(draft["issue_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+-[0-9]+", draft["issue_id"])
            or not isinstance(draft["updated_at"], str) or not draft["updated_at"]
            or not isinstance(draft["changes"], dict) or not draft["changes"].keys() <= UPDATE_FIELDS):
        raise FilingError("OpenCode returned an invalid issue update.")
    identifier = draft["issue_id"].upper()
    try:
        current = read_update_issue(linear, identifier)
    except RequestError as error:
        if error.retryable:
            raise
        current = None
    if not current or current["trashed"] or current["identifier"] != identifier:
        raise ReplyNeeded(f"I could not verify {identifier}. Which Linear issue should I update?")
    if current["updatedAt"] != draft["updated_at"]:
        raise ReplyNeeded(f"{identifier} changed while I was drafting. Should I apply your update to its current version?")
    validate_links(draft, current["id"])
    changes = draft["changes"].copy()
    if not (changes or draft["blocked_by"] or draft["blocks"] or draft["attach_image"]):
        raise ReplyNeeded(f"What would you like to change on {identifier}?")

    projects, teams, labels = catalog
    team_id = current["team"]["id"]
    team = teams.get(team_id, {})
    text_keys = changes.keys() & {"title", "description", "append_description"}
    if ({"description", "append_description"} <= text_keys
            or any(not isinstance(changes[key], str) or not changes[key].strip() for key in text_keys)):
        raise FilingError("OpenCode returned an invalid title or description edit.")
    for key in text_keys:
        changes[key] = changes[key].strip()
    if "append_description" in changes:
        existing = current["description"] or ""
        addition = changes.pop("append_description")
        changes["description"] = f"{existing}\n\n{addition}" if existing else addition
    project_id = changes.get("projectId")
    if project_id is not None and (not isinstance(project_id, str) or project_id not in projects
                                   or team_id not in projects[project_id]["team_ids"]):
        raise FilingError("OpenCode selected a project unavailable to that team.")
    if "stateId" in changes and changes["stateId"] not in [state["id"] for state in team.get("states", [])]:
        raise FilingError("OpenCode selected a status unavailable to that team.")
    add, remove = changes.get("addedLabelIds", []), changes.get("removedLabelIds", [])
    if (not valid_ids(add) or not valid_ids(remove) or set(add) & set(remove)
            or any(label_id not in labels or labels[label_id]["team_id"] not in (None, team_id)
                   for label_id in add + remove)):
        raise FilingError("OpenCode selected invalid labels for the issue's team.")
    try:
        validate_metadata(changes, estimates=team.get("estimates", []))
        if changes.get("dueDate") is not None and date.fromisoformat(changes["dueDate"]).isoformat() != changes["dueDate"]:
            raise ValueError("Expected a calendar date")
    except (ValueError, TypeError, OverflowError) as error:
        raise FilingError("OpenCode returned an invalid priority, estimate, or due date.") from error
    if "estimate" in changes:
        changes["estimate"] = int(changes["estimate"])
    return IssueDraft({"id": current["id"], **changes}, draft["blocked_by"], draft["blocks"], draft["attach_image"], current)


def read_dependencies(linear, ids):
    refs = {}
    for start in range(0, len(ids), 100):
        data = linear.query("query($ids: [ID!]!) { issues(first: 100, includeArchived: true, filter: {id: {in: $ids}}) { "
                            "nodes { id identifier trashed } } }", {"ids": ids[start:start + 100]})["issues"]
        refs.update((item["id"], item["identifier"]) for item in data["nodes"] if not item["trashed"])
    if set(refs) != set(ids):
        raise ReplyNeeded("A referenced dependency is unavailable. What is its Linear issue identifier?")
    return refs


def create_issue(linear, fields):
    for attempt in range(2):
        try:
            return linear.create_issue(fields)
        except RequestError as error:
            if attempt or not error.retryable:
                raise
            time.sleep(1)


def update_matches(issue, issue_id, changes):
    if not issue or issue["id"] != issue_id or issue["trashed"]:
        return False
    labels = set(issue["labelIds"])
    if not labels.issuperset(changes.get("addedLabelIds", [])) or not labels.isdisjoint(changes.get("removedLabelIds", [])):
        return False
    observed = {**issue, "projectId": (issue["project"] or {}).get("id"), "stateId": (issue["state"] or {}).get("id")}
    return all(observed[key] == value for key, value in changes.items() if key not in {"addedLabelIds", "removedLabelIds"})


def update_issue(linear, current, fields):
    issue_id = current["id"]
    changes = {key: value for key, value in fields.items() if key != "id"}
    for attempt in range(2):
        latest = read_update_issue(linear, issue_id)
        if update_matches(latest, issue_id, changes):
            return latest
        if not latest or latest["trashed"] or latest["updatedAt"] != current["updatedAt"]:
            raise FilingError(f"{current['identifier']} changed before the update could be saved. Dictate the changes again.")
        try:
            saved = linear.query(f"""mutation($id: String!, $input: IssueUpdateInput!) {{
              issueUpdate(id: $id, input: $input) {{ success issue {{ {UPDATE_SELECTION} }} }}
            }}""", {"id": issue_id, "input": changes})["issueUpdate"]["issue"]
        except RequestError as error:
            if not attempt and error.retryable:
                time.sleep(1)
                continue
        else:
            if update_matches(saved, issue_id, changes):
                return saved
        verified = read_update_issue(linear, issue_id)
        if update_matches(verified, issue_id, changes):
            return verified
        raise RequestError()


def unlinked_dependencies(linear, issue_id, refs, *, incoming):
    if not refs:
        return []
    if incoming:
        linked = {relation["issue"]["id"] for relation in linear.issue_dependencies(issue_id)
                  if relation["type"] == "blocks"}
    else:
        linked, cursor = set(), None
        while True:
            connection = linear.query("""query($id: String!, $after: String) {
              issue(id: $id) { relations(first: 50, after: $after, includeArchived: true) {
                nodes { type relatedIssue { id } } pageInfo { hasNextPage endCursor }
              } }
            }""", {"id": issue_id, "after": cursor})["issue"]["relations"]
            linked.update(relation["relatedIssue"]["id"] for relation in connection["nodes"]
                          if relation["type"] == "blocks")
            cursor = next_cursor(connection, cursor)
            if not cursor or linked.issuperset(refs):
                break
    return [ref for ref in refs if ref not in linked]


def add_dependencies(linear, issue_id, blocked_by, blocks, refs):
    edges = [(ref, issue_id) for ref in blocked_by] + [(issue_id, ref) for ref in blocks]
    if not edges:
        return []
    variables = {f"r{index}": {"id": str(uuid4()), "type": "blocks",
                                "issueId": source, "relatedIssueId": target}
                 for index, (source, target) in enumerate(edges)}
    verified = set()
    for _ in range(2):
        pending = {key: value for key, value in variables.items() if key not in verified}
        if not pending:
            break
        declarations = ", ".join(f"${key}: IssueRelationCreateInput!" for key in pending)
        statements = "\n".join(f"{key}: issueRelationCreate(input: ${key}) {{ success "
                               "issueRelation { type issue { id } relatedIssue { id } } }" for key in pending)
        try:
            result = linear.query(f"mutation({declarations}) {{ {statements} }}", pending)
        except RequestError:
            result = {}
        for key, value in pending.items():
            relation = (result.get(key) or {}).get("issueRelation")
            if not relation:
                # A lost response can hide a successful write. Check its id before a retry.
                try:
                    relation = linear.query("""query($id: String!) { issueRelation(id: $id) {
                      type issue { id } relatedIssue { id }
                    } }""", {"id": value["id"]}).get("issueRelation")
                except RequestError:
                    continue
            if (relation and relation.get("type") == "blocks" and (relation.get("issue") or {}).get("id") == value["issueId"]
                    and (relation.get("relatedIssue") or {}).get("id") == value["relatedIssueId"]):
                verified.add(key)
    return [refs[target if source == issue_id else source]
            for index, (source, target) in enumerate(edges) if f"r{index}" not in verified]


def upload_image(linear, image):
    mime, data = image
    try:
        upload = linear.query("""mutation($type: String!, $name: String!, $size: Int!) {
          fileUpload(contentType: $type, filename: $name, size: $size, makePublic: false) {
            success uploadFile { uploadUrl assetUrl headers { key value } }
          }
        }""", {"type": mime, "name": f"clipboard.{EXTENSIONS[mime]}", "size": len(data)})["fileUpload"]["uploadFile"]
        destination = urlsplit(upload["uploadUrl"])
        asset = urlsplit(upload["assetUrl"])
        if (destination.scheme != "https" or not destination.hostname or destination.port not in (None, 443)
                or destination.username is not None or destination.password is not None
                or asset.scheme != "https" or asset.netloc != "uploads.linear.app" or not asset.path.startswith("/")):
            raise ValueError("Unexpected Linear upload URL")
        headers = {"Content-Type": mime, "Cache-Control": "public, max-age=31536000"}
        headers.update({header["key"]: header["value"] for header in upload["headers"]})
        path = destination.path or "/"
        if destination.query:
            path += "?" + destination.query
        connection = HTTPSConnection(destination.hostname, timeout=20)
        try:
            connection.request("PUT", path, body=data, headers=headers)
            if not 200 <= connection.getresponse().status < 300:
                raise RequestError()
        finally:
            connection.close()
        return upload["assetUrl"]
    except (OSError, ValueError, RequestError, HTTPException, TypeError, KeyError) as error:
        raise FilingError("Linear could not upload the clipboard image.") from error


def file_dictation(dictation, *, dry_run=False):
    if dictation.image_use and not dictation.image:
        raise ReplyNeeded(IMAGE_QUESTION, copy_image=True)
    linear = configured_client()
    catalog = read_catalog(linear)
    reply = opencode_draft(dictation, catalog)
    if dictation.image:
        if not isinstance(reply, dict) or reply.get("image_relevant") is not True:
            raise ReplyNeeded("The clipboard image did not clearly match the request. " + IMAGE_QUESTION, copy_image=True)
        reply = {key: value for key, value in reply.items() if key != "image_relevant"}
    elif isinstance(reply, dict) and reply.get("image_relevant") is False:
        raise ReplyNeeded(IMAGE_QUESTION, copy_image=True)
    if isinstance(reply, dict) and set(reply) == {"clarification"} and isinstance(reply["clarification"], str):
        reason = reply["clarification"].strip()
        if reason:
            raise ReplyNeeded(reason[:350])
    if (not isinstance(reply, dict) or set(reply) != {"issues"}
            or not isinstance(reply["issues"], list) or not reply["issues"]):
        raise FilingError("OpenCode did not return any issue drafts.")
    drafts = []
    for index, item in enumerate(reply["issues"]):
        if isinstance(item, dict) and "issue_id" in item:
            draft = validate_update(linear, item, catalog)
        else:
            draft = validate_issue(item, catalog, str(uuid4()) if index else dictation.issue_id)
        drafts.append(draft)
    targets = [draft.fields["id"] for draft in drafts if draft.existing]
    if len(set(targets)) != len(targets):
        raise FilingError("OpenCode returned more than one update for the same issue.")
    attaching = any(draft.attach_image for draft in drafts)
    if attaching and dictation.image_use != "attach":
        raise FilingError("OpenCode requested an attachment without an instruction to attach an image.")
    if dictation.image_use == "attach" and not attaching:
        raise ReplyNeeded("Which issue should include the clipboard image?")

    ids = list(dict.fromkeys(ref for draft in drafts for ref in draft.blocked_by + draft.blocks))
    refs = read_dependencies(linear, ids)
    asset = upload_image(linear, dictation.image) if attaching and not dry_run else None
    issues, unconfirmed = [], []
    for draft in drafts:
        fields, current = draft.fields.copy(), draft.existing
        blocked_by, blocks = draft.blocked_by, draft.blocks
        action = "Updated" if current else "Created"
        dependencies = {"blocked_by": [refs[value] for value in blocked_by], "blocks": [refs[value] for value in blocks]}
        if dry_run:
            target = {"identifier": current["identifier"]} if current else {}
            issues.append({"action": action, "fields": fields, "attach_image": draft.attach_image, **target, **dependencies})
            continue
        if draft.attach_image:
            description = fields.get("description", (current or {}).get("description"))
            image_link = f"![Clipboard image](<{asset}>)"
            fields["description"] = f"{description}\n\n{image_link}" if description else image_link
        try:
            saved = update_issue(linear, current, fields) if current else create_issue(linear, fields)
            identifier, url = saved["identifier"], saved.get("url")
        except FilingError as error:
            unconfirmed.append(str(error))
            continue
        except (RequestError, ValueError, KeyError, TypeError) as error:
            print(f"Hex Linear could not confirm one issue ({type(error).__name__}).")
            unconfirmed.append(current["identifier"] if current else fields["title"])
            continue
        try:
            if current:
                blocked_by = unlinked_dependencies(linear, fields["id"], blocked_by, incoming=True)
                blocks = unlinked_dependencies(linear, fields["id"], blocks, incoming=False)
            missing = add_dependencies(linear, fields["id"], blocked_by, blocks, refs)
        except (RequestError, ValueError, KeyError, TypeError):
            missing = dependencies["blocked_by"] + dependencies["blocks"]
        if not isinstance(url, str) or not url.startswith("https://linear.app/"):
            url = None
        issues.append({"action": action, "identifier": identifier, "url": url, "unverified": missing, **dependencies})
    if dry_run:
        return {"issues": issues, "clipboard_image": bool(dictation.image)}
    return {"issues": issues, "unconfirmed": unconfirmed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True, help="Print validated drafts from stdin")
    args = parser.parse_args()
    transcript = sys.stdin.read().strip()
    if not transcript:
        parser.error("Pass a dictation transcript on stdin")
    try:
        print(json.dumps(file_dictation(Dictation(transcript, None, str(uuid4())), dry_run=args.dry_run), indent=2))
    except FilingError as error:
        print(error, file=sys.stderr)
        return 1
    except (OSError, ValueError, RequestError, subprocess.SubprocessError) as error:
        print(f"Issue dry run failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
