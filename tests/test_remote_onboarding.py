"""Explicit enrollment, idempotent configuration, and shared caller authorization."""

import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation")]
import onboard
from common.policy import PROTOCOL, digest
from common.accounts import WORKER_REPOSITORY
from common.clients import Linear
from call_budget import CallBudget, record_api_calls

PROJECT = "11111111-1111-4111-8111-111111111111"
TEAM = "22222222-2222-4222-8222-222222222222"
LABEL = "33333333-3333-4333-8333-333333333333"
PIN = "c" * 40
REPO = "example/widget"
BRANCH = "trunk"
CHECK = "Repository tests"


def original_registry():
    return json.loads((ROOT / "automation/controller/projects.json").read_text())


def states():
    row = original_registry()["projects"][0]
    return {"ready": row["ready_state_ids"], **row["linear_states"]}


def configured(data=None):
    return onboard.prepare_configuration(data or original_registry(), PROJECT, REPO, BRANCH, TEAM,
                                         states(), [["python3", "-m", "unittest", "discover"]], [CHECK])


class ConfigurationTests(unittest.TestCase):
    def test_repository_guidance_preserves_existing_text_and_symlinks_on_repeated_setup(self):
        for layout in ("new", "agents", "claude", "symlink"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                canonical = root / ("CLAUDE.md" if layout == "claude" else "AGENTS.md")
                if layout != "new":
                    canonical.write_text("# Project instructions\n\nKeep the existing CI and documentation rules.\n")
                if layout == "symlink":
                    (root / "CLAUDE.md").symlink_to("AGENTS.md")
                registry = root / "registry.json"
                initial = original_registry()
                registry.write_text(json.dumps(initial))
                preview = onboard.configure(root, registry, initial, PROJECT, configured(initial))
                self.assertIn(str(canonical), preview)
                self.assertEqual(canonical.exists(), layout != "new")
                onboard.configure(root, registry, initial, PROJECT, configured(initial), apply=True)
                text = canonical.read_text()
                if layout != "new":
                    self.assertTrue(text.startswith("# Project instructions\n\nKeep the existing CI and documentation rules.\n"))
                for requirement in ("explicit priority", "estimate", "configured estimate scale", "follow-up", "preserve", "human review"):
                    self.assertIn(requirement, text)
                self.assertEqual(text.count(onboard.INSTRUCTIONS_START), 1)
                before = canonical.stat().st_mtime_ns
                self.assertEqual(onboard.configure(root, registry, configured(initial), PROJECT, configured(initial), apply=True), [])
                self.assertEqual(canonical.stat().st_mtime_ns, before)
                if layout == "symlink":
                    self.assertTrue((root / "CLAUDE.md").is_symlink())
                if layout == "claude":
                    self.assertFalse((root / "AGENTS.md").exists())

    def test_guidance_can_follow_the_repository_documentation_pattern_and_keep_its_link_on_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("# Rules\n\nDetailed policy belongs in docs.\n")
            topic = root / "docs/development.md"
            topic.parent.mkdir()
            topic.write_text("# Development\n\nRun the existing tests.\n")
            for path, content, _ in onboard.issue_instruction_changes(root, Path("docs/development.md")):
                path.write_text(content)
            self.assertIn("Symphony issue instructions](<docs/development.md>)", (root / "AGENTS.md").read_text())
            self.assertNotIn("explicit priority", (root / "AGENTS.md").read_text())
            self.assertTrue(topic.read_text().startswith("# Development\n\nRun the existing tests.\n"))
            self.assertIn("explicit priority", topic.read_text())
            self.assertEqual(onboard.issue_instruction_changes(root), [])

    def test_symlinked_root_guidance_keeps_relative_topic_links_discoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "docs/agents.md"
            canonical.parent.mkdir()
            canonical.write_text("# Existing rules\n")
            (root / "AGENTS.md").symlink_to("docs/agents.md")
            for path, content, _ in onboard.issue_instruction_changes(root, Path("docs/development.md")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            self.assertIn("Symphony issue instructions](<development.md>)", canonical.read_text())
            self.assertTrue((root / "AGENTS.md").is_symlink())
            self.assertEqual(onboard.issue_instruction_changes(root), [])

    def test_unsafe_or_malformed_guidance_never_causes_partial_configuration_writes(self):
        for content in (onboard.INSTRUCTIONS_START, onboard.INSTRUCTIONS_END,
                        onboard.INSTRUCTIONS_END + onboard.INSTRUCTIONS_START,
                        (onboard.INSTRUCTIONS_START + onboard.INSTRUCTIONS_END) * 2):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "AGENTS.md").write_text(content)
                path = root / "registry.json"
                initial = original_registry()
                path.write_text(json.dumps(initial))
                with self.assertRaises(onboard.SetupError):
                    onboard.configure(root, path, initial, PROJECT, configured(initial), install_ci=True, apply=True)
                self.assertEqual(json.loads(path.read_text()), initial)
                self.assertFalse((root / ".github").exists())
                self.assertEqual((root / "AGENTS.md").read_text(), content)
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            external = Path(outside) / "AGENTS.md"
            external.write_text("External rules stay intact.")
            (root / "AGENTS.md").symlink_to(external)
            with self.assertRaises(onboard.SetupError):
                onboard.issue_instruction_changes(root)
            self.assertEqual(external.read_text(), "External rules stay intact.")

    def test_central_configuration_writes_mapping_and_ci_without_a_target_worker_workflow(self):
        initial=original_registry()
        updated=onboard.prepare_configuration(initial,PROJECT,REPO,BRANCH,TEAM,states(),
                                              [['python3','-m','unittest','discover']],[CHECK])
        row=updated['projects'][-1]
        self.assertEqual(row['workflow'],'clusterfork-task.yml')
        self.assertNotIn('worker_revision',row)
        self.assertNotIn('blocked', row['linear_states'])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            registry=root/'registry.json'
            registry.write_text(json.dumps(initial))
            existing=root/'.github/workflows/clusterfork-worker.yml'
            existing.parent.mkdir(parents=True)
            existing.write_text('Existing user workflow stays intact.')
            changes=onboard.configure(root,registry,initial,PROJECT,updated,install_ci=True,apply=True)
            self.assertEqual(len(changes),3)
            self.assertEqual(existing.read_text(),'Existing user workflow stays intact.')
            self.assertEqual(onboard.configure(root,registry,updated,PROJECT,updated,install_ci=True,apply=True),[])
        for field in ('validation_commands','required_checks','linear_states'):
            bad=copy.deepcopy(updated)
            bad['projects'][-1].pop(field)
            with self.assertRaises(ValueError):
                onboard.validate_registry(bad)

    def test_different_names_map_by_uuid_and_second_invocation_writes_nothing(self):
        initial = original_registry()
        initial["operator_metadata"] = {"preserve": True}
        updated = configured(initial)
        self.assertEqual(updated["generation"], initial["generation"])
        self.assertEqual(updated["projects"][0], initial["projects"][0])
        self.assertEqual(updated["agent_selection"], initial["agent_selection"])
        self.assertEqual(updated["operator_metadata"], initial["operator_metadata"])
        row = updated["projects"][-1]
        self.assertEqual((row["project_id"], row["repository"]), (PROJECT, REPO))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "registry.json"
            path.write_text(json.dumps(initial))
            self.assertEqual(len(onboard.configure(root, path, initial, PROJECT, updated, install_ci=True)), 3)
            self.assertEqual(json.loads(path.read_text()), initial)
            onboard.configure(root, path, initial, PROJECT, updated, install_ci=True, apply=True)
            files = [path, root / "AGENTS.md", *root.glob(".github/workflows/*.yml")]
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
            second = configured(updated)
            self.assertEqual(onboard.configure(root, path, updated, PROJECT, second, install_ci=True, apply=True), [])
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})
            self.assertEqual(len(second["projects"]), len(initial["projects"]) + 1)
            self.assertFalse((root / ".github/workflows/clusterfork-worker.yml").exists())


    def test_conflicts_do_not_overwrite_mapping_workflow_or_unrelated_ci(self):
        initial = original_registry()
        updated = configured(initial)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "registry.json"
            path.write_text(json.dumps(initial))
            worker = root / ".github/workflows" / onboard.CI_WORKFLOW
            worker.parent.mkdir(parents=True)
            worker.write_text("existing user workflow\n")
            ci = worker.with_name("test.yml")
            ci.write_text("existing CI\n")
            before = path.read_bytes()
            with self.assertRaisesRegex(onboard.SetupError, "Preserve"):
                onboard.configure(root, path, initial, PROJECT, updated, install_ci=True, apply=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(worker.read_text(), "existing user workflow\n")
            self.assertEqual(ci.read_text(), "existing CI\n")
        changed = copy.deepcopy(updated)
        changed["projects"][-1]["repository"] = "other/repository"
        with self.assertRaisesRegex(onboard.SetupError, "another repository"):
            configured(changed)
        duplicate = copy.deepcopy(updated)
        duplicate["projects"].append(copy.deepcopy(duplicate["projects"][-1]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            configured(duplicate)


    def test_repo_identity_comes_from_checkout_origin_not_project_name(self):
        for remote in ("git@github.com:example/widget.git", "https://github.com/example/widget.git",
                       "ssh://git@github.com/example/widget"):
            with self.subTest(remote=remote), patch.object(onboard, "command", return_value=remote), \
                 patch.object(onboard, "gh", return_value={"full_name": REPO, "default_branch": BRANCH}):
                self.assertEqual(onboard.repository(Path("/checkout")), (REPO, BRANCH))



class MergeTests(unittest.TestCase):
    def test_merge_adds_protection_and_updates_only_merge_flags_then_becomes_noop(self):
        row = configured()["projects"][-1]
        info = {"allow_auto_merge": False, "allow_squash_merge": False, "allow_merge_commit": True}
        rules = []
        mutations = []

        def api(path, *, method="GET", data=None):
            if method == "POST":
                mutations.append((path, copy.deepcopy(data)))
                rules.append({"type": "required_status_checks", "parameters": data["rules"][0]["parameters"]})
                return {"id": 42}
            if method == "PATCH":
                mutations.append((path, data))
                info.update(data)
                return info
            return info

        with patch.object(onboard, "gh", side_effect=api), \
             patch.object(onboard, "effective_checks", side_effect=lambda *a: {"Existing check"} | {
                 check["context"] for rule in rules for check in rule["parameters"]["required_status_checks"]}), \
             patch.object(onboard, "gh_pages", return_value=iter([])):
            onboard.configure_merge(row, apply=True)
            self.assertEqual(onboard.configure_merge(row, apply=True), [])
        self.assertEqual(len(mutations), 2)
        body = mutations[0][1]
        self.assertEqual(body["bypass_actors"], [])
        self.assertEqual(body["conditions"]["ref_name"]["include"], ["refs/heads/trunk"])
        self.assertEqual(body["rules"][0]["parameters"]["required_status_checks"], [{"context": CHECK}])
        self.assertIs(info["allow_merge_commit"], True)
        self.assertIs(info['allow_auto_merge'], False)
        self.assertEqual(mutations[1][1], {'allow_squash_merge': True})
        self.assertNotIn("allow_merge_commit", mutations[1][1])

    def test_existing_owned_ruleset_retains_all_rules_and_required_checks(self):
        row = configured()["projects"][-1]
        owned = {"id": 42, "name": "clusterfork-remote-development:trunk", "target": "branch",
                 "conditions": {"ref_name": {"include": ["refs/heads/trunk"], "exclude": []}},
                 "bypass_actors": [], "rules": [{"type": "non_fast_forward"},
                     {"type": "required_status_checks", "parameters": {
                         "required_status_checks": [{"context": "Older gate", "integration_id": 7}],
                         "strict_required_status_checks_policy": True}}]}
        writes = []

        def api(path, *, method="GET", data=None):
            if method == "PUT":
                writes.append(data)
            return owned if path.endswith('/42') else {"allow_auto_merge": True, "allow_squash_merge": True}

        with patch.object(onboard, "gh", side_effect=api), patch.object(onboard, "effective_checks", return_value=set()), \
             patch.object(onboard, "gh_pages", return_value=iter([owned])):
            onboard.configure_merge(row, apply=True)
        self.assertEqual(writes[0]["rules"][0], {"type": "non_fast_forward"})
        self.assertIn({"context": "Older gate", "integration_id": 7}, writes[0]["rules"][1]["parameters"]["required_status_checks"])
        self.assertEqual(owned["rules"][1]["parameters"]["required_status_checks"], [{"context": "Older gate", "integration_id": 7}])






class FakeLinear(Linear):
    def __init__(self):
        self.labels = [{"id": "unrelated", "name": "Keep this"}]
        self.mutations = []
        self.state_rows = [{"id": s, "name": "Todo", "type": "unstarted"} for s in states()["ready"]]
        self.state_rows += [{"id": states()[key], "name": key, "type": kind}
                            for key, kind in (("in_progress", "started"), ("done", "completed"))]
        self.issue_labels = [{"name": name, "parent": None, "isGroup": False} for name in
                             set(onboard.STAGE_LABELS.values()) | {"clusterfork: queued", "clusterfork: selected",
                                                                  "clusterfork: auto-repair"}]

    def query(self, query, variables):
        connection = lambda nodes: {"nodes": copy.deepcopy(nodes), "pageInfo": {"hasNextPage": False}}
        if "issueEstimationType" in query:
            return {"team": {"issueEstimationType": "fibonacci", "issueEstimationExtended": False,
                             "issueEstimationAllowZero": False, "inheritIssueEstimation": False}}
        if "projectAddLabel" in query:
            self.mutations.append(variables)
            self.labels.append({"id": LABEL, "name": "clusterfork-symphony"})
            return {"projectAddLabel": {"success": True}}
        if "projectLabels(" in query:
            return {"projectLabels": connection([{"id": LABEL, "name": "clusterfork-symphony", "parent": None,
                                                   "team": None, "isGroup": False, "retiredAt": None}])}
        if "issueLabels(" in query:
            return {"issueLabels": connection(self.issue_labels)}
        if "states(" in query:
            return {"team": {"states": connection(self.state_rows)}}
        if "labels(" in query:
            return {"project": {"labels": connection(self.labels)}}
        return {"project": {"id": PROJECT, "name": "An entirely different project name", "archivedAt": None,
                            "teams": connection([{"id": TEAM, "name": "Team"}])}}

    def project_issues(self, project_id):
        for number, state, archived in ((1, states()["ready"][0], False), (2, states()["done"], False),
                                        (3, states()["ready"][0], True)):
            yield {"id": f"55555555-5555-4555-8555-{number:012d}", "identifier": f"OTHER-{number}",
                   "title": f"Issue {number}", "state": {"id": state}, "archivedAt": "now" if archived else None,
                   "project": {"id": PROJECT, "archivedAt": None, "labels": {"nodes": copy.deepcopy(self.labels)}}}


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.checkout = Path(self.temporary.name)
        self.path = self.checkout / "registry.json"
        self.data = configured()
        self.path.write_text(json.dumps(self.data))
        for path, content, _ in onboard.issue_instruction_changes(self.checkout):
            path.write_text(content)
        self.published_guidance = {"AGENTS.md": (self.checkout / "AGENTS.md").read_bytes()}
        self.linear = record_api_calls(FakeLinear())
        self.health = {"missing": [], "deployment_paused": False, "serial_admission": True, "mode":"single-controller", "phase_isolation":True,
                       "registry_digest": digest(self.data), "protocol": PROTOCOL, "deployment": {"revision":PIN,"digest": "deployed-source"},
                       "repository_setup": {"project_id": PROJECT, "repository": REPO, "ref": BRANCH, "app_access": True,
                                            "permissions": {key: "write" for key in ("actions", "contents", "pull_requests", "workflows")},
                                            "workflow_active": True,
                                            "branch_sha": "b" * 40,
                                            "workflow_repository":WORKER_REPOSITORY,"workflow":"clusterfork-task.yml",
                                            "runtime_sha":PIN,"runtime_tag_verified":True,"workflow_verified":True}}
        self.check_runs = [{"id": 2, "name": CHECK, "conclusion": "success"}]
        self.statuses = []
        for target, replacement in (("repository", Mock(return_value=(REPO, BRANCH))),
                                    ("gh", Mock(side_effect=self.github)), ("gh_pages", Mock(side_effect=self.pages))):
            patcher = patch.object(onboard, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.object(onboard, "controller_health", side_effect=lambda *a,**kw: copy.deepcopy(self.health)),
                        patch.object(onboard,"local_manifest",return_value={"digest":"deployed-source"}),
                        patch.object(onboard,"command",side_effect=lambda args,**kw: "" if "status" in args else PIN)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def github(self, path, **kwargs):
        if "/contents/" in path:
            relative = unquote(path.split("/contents/", 1)[1].split("?", 1)[0])
            content = self.published_guidance.get(relative)
            if content is None:
                raise onboard.GitHubAccessError(path, 404)
            return {"encoding": "base64", "content": base64.b64encode(content).decode()}
        if "protection/required_status_checks" in path:
            raise onboard.GitHubAccessError(path, 404)
        if path.endswith("actions/permissions"):
            return {"enabled": True, "allowed_actions": "all"}
        if path.endswith("/commits/" + PIN):
            return {"sha": PIN}
        if path.endswith("/commits/trunk"):
            return {"sha": "b" * 40}
        return {"private": False, "allow_auto_merge": True, "allow_squash_merge": True}

    def pages(self, path, field=None):
        if "/rules/branches/" in path:
            return iter([{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": CHECK}]}}])
        if path.endswith("/check-runs"):
            return iter(self.check_runs)
        if path.endswith("/statuses"):
            return iter(self.statuses)
        return iter([])

    def test_enrollment_is_last_additive_verified_and_idempotent(self):
        before = self.path.read_bytes()
        result = onboard.verify(self.checkout, self.path, PROJECT, self.linear)
        self.assertEqual([issue["identifier"] for issue in result["eligible_todo"]], ["OTHER-1"])
        self.assertEqual(self.linear.mutations, [])
        with redirect_stdout(io.StringIO()) as output:
            with CallBudget(self, linear=self.linear, exact={"linear.query": 8, "linear.team_estimates": 1, "linear.project_issues": 1}):
                self.assertIn("enrolled;", onboard.enroll(self.checkout, self.path, PROJECT, self.linear))
            with CallBudget(self, linear=self.linear, exact={"linear.query": 6, "linear.team_estimates": 1, "linear.project_issues": 1}):
                self.assertIn("already enrolled", onboard.enroll(self.checkout, self.path, PROJECT, self.linear))
        self.assertIn("OTHER-1", output.getvalue())
        self.assertEqual(self.linear.mutations, [{"id": PROJECT, "label": LABEL}])
        self.assertEqual(self.linear.labels[0], {"id": "unrelated", "name": "Keep this"})
        self.assertEqual(self.path.read_bytes(), before)

    def test_enrollment_requires_discoverable_issue_instructions(self):
        (self.checkout / "AGENTS.md").unlink()
        with self.assertRaisesRegex(onboard.SetupError, "persistent Symphony issue instructions"):
            onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
        self.assertFalse(self.linear.mutations)

    def test_enrollment_requires_a_configured_team_estimate_scale(self):
        with patch.object(self.linear, "team_estimates", side_effect=ValueError("Estimates disabled")), \
             self.assertRaisesRegex(ValueError, "Estimates disabled"):
            onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
        self.assertFalse(self.linear.mutations)

    def test_locally_prepared_guidance_does_not_prove_it_is_published(self):
        for content in (None, b"# Previous rules\n"):
            self.published_guidance["AGENTS.md"] = content
            with self.subTest(content=content), self.assertRaisesRegex(onboard.SetupError, "Publish.*default branch"):
                onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
            self.assertFalse(self.linear.mutations)
        onboard.gh.assert_any_call(f"repos/{REPO}/contents/AGENTS.md?ref={'b' * 40}")

    def test_enrollment_verifies_both_the_root_link_and_its_published_topic(self):
        for path, content, _ in onboard.issue_instruction_changes(self.checkout, Path("docs/development.md")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.published_guidance["AGENTS.md"] = (self.checkout / "AGENTS.md").read_bytes()
        with self.assertRaisesRegex(onboard.SetupError, "Publish.*docs/development.md"):
            onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
        self.assertFalse(self.linear.mutations)
        self.published_guidance["docs/development.md"] = (self.checkout / "docs/development.md").read_bytes()
        onboard.verify(self.checkout, self.path, PROJECT, self.linear)
        onboard.gh.assert_any_call(f"repos/{REPO}/contents/docs/development.md?ref={'b' * 40}")

    def test_existing_enrollment_name_is_not_duplicated_under_another_label_uuid(self):
        self.linear.labels.append({"id": "another-existing-label", "name": "clusterfork-symphony"})
        with redirect_stdout(io.StringIO()):
            self.assertIn("already enrolled", onboard.enroll(self.checkout, self.path, PROJECT, self.linear))
        self.assertEqual(self.linear.mutations, [])

    def test_unverified_deployment_mapping_protocol_or_admission_never_enrolls(self):
        for key, value in (("registry_digest", "wrong"), ("protocol", "old-worker"), ("phase_isolation", False),
                           ("serial_admission", False), ("deployment_paused", True), ("missing", ["LINEAR_WEBHOOK_SECRET"]),
                           ("deployment", {"digest": "old-source"})):
            good = copy.deepcopy(self.health)
            self.health[key] = value
            with self.subTest(field=key), self.assertRaisesRegex(onboard.SetupError, "Deploy"):
                onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
            self.assertEqual(self.linear.mutations, [])
            self.health = good

    def test_missing_app_grant_workflow_or_issue_labels_never_enrolls(self):
        for field, value in (("app_access", False), ("permissions", {"actions": "read"}),
                             ("workflow_verified", False), ("workflow_active", False), ("repository", "other/repo")):
            good = copy.deepcopy(self.health)
            self.health["repository_setup"][field] = value
            with self.subTest(field=field), self.assertRaises(onboard.SetupError):
                onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
            self.assertEqual(self.linear.mutations, [])
            self.health = good
        self.linear.issue_labels = []
        with self.assertRaisesRegex(onboard.SetupError, "provision"):
            onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
        self.assertEqual(self.linear.mutations, [])

    def test_old_success_cannot_hide_current_ci_failure(self):
        self.check_runs = [{"id": 1, "name": CHECK, "conclusion": "success"},
                           {"id": 2, "name": CHECK, "conclusion": "failure"}]
        with self.assertRaisesRegex(onboard.SetupError, "CI successfully"):
            onboard.enroll(self.checkout, self.path, PROJECT, self.linear)
        self.assertEqual(self.linear.mutations, [])

    def test_latest_commit_status_supersedes_its_old_failure(self):
        self.statuses = [{"id": 1, "context": CHECK, "state": "failure"},
                         {"id": 2, "context": CHECK, "state": "success"}]
        onboard.verify(self.checkout, self.path, PROJECT, self.linear)
        self.statuses.append({"id": 3, "context": CHECK, "state": "failure"})
        with self.assertRaisesRegex(onboard.SetupError, "CI successfully"):
            onboard.verify(self.checkout, self.path, PROJECT, self.linear)

    def test_state_selection_must_belong_to_explicit_project_team(self):
        with self.assertRaisesRegex(onboard.SetupError, "belonging"):
            onboard.validate_states(self.linear, PROJECT, LABEL, states())
        other = copy.deepcopy(states())
        other["ready"] = [LABEL]
        with self.assertRaisesRegex(onboard.SetupError, "lifecycle"):
            onboard.validate_states(self.linear, PROJECT, TEAM, other)


class ControllerEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture=EnrollmentTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        f.data=onboard.prepare_configuration(original_registry(),PROJECT,REPO,BRANCH,TEAM,states(),
                                             [['python3','-m','unittest','discover']],[CHECK])
        f.path.write_text(json.dumps(f.data))
        f.health.update(mode='single-controller',phase_isolation=True,registry_digest=digest(f.data),
                        deployment={'revision':PIN,'digest':'deployed-source'})
        f.health['repository_setup'].update(workflow_repository=WORKER_REPOSITORY,workflow='clusterfork-task.yml',
                                            runtime_sha=PIN,runtime_tag_verified=True,workflow_verified=True)
        self.dirty=''
        for patcher in (patch.object(onboard,'command',side_effect=lambda args,**kw:self.dirty if 'status' in args else PIN),
                        patch.object(onboard,'local_manifest',return_value={'digest':'deployed-source'})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_enrollment_uses_central_identity_without_a_target_worker_pin_or_wrapper(self):
        f=self.fixture
        result=onboard.verify(f.checkout,f.path,PROJECT,f.linear)
        self.assertEqual(result['eligible_todo'][0]['identifier'],'OTHER-1')
        paths=[call.args[0] for call in onboard.gh.call_args_list]
        self.assertFalse(any('clusterfork-select-account.yml' in path or 'clusterfork-develop.yml' in path for path in paths))
        self.assertEqual(f.linear.mutations,[])

    def test_unverified_runtime_or_unpublished_inputs_cannot_enroll(self):
        f=self.fixture
        f.health['repository_setup']['runtime_tag_verified']=False
        with self.assertRaises(onboard.SetupError):
            onboard.verify(f.checkout,f.path,PROJECT,f.linear)
        f.health['repository_setup']['runtime_tag_verified']=True
        self.dirty=' M automation/new-controller.py'
        with self.assertRaisesRegex(onboard.SetupError,'committed runtime'):
            onboard.verify(f.checkout,f.path,PROJECT,f.linear)
        self.assertEqual(f.linear.mutations,[])


if __name__ == "__main__":
    unittest.main()
