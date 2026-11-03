"""Deployment transport failures are bounded, explicit, and never retried blindly."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'automation'))
import controller_deploy as deploy


class ControllerDeployTransportTests(unittest.TestCase):
    def test_busy_run_command_explains_conflict_without_retrying_or_exposing_output(self):
        result=subprocess.CompletedProcess([],1,'private stdout',
            'ERROR: (Conflict) Run command extension execution is in progress. private stderr')
        with patch.object(deploy.subprocess,'run',return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError,'Azure Run Command is busy') as caught:
                deploy.run_host('echo private script')
        run.assert_called_once()
        self.assertNotIn('private',str(caught.exception))

    def test_other_failures_continue_to_withhold_raw_output(self):
        result=subprocess.CompletedProcess([],1,'private stdout','private stderr')
        with patch.object(deploy.subprocess,'run',return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError,'raw output withheld') as caught:
                deploy.command(['az','rest'])
        run.assert_called_once()
        self.assertNotIn('private',str(caught.exception))

    def test_a_hung_client_exits_with_a_remote_execution_warning(self):
        with self.assertRaisesRegex(RuntimeError,'remote execution may still be running'):
            deploy.command([sys.executable,'-c','import time; time.sleep(30)'],timeout=0.1)

    def test_guest_deadline_stops_a_hung_script_even_if_the_client_remains_connected(self):
        outcomes=[]
        def local_guest(args,**kwargs):
            script=args[args.index('--scripts')+1]
            result=subprocess.run(['/bin/sh'],input=script,capture_output=True,text=True,timeout=3)
            outcomes.append(result)
            return json.dumps({'value':[{'message':result.stdout}]})
        with patch.object(deploy,'command',side_effect=local_guest) as command:
            deploy.run_host('sleep 30\nprintf CF_UNEXPECTED_SUCCESS',timeout=0.1)
        command.assert_called_once()
        self.assertEqual(outcomes[0].returncode,124)
        self.assertNotIn('CF_UNEXPECTED_SUCCESS',outcomes[0].stdout)

    def test_successful_host_output_is_preserved(self):
        with patch.object(deploy,'command',return_value=json.dumps({'value':[{'message':'CF_RESULT:fixture'}]})):
            self.assertEqual(deploy.run_host('true'),'CF_RESULT:fixture')


if __name__=='__main__':
    unittest.main()
