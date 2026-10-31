"""Real signed operator identity and HTTPS-protocol account transfer semantics."""

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from account_client import AccountClient
from common.accounts import WorkerError
from common.identity import verify_operator
import test_controller_server as server_fixtures
from test_remote_worker import auth

TENANT = "00000000-0000-0000-0000-000000000001"
OPERATOR = "00000000-0000-0000-0000-000000000002"


class OperatorIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, **overrides):
        return jwt.encode({"iss": f"https://sts.windows.net/{TENANT}/", "aud": "https://management.azure.com/",
                           "exp": int(time.time()) + 300, "nbf": int(time.time()) - 5,
                           "tid": TENANT, "oid": OPERATOR, **overrides}, self.key, algorithm="RS256")

    def verify(self, token):
        keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=self.key.public_key()))
        with patch("common.identity.operator_keys", return_value=keys):
            return verify_operator("Bearer " + token, TENANT, [OPERATOR])

    def test_existing_azure_operator_access_token_is_sufficient(self):
        self.assertEqual(self.verify(self.token()), OPERATOR)

    def test_wrong_principal_tenant_audience_issuer_and_expiry_are_rejected(self):
        for change in ({"oid": "another-user"}, {"tid": "another-tenant"}, {"aud": "https://graph.microsoft.com"},
                       {"iss": "https://attacker.example"}, {"exp": int(time.time()) - 1}):
            with self.subTest(change=change), self.assertRaises(WorkerError):
                self.verify(self.token(**change))

    def test_forged_signature_cannot_authorize_a_transfer(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        with self.assertRaises(WorkerError):
            self.verify(jwt.encode(claims, other, algorithm="RS256"))


class AccountTransferTests(unittest.TestCase):
    def setUp(self):
        self.f = server_fixtures.ControllerServerTests(methodName="runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.app.account_operator = lambda header: OPERATOR
        self.endpoint = patch("account_client.ENDPOINT", self.f.url + "/api/accounts")
        self.endpoint.start()
        self.addCleanup(self.endpoint.stop)

    def client(self):
        client = AccountClient("codex")
        client.token = "synthetic-operator-token"
        return client

    def test_reclaim_is_fenced_by_operator_and_transfer_id(self):
        first, other = self.client(), self.client()
        identity = first.enroll(auth())
        lease = first.claim(identity, "local-reclaim", allow_staged=True)
        self.assertEqual(first.claim(identity, "local-reclaim", allow_staged=True)["lease"], lease["lease"])
        with self.assertRaises(WorkerError):
            other.claim(identity, "local-reclaim", allow_staged=True)
        with self.assertRaises(WorkerError):
            other.delete(identity, lease["lease"])
        first.delete(identity, lease["lease"])
        with self.assertRaises(WorkerError):
            first.get(identity)

    def test_missing_operator_access_rejects_credentials_before_mutation(self):
        self.f.app.account_operator = None
        with self.assertRaises(WorkerError):
            self.client().enroll(auth())
        self.assertEqual(self.f.controller.accounts.list(), [])

    def test_non_object_body_is_a_bad_request(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as caught:
            self.f.request("/api/accounts", json.dumps([]).encode())
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
