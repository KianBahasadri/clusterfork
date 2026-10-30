"""Native task graph and signed role checks, using the real workflow documents."""

from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
from controller.workflows import ROLE_FILES, WORKFLOW


def workflow(name):
    # Ruby's YAML parser is also used by the existing workflow layout checks.
    result = subprocess.run(["ruby", "-rjson", "-ryaml", "-e",
                             "puts JSON.generate(YAML.load_file(ARGV[0]))", str(ROOT / ".github/workflows" / name)],
                            capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    # Psych uses YAML 1.1, where the Actions key `on` is a boolean.
    if "true" in data:
        data["on"] = data.pop("true")
    return data


class ControllerWorkflowTests(unittest.TestCase):
    def test_coding_needs_passing_review_in_the_same_run(self):
        jobs = workflow(WORKFLOW)["jobs"]
        self.assertEqual(set(jobs), {"review-account", "review", "coding-account", "code"})
        for job, prerequisite, output in (("review", "review-account", "selected"),
                                          ("coding-account", "review", "ready"),
                                          ("code", "coding-account", "selected")):
            with self.subTest(job=job):
                self.assertEqual(jobs[job]["needs"], prerequisite)
                self.assertEqual(jobs[job]["if"], f"needs.{prerequisite}.outputs.{output} == 'true'")
        for job in jobs.values():
            self.assertEqual(job["with"]["worker_revision"], "${{ github.sha }}")

    def test_four_trust_roles_have_distinct_workflow_identities_and_fresh_vms(self):
        self.assertEqual(len(set(ROLE_FILES.values())), 4)
        for role, name in ROLE_FILES.items():
            data = workflow(name)
            self.assertEqual(data["permissions"], {"contents": "read", "id-token": "write"})
            job, = data["jobs"].values()
            self.assertEqual(job["runs-on"], "ubuntu-24.04")
            steps = job["steps"]
            boot = next(i for i,s in enumerate(steps) if s.get("id") == "target")
            self.assertIn("controller_bootstrap.py", steps[boot]["run"])
            checkouts = [(i,s) for i,s in enumerate(steps) if s.get("with",{}).get("path") == ".clusterfork-target"]
            if role.endswith("-select"):
                self.assertEqual(checkouts, [])
                self.assertEqual(steps[boot]["env"]["PREFLIGHT_ONLY"], "true")
            else:
                (checkout, step), = checkouts
                self.assertLess(boot, checkout)
                self.assertEqual(step["with"]["token"], "${{ steps.target.outputs.token }}")
                self.assertEqual(step["with"]["ref"], "${{ steps.target.outputs.context_sha }}")
                self.assertIs(step["with"]["persist-credentials"], False)
            execute = next(s for s in steps if s.get("id") == "execute")
            self.assertEqual(execute["working-directory"], ".clusterfork-target")
            self.assertIn(".clusterfork-runtime/automation/worker.py", execute["run"])
            self.assertNotIn("mv ", "\n".join(s.get("run", "") for s in steps))

    def test_only_preflight_roles_run_selection_and_no_credentials_cross_jobs(self):
        for role, name in ROLE_FILES.items():
            data = workflow(name)
            job, = data["jobs"].values()
            execute = next(s for s in job["steps"] if s.get("id") == "execute")
            self.assertEqual("--select-account" in execute["run"], role.endswith("-select"))
            self.assertFalse(set(job.get("outputs", {})) - {"selected", "ready"})
            self.assertFalse(set(data["on"]["workflow_call"].get("outputs", {})) - {"selected", "ready"})


if __name__ == "__main__":
    unittest.main()
