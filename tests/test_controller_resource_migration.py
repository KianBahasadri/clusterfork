"""Once-only import preserves credentials and fences the retired remote owner."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from controller.core import Controller
from controller.migrate_tables import FENCE_OWNER, migrate
from controller.model import Rejected
from controller.store import Database
from common.accounts import account_identity
from test_remote_worker import auth


class Entity(dict):
    @property
    def metadata(self):
        return {"etag": "fixture-version"}


class ResourceMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "tasks.db")
        self.addCleanup(self.database.close)
        self.controller = Controller(self.database)
        self.identity = account_identity(auth())
        self.accounts = [Entity(PartitionKey="codex", RowKey=self.identity, auth=json.dumps(auth()),
                                state="staged", owner="", fence="", lease_until=0, note="retained")]
        self.instructions = [Entity(PartitionKey="instructions", RowKey="comment", issue_id="issue",
                                    sequence=1, state="accepted", text="frozen instruction")]
        self.remote = Mock()
        self.remote.list_entities.side_effect = lambda: copy.deepcopy(self.accounts)
        def fence(entity, **kwargs):
            # Exercise the pinned SDK codec: large lease timestamps require Int64.
            from azure.data.tables._serialize import _add_entity_properties
            from azure.data.tables import EntityProperty
            _add_entity_properties(entity)
            self.assertEqual(kwargs["etag"], "fixture-version")
            self.accounts[:] = [Entity({key:value.value if isinstance(value,EntityProperty) else value
                                       for key,value in entity.items()})]
        self.remote.update_entity.side_effect = fence
        other = Mock()
        other.list_entities.side_effect = lambda: copy.deepcopy(self.instructions)
        self.factory = Mock(side_effect=lambda endpoint, table, **kwargs:
                            self.remote if table == "ClusterforkAccounts" else other)

    def migrate(self):
        migrate(self.controller, {"storage_account": "fixture"}, None, client_factory=self.factory)

    def test_preserves_auth_staging_and_instructions_with_private_backup(self):
        self.migrate()
        row, _ = self.controller.accounts.get(self.identity)
        self.assertEqual(json.loads(row["auth"]), auth())
        self.assertEqual(row["state"], "staged")
        self.assertEqual(row["note"], "retained")
        self.assertEqual(row["lease_until"], 0)
        self.assertEqual(self.accounts[0]["owner"], FENCE_OWNER)
        backup = self.database.path.parent / "migrations/azure-resources.json"
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with self.database.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM instructions").fetchone()[0], "accepted")
        self.factory.reset_mock()
        self.migrate()
        self.factory.assert_not_called()

    def test_live_legacy_lease_prevents_any_backup_or_fence(self):
        self.accounts[0]["lease_until"] = 4102444800
        with self.assertRaises(Rejected):
            self.migrate()
        self.remote.update_entity.assert_not_called()
        self.assertFalse((self.database.path.parent / "migrations").exists())

    def test_interrupted_fencing_retries_from_original_private_snapshot(self):
        fence = self.remote.update_entity.side_effect
        def interrupted(entity, **kwargs):
            fence(entity, **kwargs)
            raise OSError("lost response after remote fence")
        self.remote.update_entity.side_effect = interrupted
        with self.assertRaises(OSError):
            self.migrate()
        self.assertEqual(self.controller.accounts.list(), [])
        self.remote.update_entity.side_effect = fence
        self.migrate()
        self.assertEqual(self.controller.accounts.get(self.identity)[0]["state"], "staged")

    def test_changed_cloud_credentials_after_backup_fail_closed(self):
        self.remote.update_entity.side_effect = OSError("interrupted")
        with self.assertRaises(OSError):
            self.migrate()
        self.accounts[0]["auth"] = json.dumps(auth(token="changed-refresh-owner"))
        with self.assertRaisesRegex(Rejected, "ownership changed"):
            self.migrate()
        self.assertEqual(self.controller.accounts.list(), [])

    def test_invalid_instruction_import_rolls_back_accounts_and_receipt(self):
        self.instructions[0].pop("sequence")
        with self.assertRaises(KeyError):
            self.migrate()
        with self.database.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT value FROM metadata WHERE key='resources:sqlite-v1'").fetchone())


if __name__ == "__main__":
    unittest.main()
