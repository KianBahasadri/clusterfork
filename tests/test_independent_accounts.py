"""Independent synthetic provider sign-ins, conservative migration and secret-free quota reads."""

import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'automation'), str(ROOT / 'scripts')]
import accounts
import rotate_auth
from common.accounts import WorkerError, account_identity, login_fingerprints, login_id
from controller.accounts import Accounts
from controller.account_usage import UsageCollector, snapshot
from controller.store import Database

NOW = 1_800_000_000


def login(provider, session='cloud', revision=0, account='one'):
    payload = base64.urlsafe_b64encode(json.dumps({'sub': account, 'sid': session, 'revision': revision}).encode()).decode().rstrip('=')
    access = f'fixture.{payload}.signature'
    refresh = f'fixture-refresh-{session}-{revision}'
    if provider == 'cursor':
        return {'accessToken': access, 'refreshToken': refresh}
    return {'auth_mode': 'chatgpt', 'tokens': {'account_id': account, 'access_token': access, 'refresh_token': refresh}}


def usage(provider, used=20):
    return {'observed_at': NOW, 'windows': [{'name': 'codex/primary' if provider == 'codex' else 'cursor/api',
            'used_percent': used, 'resets_at': NOW + 10000, 'duration_minutes': 300}]}


class IndependentAccountTests(unittest.TestCase):
    def fixture(self, provider):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        database = Database(root / 'accounts.db')
        self.addCleanup(database.close)
        pools = Accounts(database, clock=lambda: NOW)
        pool = pools.pools[provider]
        pool.snapshot = lambda: snapshot(pools, provider, now=NOW)
        store = root / 'store'
        accounts.write_private(store / 'auth.json.main', login(provider))
        accounts.write_private(store / 'auth.json.alias', login(provider))
        backend = rotate_auth.SharedStoreBackend(command='rotate-codex' if provider == 'codex' else 'rotate-cursor-cli',
            label=provider, agent_dir_env='INDEPENDENT_TEST_AGENT', store_dir_env='INDEPENDENT_TEST_STORE',
            default_agent=root / 'agent', default_store=store)
        backend.agent_dir.mkdir()
        backend.current.symlink_to('auth.json.main')
        backend.auth.symlink_to(os.path.relpath(backend.current, backend.agent_dir))
        return root, store, pool, backend

    def command(self, action, provider, store, pool=None, *args):
        with patch.object(sys, 'argv', ['accounts.py', action, '--provider', provider, '--store', str(store), *args]), \
             patch.object(accounts, 'connect', return_value=pool), redirect_stdout(io.StringIO()) as output:
            accounts.main()
        return output.getvalue()

    def register(self, provider, root, store, pool, auth=None, target='local'):
        source = root / 'fresh.json'
        accounts.write_private(source, auth or login(provider, 'local'))
        self.command('register-local', provider, store, pool, '--profile', 'main', '--local-profile', target,
                     '--auth-file', str(source), '--fresh-login')
        return store / f'auth.json.{target}'

    def test_independent_sessions_and_refreshes_remain_separate_and_capacity_is_one(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                root, store, pool, backend = self.fixture(provider)
                self.command('enroll', provider, store, pool)
                identity = account_identity(login(provider), provider)
                self.assertEqual(pool.list(), [identity])
                self.assertFalse(backend.auth.exists())  # The old cloud-selected link cannot refresh.
                self.assertEqual(accounts.availability(store, provider)['accounts'][0]['local_available'], False)
                local = self.register(provider, root, store, pool)
                cloud_before = json.loads(pool.get(identity)[0]['auth'])
                accounts.write_private(local, login(provider, 'local', 1))
                with redirect_stdout(io.StringIO()):
                    backend.select('local')
                    backend.save('local-alias')
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), cloud_before)
                saved_local = local.read_bytes()
                lease = pool.claim(identity, 'cloud-worker')
                with self.assertRaisesRegex(WorkerError, 'busy'):
                    pool.claim(identity, 'other-worker')
                pool.save(identity, 'cloud-worker', lease['lease'], login(provider, 'cloud', 1), release=True)
                quota_lease = pool.claim(identity, 'quota:test-probe')
                worker_lease = pool.claim(identity, 'cloud-worker')
                self.assertNotEqual(quota_lease['lease'], worker_lease['lease'])
                pool.save(identity, 'cloud-worker', worker_lease['lease'], release=True)
                self.assertEqual(local.read_bytes(), saved_local)
                self.assertEqual(pool.list(), [identity])
                self.command('enroll', provider, store, pool)
                self.assertEqual(local.read_bytes(), saved_local)
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), login(provider, 'cloud', 1))
                with patch.object(rotate_auth, 'ping_agent', return_value=(True, '')) as ping, redirect_stdout(io.StringIO()):
                    self.assertEqual(backend.kickoff(['local']), 0)
                    ping.assert_called_once()

    def test_known_tokens_rotated_same_chain_and_unregistered_aliases_are_rejected(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                root, store, pool, backend = self.fixture(provider)
                self.command('enroll', provider, store, pool)
                identity = pool.list()[0]
                lease = pool.claim(identity, 'cloud-worker')
                pool.save(identity, 'cloud-worker', lease['lease'], login(provider, 'cloud', 1), release=True)
                for candidate in (login(provider), login(provider, 'cloud', 1), login(provider, 'cloud', 2)):
                    with self.assertRaisesRegex(WorkerError, 'cloud token|provider session'):
                        self.register(provider, root, store, pool, candidate)
                self.assertFalse((store / 'auth.json.local').exists())
                accounts.write_private(store / 'auth.json.copy', login(provider, 'cloud', 2))
                for operation in (lambda: backend.select('copy'), lambda: backend.kickoff(['copy']),
                                  lambda: backend.save('copy')):
                    with self.assertRaises(rotate_auth.RotateError):
                        operation()
                # Even an unhooked regular cloud login cannot be saved under a new alias.
                backend.auth.unlink()
                accounts.write_private(backend.auth, login(provider, 'cloud', 1))
                with self.assertRaisesRegex(rotate_auth.RotateError, 'explicit fresh'):
                    backend.save('bypass')
                self.assertFalse((store / 'auth.json.bypass').exists())

    def test_registration_retries_without_signin_and_unsafe_destinations_do_not_overwrite(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                root, store, pool, backend = self.fixture(provider)
                self.command('enroll', provider, store, pool)
                with patch.object(accounts, 'write_new', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                    self.register(provider, root, store, pool)
                self.assertFalse(accounts.availability(store, provider)['accounts'][0]['local_available'])
                local = self.register(provider, root, store, pool)
                original = local.read_bytes()
                self.register(provider, root, store, pool)  # An exact retry is harmless.
                with self.assertRaisesRegex(WorkerError, 'overwrite'):
                    self.register(provider, root, store, pool, login(provider, 'another-local'))
                self.assertEqual(local.read_bytes(), original)
                identity = pool.list()[0]
                lease = pool.claim(identity, 'cloud-worker')
                with self.assertRaisesRegex(WorkerError, 'independent local'):
                    pool.save(identity, 'cloud-worker', lease['lease'], login(provider, 'local'))
                pool.save(identity, 'cloud-worker', lease['lease'], release=True)
                # Save cannot replace the registered local login with another account.
                backend.auth.unlink()
                accounts.write_private(backend.auth, login(provider, 'other', account='two'))
                with self.assertRaisesRegex(rotate_auth.RotateError, 'overwrite'):
                    backend.save('local')
                self.assertEqual(local.read_bytes(), original)

    def test_reclaim_requires_new_destination_and_preserves_independent_local_login(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                root, store, pool, backend = self.fixture(provider)
                self.command('enroll', provider, store, pool)
                local = self.register(provider, root, store, pool)
                original = local.read_bytes()
                identity = pool.list()[0]
                lease = pool.claim(identity, 'cloud-worker')
                pool.save(identity, 'cloud-worker', lease['lease'], login(provider, 'cloud', 1), release=True)
                with self.assertRaisesRegex(WorkerError, 'separate --destination-profile'):
                    self.command('reclaim', provider, store, pool, '--profile', 'main')
                self.command('reclaim', provider, store, pool, '--profile', 'main', '--destination-profile', 'reclaimed')
                self.assertEqual(local.read_bytes(), original)
                self.assertEqual(json.loads((store / 'auth.json.reclaimed').read_text()), login(provider, 'cloud', 1))
                self.assertEqual(pool.list(), [])
                local_state = accounts.availability(store, provider)['accounts'][0]
                self.assertTrue(local_state['local_available'])
                self.assertFalse(local_state['cloud_available'])
                self.command('enroll', provider, store, pool, '--profile', 'reclaimed')
                self.assertEqual(local.read_bytes(), original)
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), login(provider, 'cloud', 1))
                self.assertTrue(accounts.availability(store, provider)['accounts'][0]['local_available'])

    def test_interrupted_reclaim_retries_before_and_after_cloud_deletion_without_replacing_local(self):
        for provider in ('codex', 'cursor'):
            for interruption in ('delete', 'manifest'):
                with self.subTest(provider=provider, interruption=interruption):
                    root, store, pool, backend = self.fixture(provider)
                    self.command('enroll', provider, store, pool)
                    local = self.register(provider, root, store, pool)
                    original = local.read_bytes()
                    client = SimpleNamespace(transfer_id=str(uuid4()))
                    client.claim = lambda identity, owner, allow_staged: pool.claim(
                        identity, 'reclaim:' + client.transfer_id, allow_staged=allow_staged, reuse=True)
                    client.delete = lambda identity, fence: pool.delete(identity, fence, owner='reclaim:' + client.transfer_id)
                    if interruption == 'delete':
                        with patch.object(client, 'delete', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                            self.command('reclaim', provider, store, client, '--profile', 'main', '--destination-profile', 'reclaimed')
                    else:
                        write = accounts.write_private
                        calls = []
                        def interrupted_write(path, value):
                            calls.append(path)
                            if len(calls) == 2:
                                raise OSError('interrupted after cloud deletion')
                            write(path, value)
                        with patch.object(accounts, 'write_private', side_effect=interrupted_write), self.assertRaises(OSError):
                            self.command('reclaim', provider, store, client, '--profile', 'main', '--destination-profile', 'reclaimed')
                    self.assertEqual(local.read_bytes(), original)
                    # A new CLI instance recovers the journal's original transfer owner.
                    client.transfer_id = str(uuid4())
                    self.command('reclaim', provider, store, client, '--profile', 'main', '--destination-profile', 'reclaimed')
                    self.assertEqual(local.read_bytes(), original)
                    self.assertEqual(json.loads((store / 'auth.json.reclaimed').read_text()), login(provider))
                    self.assertEqual(pool.list(), [])

    def test_interrupted_enrollment_never_replaces_cloud_tokens_refreshed_before_retry(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                root, store, pool, backend = self.fixture(provider)
                with patch.object(accounts, 'write_private', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                    self.command('enroll', provider, store, pool)
                identity = pool.list()[0]
                lease = pool.claim(identity, 'worker')
                pool.save(identity, 'worker', lease['lease'], login(provider, 'cloud', 3), release=True)
                self.command('enroll', provider, store, pool)
                self.command('enroll', provider, store, pool)
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), login(provider, 'cloud', 3))
                self.assertFalse(accounts.availability(store, provider)['accounts'][0]['local_available'])

    def test_read_only_contract_groups_aliases_and_distinguishes_availability_from_selection(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                root, store, pool, backend = self.fixture(provider)
                self.command('enroll', provider, store, pool)
                self.register(provider, root, store, pool)
                before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in store.rglob('*') if p.is_file()}
                with patch.object(pool, 'claim', side_effect=AssertionError('read claimed credentials')), \
                     patch.object(pool, 'get', side_effect=AssertionError('read returned credentials')):
                    result = accounts.consumer_snapshot(store, provider, pool)
                self.assertEqual(len(result['accounts']), 1)
                row = result['accounts'][0]
                self.assertEqual(row['aliases'], ['alias', 'local', 'main'])
                self.assertTrue(row['local_available'])
                self.assertIsNone(row['selected_local_profile'])
                self.assertEqual(row['usage']['freshness'], 'unknown')
                self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in store.rglob('*') if p.is_file()})
                with redirect_stdout(io.StringIO()):
                    backend.select('local')
                self.assertEqual(accounts.availability(store, provider)['accounts'][0]['selected_local_profile'], 'local')
                encoded = json.dumps(result)
                for auth in (login(provider), login(provider, 'local')):
                    tokens = auth if provider == 'cursor' else auth['tokens']
                    for key in ('accessToken', 'refreshToken', 'access_token', 'refresh_token'):
                        if key in tokens:
                            self.assertNotIn(tokens[key], encoded)
                unavailable = SimpleNamespace(snapshot=Mock(side_effect=WorkerError('offline', 503)))
                failed = accounts.consumer_snapshot(store, provider, unavailable)
                self.assertEqual(failed['cloud_error'], 'controller_unavailable')
                self.assertTrue(failed['accounts'][0]['local_available'])

    def test_legacy_inventory_is_read_only_and_migration_is_repeatable_after_archive_interruption(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider):
                root, store, pool, backend = self.fixture(provider)
                identity = pool.enroll(login(provider, 'cloud', 2))
                manifest = {'main': {'account_id': identity, 'source_sha256': accounts.digest(store / 'auth.json.main')}}
                accounts.write_private(store / accounts.MANIFEST, manifest)
                original = (store / accounts.MANIFEST).read_bytes()
                self.assertFalse(accounts.availability(store, provider)['accounts'][0]['local_available'])
                self.assertEqual((store / accounts.MANIFEST).read_bytes(), original)
                with patch.object(rotate_auth, 'atomic_write_text', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                    rotate_auth.load_remote_profiles(store)
                self.assertFalse((store / 'auth.json.main').exists())
                self.command('migrate', provider, store)
                self.command('migrate', provider, store)
                self.command('enroll', provider, store, pool, '--profile', 'main')
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), login(provider, 'cloud', 2))
                self.assertFalse(accounts.availability(store, provider)['accounts'][0]['local_available'])


