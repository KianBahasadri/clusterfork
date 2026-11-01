"""Native task graph and signed role checks, using the real workflow documents."""

from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation")]
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
    def test_controller_dispatches_exactly_one_phase_without_job_chaining(self):
        jobs=workflow(WORKFLOW)['jobs']
        self.assertEqual(set(jobs),{'review','code'})
        self.assertEqual(jobs['review']['if'],"inputs.phase == 'review'")
        self.assertEqual(jobs['code']['if'],"inputs.phase == 'coding'")
        for job in jobs.values():
            self.assertNotIn('needs',job)
            self.assertEqual(job['with']['worker_revision'],'${{ github.sha }}')

    def test_two_signed_phases_run_on_fresh_vms_and_select_before_target_access(self):
        self.assertEqual(len(set(ROLE_FILES.values())),2)
        for role,name in ROLE_FILES.items():
            data=workflow(name)
            self.assertEqual(data['permissions'],{'contents':'read','id-token':'write'})
            job,=data['jobs'].values()
            self.assertEqual(job['runs-on'],'ubuntu-24.04')
            steps=job['steps']
            checkouts=[step for step in steps if step.get('uses','').startswith('actions/checkout@')]
            self.assertEqual(len(checkouts),1)
            self.assertEqual(checkouts[0]['with']['path'],'.clusterfork-runtime')
            self.assertIs(checkouts[0]['with']['persist-credentials'],False)
            execute=next(step for step in steps if step.get('id')=='execute')
            self.assertIn('.clusterfork-runtime/automation/worker.py',execute['run'])
            self.assertNotIn('--select-account',execute['run'])
            self.assertEqual(execute['env']['WORKER_REVISION'],'${{ inputs.worker_revision }}')

    def test_phase_results_go_to_controller_without_job_outputs(self):
        for name in ROLE_FILES.values():
            data=workflow(name)
            job,=data['jobs'].values()
            self.assertNotIn('outputs',job)
            self.assertNotIn('outputs',data['on']['workflow_call'])


if __name__ == "__main__":
    unittest.main()
