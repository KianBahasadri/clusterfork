"""Read-only Linear issue context and authenticated file access for the web UI."""

import asyncio
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from emoji import emojize, is_emoji
import mistune

from .services import ISSUE_ID


USER = "name displayName avatarUrl url"
REFERENCE = "identifier title url state { name type color }"
REACTIONS = "emoji user { name } externalUser { name }"
CONNECTIONS = {
    "labels": "name color",
    "subscribers": USER,
    "children": REFERENCE,
    "relations": "type issue { " + REFERENCE + " } relatedIssue { " + REFERENCE + " }",
    "inverseRelations": "type issue { " + REFERENCE + " } relatedIssue { " + REFERENCE + " }",
    "attachments": """id title subtitle url createdAt
        creator { name } externalUserCreator { name }""",
    "documents": "id title url content",
    "releases": "name version url",
    "comments": """id body createdAt updatedAt editedAt parentId quotedText resolvedAt url
        user { """ + USER + """ } externalUser { name } botActor { name }
        resolvingUser { name } reactions { """ + REACTIONS + " }",
    "history": """id createdAt actor { name } botActor { name }
        updatedDescription fromTitle toTitle
        fromAssignee { name } toAssignee { name }
        fromDelegate { name } toDelegate { name }
        fromPriority toPriority fromEstimate toEstimate
        fromTeam { name } toTeam { name }
        fromParent { identifier title } toParent { identifier title }
        fromState { name } toState { name }
        fromCycle { name number } toCycle { name number }
        fromProject { name } toProject { name }
        fromProjectMilestone { name } toProjectMilestone { name }
        fromDueDate toDueDate archived trashed autoClosed autoArchived
        fromSlaStartedAt toSlaStartedAt fromSlaBreachesAt toSlaBreachesAt fromSlaType toSlaType
        attachment { title url } addedLabels { name color } removedLabels { name color }
        relationChanges { identifier type }""",
}
STATE_TYPE_ORDER = {"triage": 0, "backlog": 1, "unstarted": 2, "started": 3, "completed": 4, "canceled": 5}
FIELDS = """id identifier title url description priority priorityLabel estimate dueDate
    createdAt updatedAt startedAt completedAt canceledAt archivedAt snoozedUntilAt
    slaStartedAt slaBreachesAt slaType branchName previousIdentifiers
    state { id name type color }
    team { id name key states(first: 100) { nodes { id name type color position archivedAt } } }
    creator { """ + USER + " } assignee { " + USER + " } delegate { " + USER + """ }
    project { id name icon url }
    cycle { name number startsAt endsAt }
    projectMilestone { name targetDate description }
    parent { """ + REFERENCE + " } reactions { " + REACTIONS + " }"


def asset_url(url):
    if url.startswith("https://uploads.linear.app/"):
        return "/api/linear-asset?" + urlencode({"url": url})
    return url


class IssueMarkdown(mistune.HTMLRenderer):
    def image(self, text, url, title=None):
        return super().image(text, asset_url(url), title)

    def link(self, text, url, title=None):
        return super().link(text, asset_url(url), title)

    def table_cell(self, text, align=None, head=False):
        tag = "th" if head else "td"
        attrs = self.render_attrs({"class": "table-align-" + align}) if align else ""
        return f"<{tag}{attrs}>{text}</{tag}>\n"


markdown = mistune.create_markdown(
    renderer=IssueMarkdown(escape=True), plugins=["table", "strikethrough", "task_lists", "url"],
)


async def issue_details(linear, identifier):
    if not ISSUE_ID.fullmatch(identifier):
        raise ValueError("Use a Linear issue ID such as KIAN-1077.")
    collections = "\n".join(
        name + "(first: 50) { nodes { " + fields + " } pageInfo { endCursor hasNextPage } }"
        for name, fields in CONNECTIONS.items()
    )
    data = await linear.query(
        "query($id: String!) { issue(id: $id) { " + FIELDS + collections + " } }", id=identifier,
    )
    issue = data["issue"]
    if issue is None:
        raise ValueError(f"Linear issue {identifier} is unavailable.")

    async def collect(name, fields):
        connection = issue[name]
        nodes = list(connection["nodes"])
        while connection["pageInfo"]["hasNextPage"]:
            page = await linear.query(
                "query($id: String!, $after: String!) { issue(id: $id) { " + name
                + "(first: 50, after: $after) { nodes { " + fields
                + " } pageInfo { endCursor hasNextPage } } } }",
                id=identifier, after=connection["pageInfo"]["endCursor"],
            )
            connection = page["issue"][name]
            nodes.extend(connection["nodes"])
        issue[name] = nodes

    await asyncio.gather(*(collect(name, fields) for name, fields in CONNECTIONS.items()))
    issue["descriptionHtml"] = markdown(issue["description"] or "")
    for comment in issue["comments"]:
        comment["bodyHtml"] = markdown(comment["body"] or "")
    for document in issue["documents"]:
        document["contentHtml"] = markdown(document["content"] or "")
    for attachment in issue["attachments"]:
        attachment["url"] = asset_url(attachment["url"])
    if issue["project"]:
        glyph = emojize(issue["project"]["icon"] or "", language="alias")
        issue["project"]["icon"] = glyph if is_emoji(glyph) else ""
    team = issue.get("team")
    if isinstance(team, dict):
        raw_states = (team.get("states") or {}).get("nodes") if isinstance(team.get("states"), dict) else team.get("states")
        states = []
        if isinstance(raw_states, list):
            for node in raw_states:
                if not isinstance(node, dict) or node.get("archivedAt"):
                    continue
                if isinstance(node.get("id"), str) and isinstance(node.get("name"), str):
                    states.append({
                        "id": node["id"],
                        "name": node["name"],
                        "type": node.get("type") or "",
                        "color": node.get("color") or "",
                        "position": node.get("position") if isinstance(node.get("position"), (int, float)) else 0,
                    })
            states.sort(key=lambda item: (STATE_TYPE_ORDER.get(item["type"], 99), item["position"], item["name"]))
        team["states"] = states
    return issue


class AssetRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Linear files must use HTTPS.")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected:
            redirected.remove_header("Authorization")
        return redirected


def open_asset(token, url, byte_range=None):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "uploads.linear.app"
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError("Expected a Linear upload URL.")
    headers = {"Authorization": token}
    if byte_range:
        headers["Range"] = byte_range
    return build_opener(AssetRedirect()).open(Request(url, headers=headers), timeout=20)