class CloudUsageTests(unittest.TestCase):
    def test_version_six_upgrade_preserves_latest_cloud_login_and_never_invents_local_ownership(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'accounts.db'
                with Database(path) as database:
                    pool = Accounts(database).pools[provider]
                    identity = pool.enroll(login(provider, 'cloud', 7))
                    with database.transaction() as db:
                        db.execute('UPDATE accounts SET metadata=?,state=?', ('{"staged_for_issue":"legacy"}', 'staged'))
                        db.execute('DROP TABLE account_usage')
                        db.execute('PRAGMA user_version=6')
                for _ in range(2):
                    with Database(path) as database:
                        pools = Accounts(database)
                        pool = pools.pools[provider]
                        row = pool.get(identity)[0]
                        self.assertEqual(json.loads(row['auth']), login(provider, 'cloud', 7))
                        self.assertEqual(row['state'], 'staged')
                        self.assertNotIn('local_logins', row)
                        self.assertEqual(snapshot(pools, provider, now=NOW)['accounts'][0]['usage']['freshness'], 'unknown')

    def test_collection_is_fenced_preserves_refreshes_and_exposes_busy_stale_unknown_errors(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                now = [NOW]
                database = Database(Path(directory) / 'accounts.db')
                self.addCleanup(database.close)
                pools = Accounts(database, clock=lambda: now[0])
                pool = pools.pools[provider]
                identity = pool.enroll(login(provider), aliases=['main'])
                def probe(provider, identity, home):
                    path = home / ('.config/cursor/auth.json' if provider == 'cursor' else 'auth.json')
                    accounts.write_private(path, login(provider, 'cloud', 1))
                    return {**usage(provider), 'auth': 'private', 'access_token': 'private'}
                collector = UsageCollector(pools, None, clock=lambda: now[0], probe=probe)
                collector.collect(pool, identity)
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), login(provider, 'cloud', 1))
                reading = snapshot(pools, provider, now=NOW)['accounts'][0]['usage']
                self.assertEqual(reading['freshness'], 'fresh')
                self.assertEqual(reading['observed_at'], NOW)
                self.assertNotIn('auth', reading)
                lease = pool.claim(identity, 'worker')
                collector.probe = Mock(side_effect=AssertionError('busy account inspected'))
                before = pool.get(identity)
                collector.collect(pool, identity)
                collector.probe.assert_not_called()
                self.assertEqual(pool.get(identity), before)
                busy = snapshot(pools, provider, now=NOW + 301)['accounts'][0]['usage']
                self.assertEqual((busy['freshness'], busy['error']), ('stale', 'busy'))
                self.assertEqual(busy['observed_at'], NOW)
                pool.save(identity, 'worker', lease['lease'], release=True)
                def failed_probe(provider, identity, home):
                    path = home / ('.config/cursor/auth.json' if provider == 'cursor' else 'auth.json')
                    accounts.write_private(path, login(provider, 'cloud', 2))
                    raise ValueError('PRIVATE TOKEN IN PROVIDER ERROR')
                collector.probe = failed_probe
                collector.collect(pool, identity)
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), login(provider, 'cloud', 2))
                failed = snapshot(pools, provider, now=NOW + 302)['accounts'][0]['usage']
                self.assertEqual(failed['error'], 'inspection_failed')
                self.assertNotIn('PRIVATE', json.dumps(failed))
                unknown = pool.enroll(login(provider, account='two'))
                row = next(r for r in snapshot(pools, provider, now=NOW)['accounts'] if r['account_id'] == unknown)
                self.assertEqual(row['usage']['freshness'], 'unknown')
                self.assertEqual(row['usage']['windows'], [])
                collector.enabled = lambda: False
                with patch.object(pool, 'claim', side_effect=AssertionError('hold must allow controller drain')):
                    collector.tick()

    def test_lost_lease_discards_measurement_and_does_not_overwrite_new_cloud_login(self):
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                now = [NOW]
                database = Database(Path(directory) / 'accounts.db')
                self.addCleanup(database.close)
                pools = Accounts(database, clock=lambda: now[0])
                pool = pools.pools[provider]
                identity = pool.enroll(login(provider))
                def probe(*args):
                    now[0] += 121
                    other = pool.claim(identity, 'new-owner')
                    pool.save(identity, 'new-owner', other['lease'], login(provider, 'cloud', 5))
                    return usage(provider)
                UsageCollector(pools, None, clock=lambda: now[0], probe=probe).collect(pool, identity)
                self.assertEqual(json.loads(pool.get(identity)[0]['auth']), login(provider, 'cloud', 5))
                reading = snapshot(pools, provider, now=now[0])['accounts'][0]['usage']
                self.assertEqual((reading['freshness'], reading['error']), ('unknown', 'lease_lost'))
                self.assertEqual(pool.get(identity)[0]['owner'], 'new-owner')

    def test_collector_normalizes_actual_provider_windows_without_target_execution(self):
        from common.routing import Registry
        from test_cursor_accounts import usage as cursor_usage
        from test_worker_reporting import limits as codex_limits
        registry = Registry.load(ROOT / 'automation/controller/projects.json')
        for provider in ('codex', 'cursor'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                database = Database(Path(directory) / 'accounts.db')
                self.addCleanup(database.close)
                pools = Accounts(database, clock=lambda: NOW)
                pool = pools.pools[provider]
                identity = pool.enroll(login(provider))
                runtime = 'cursor_runtime' if provider == 'cursor' else 'codex_runtime'
                data = cursor_usage(33) if provider == 'cursor' else codex_limits(33, 44)
                with patch(runtime + '.read_usage', return_value=data) as read, \
                     patch(runtime + '.time.time', return_value=NOW):
                    UsageCollector(pools, registry, clock=lambda: NOW).collect(pool, identity)
                reading = snapshot(pools, provider, now=NOW)['accounts'][0]['usage']
                self.assertEqual(reading['freshness'], 'fresh')
                self.assertEqual(reading['observed_at'], NOW)
                self.assertTrue(all(w['resets_at'] > NOW for w in reading['windows']))
                read.assert_called_once()


if __name__ == '__main__':
    unittest.main()
