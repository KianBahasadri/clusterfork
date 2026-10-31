"""Service assembly preserves halt ownership and keeps secrets out of task state."""

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"automation")]
import controller_main
from controller.model import Hold
from controller.store import Database


class ControllerRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/"tasks.db"
        self.database=Database(self.path)
        self.addCleanup(self.database.close)
        self.config={"key_vault":"fixture-vault","github_app_id":"123",
                     "linear_oauth_client_id":"fixture-oauth-id"}
        self.manifest={"revision":"a"*40,"digest":"b"*64,"format":1}
        self.credential=Mock()

    def build(self):
        with patch.object(controller_main,"secret",side_effect=lambda credential,vault,name:"private-fixture-"+name), \
             patch.object(controller_main,"Linear") as linear,patch.object(controller_main,"GitHub"):
            application=controller_main.build(self.config,self.manifest,self.database,self.credential)
        return application,linear

    def test_first_start_is_halted_and_restart_does_not_recreate_a_cleared_hold(self):
        first,_=self.build()
        self.assertIn("operator:rebuild",first.controller.holds())
        first.controller.apply(Hold("operator:rebuild","",release=True),now=1000)
        restarted,_=self.build()
        self.assertEqual(restarted.controller.holds(),{})
        for path in self.path.parent.glob("tasks.db*"):
            self.assertNotIn(b"private-fixture-",path.read_bytes())

    def test_existing_linear_authentication_modes_are_preserved(self):
        _,linear=self.build()
        self.assertEqual(linear.call_args.kwargs["client_id"],"fixture-oauth-id")
        self.config.pop("linear_oauth_client_id")
        _,linear=self.build()
        self.assertEqual(linear.call_args.args,("private-fixture-linear-api-key",))

    def test_telemetry_initialization_failure_does_not_prevent_controller_startup(self):
        self.config['telemetry']={'endpoint':'unused','rule_id':'unused'}
        with patch.object(controller_main,'Telemetry',side_effect=OSError('private telemetry error')), \
             self.assertLogs(level='WARNING') as logs:
            application,_=self.build()
        self.assertIsNone(application.telemetry)
        self.assertIn('operator:rebuild',application.controller.holds())
        self.assertNotIn('private telemetry error',' '.join(logs.output))

    def test_host_identity_cannot_be_reused_by_a_different_vm(self):
        observed={"resourceId":"/subscriptions/fixture/resourceGroups/group/providers/Microsoft.Compute/virtualMachines/controller"}
        def response(_request,**_kwargs):
            return io.BytesIO(json.dumps(observed).encode())
        with patch.object(controller_main,"urlopen",side_effect=response):
            controller_main.verify_host(observed["resourceId"].upper())
            with self.assertRaisesRegex(ValueError,"host identity"):
                controller_main.verify_host(observed["resourceId"]+"-clone")


if __name__=="__main__":
    unittest.main()
