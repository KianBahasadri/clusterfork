"""Real signed operator identity and HTTPS-protocol account transfer semantics."""

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
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
from test_independent_accounts import login, usage
from controller.account_usage import UsageCollector
from common.accounts import login_fingerprints, login_id

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

    def test_authenticated_snapshot_is_cache_only_and_never_returns_credential_metadata(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                pool = self.f.controller.accounts.pools[provider]
                identity = pool.enroll(login(provider), aliases=['main', 'alias'])
                reading = usage(provider)
                reading['observed_at'] = 1000
                collector = UsageCollector(self.f.controller.accounts, None, clock=lambda: 1000,
                    probe=lambda *args: reading)
                collector.collect(pool, identity)
                before = pool.get(identity)
                client = self.client()
                client.provider = provider
                with patch.object(pool, 'claim', side_effect=AssertionError('GET attempted to claim auth')), \
                     patch.object(pool, 'get', side_effect=AssertionError('GET read credential cache')):
                    result = client.snapshot()
                    with self.f.request('/api/accounts/snapshot?provider=' + provider) as response:
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(pool.get(identity), before)
                self.assertEqual(result['schema_version'], 1)
                self.assertEqual(set(result), {'schema_version', 'generated_at', 'provider', 'accounts'})
                self.assertEqual(set(result['accounts'][0]), {
                    'provider', 'account_id', 'aliases', 'cloud_available', 'cloud_state', 'usage'})
                self.assertEqual(set(result['accounts'][0]['usage']), {
                    'observed_at', 'attempted_at', 'age_seconds', 'max_age_seconds', 'freshness', 'error', 'windows'})
                self.assertEqual(result['accounts'][0]['aliases'], ['alias', 'main'])
                self.assertEqual(result['accounts'][0]['usage']['freshness'], 'fresh')
                encoded = json.dumps(result)
                for key in ('auth', 'access_token', 'refresh_token', 'accessToken', 'refreshToken', 'cloud_login', 'fingerprints', 'fence'):
                    self.assertNotIn('"' + key + '"', encoded)
                self.assertFalse(self.f.app.wakeup.is_set())

    def test_snapshot_rejects_missing_operator_access_and_ambiguous_queries(self):
        for query in ('', '?provider=codex&provider=cursor', '?provider=other', '?provider=codex&auth=true'):
            with self.subTest(query=query), self.assertRaises(HTTPError) as caught:
                self.f.request('/api/accounts/snapshot' + query)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        self.f.app.account_operator = None
        with self.assertRaises(WorkerError) as caught:
            self.client().snapshot()
        self.assertEqual(caught.exception.status, 401)

    def test_registration_transfers_only_fingerprints_and_cloud_login_remains_fenced(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                client = self.client()
                client.provider = provider
                identity = client.enroll(login(provider), aliases=['main'])
                original = client.get(identity)
                local = login(provider, 'local')
                client.register_local(identity, login_id(local, provider), login_fingerprints(local, provider))
                self.assertEqual(client.get(identity)[0]['auth'], original[0]['auth'])
                with self.assertRaisesRegex(WorkerError, 'cloud token|provider session'):
                    client.register_local(identity, login_id(login(provider), provider), login_fingerprints(login(provider), provider))


if __name__ == "__main__":
    unittest.main()
