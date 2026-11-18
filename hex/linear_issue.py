"""Draft one Linear issue with OpenCode and save it through Clusterfork's client."""

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
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
IMAGE_QUESTION = "Copy an image that belongs with this issue, then click Reply."
ISSUE_KEYS = {"title", "description", "project_id", "team_id", "priority", "estimate",
              "label_ids", "blocked_by", "blocks"}

CATALOG_FIELDS = {
    "projects": ("first: 50", "id name slugId description status { type } "
                 "teams(first: 30) { nodes { id } pageInfo { hasNextPage endCursor } }"),
    "teams": ("first: 50", "id name key parent { id } inheritIssueEstimation "
              "issueEstimationType issueEstimationExtended issueEstimationAllowZero"),
    "issueLabels": ("first: 100", "id name description isGroup team { id } parent { name }"),
    "workflowStates": ('first: 50, filter: {type: {eq: "backlog"}}', "id name team { id }"),
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
    wants_image: bool = False
    image: tuple[str, bytes] | None = None


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
        defaults = [state for state in states if state["team"]["id"] == team_id]
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
                          "estimates": estimates, "backlog": backlog["id"] if backlog else None}

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


def validate_draft(draft, catalog, issue_id):
    if not isinstance(draft, dict) or set(draft) != {"issue"} or not isinstance(draft["issue"], dict):
        raise FilingError("OpenCode did not return an issue draft.")
    issue = draft["issue"]
    if (set(issue) != ISSUE_KEYS or not isinstance(issue["title"], str) or not issue["title"].strip()
            or not isinstance(issue["description"], str) or not issue["description"].strip()
            or not all(valid_ids(issue[key]) for key in ("label_ids", "blocked_by", "blocks"))):
        raise FilingError("OpenCode returned incomplete issue fields.")
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
        referenced = issue["blocked_by"] + issue["blocks"]
        if any(str(UUID(value)) != value or value == issue_id for value in referenced) or len(set(referenced)) != len(referenced):
            raise ValueError("Invalid dependency IDs")
    except (ValueError, OverflowError) as error:
        raise FilingError("OpenCode returned an invalid priority, estimate, or dependency.") from error
    fields = {"id": issue_id, "title": issue["title"].strip(), "description": issue["description"].strip(),
              "projectId": project_id, "teamId": team_id, "stateId": team["backlog"],
              "labelIds": issue["label_ids"], "priority": issue["priority"], "estimate": int(issue["estimate"])}
    return fields, issue["blocked_by"], issue["blocks"]


def read_dependencies(linear, ids):
    if not ids:
        return {}
    data = linear.query("query($ids: [ID!]!) { issues(first: 100, includeArchived: true, filter: {id: {in: $ids}}) { "
                        "nodes { id identifier trashed } pageInfo { hasNextPage } } }", {"ids": ids})["issues"]
    refs = {item["id"]: item["identifier"] for item in data["nodes"] if not item["trashed"]}
    if data["pageInfo"]["hasNextPage"] or set(refs) != set(ids):
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
                    } }""", {"id": value["id"]})["issueRelation"]
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
    if dictation.wants_image and not dictation.image:
        raise ReplyNeeded(IMAGE_QUESTION, copy_image=True)
    linear = configured_client()
    catalog = read_catalog(linear)
    reply = opencode_draft(dictation, catalog)
    if dictation.image:
        if not isinstance(reply, dict) or reply.get("image_relevant") is not True:
            raise ReplyNeeded("The clipboard image did not clearly match the issue. " + IMAGE_QUESTION, copy_image=True)
        reply = {key: value for key, value in reply.items() if key != "image_relevant"}
    if isinstance(reply, dict) and set(reply) == {"clarification"} and isinstance(reply["clarification"], str):
        reason = reply["clarification"].strip()
        if reason:
            raise ReplyNeeded(reason[:350])
    issue_id = dictation.issue_id
    fields, blocked_by, blocks = validate_draft(reply, catalog, issue_id)
    refs = read_dependencies(linear, blocked_by + blocks)
    dependencies = {"blocked_by": [refs[value] for value in blocked_by], "blocks": [refs[value] for value in blocks]}
    if dry_run:
        return {"fields": fields, "clipboard_image": bool(dictation.image), **dependencies}

    if dictation.image:
        url = upload_image(linear, dictation.image)
        fields["description"] += f"\n\n![Clipboard image](<{url}>)"
    issue = create_issue(linear, fields)
    missing = add_dependencies(linear, issue_id, blocked_by, blocks, refs)
    url = urlsplit(issue["url"])
    if url.scheme != "https" or url.netloc != "linear.app" or not url.path.startswith("/"):
        raise FilingError("The issue was filed, but Linear returned an unexpected link.")
    return {"identifier": issue["identifier"], "url": issue["url"], "unverified": missing, **dependencies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True, help="Print a validated draft from stdin")
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
