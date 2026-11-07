"""Scoped account operations behind rotate-codex and rotate-cursor-cli.

Save the current provider login into a local or remote profile. Remote transfers
use a private journal; local credentials are registered with fingerprints alone.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import accounts
from common.accounts import WorkerError, account_identity, login_fingerprints, login_id
from rotate_auth import (CLOUD_DIRECTORY, REMOTE_MANIFEST, SUFFIX_RE, confirm_delete,
                         fail, legacy_archive, load_remote_profiles, normalize_profile_scopes,
                         remote_profile_key)


def remote_names(manifest):
    return {alias: record.get("display_name", alias) for alias, record in manifest.items()
            if record.get("ownership", "cloud") == "cloud"}


class ScopedAccounts:
    def __init__(self, backend):
        self.backend = backend
        self.provider = "cursor" if backend.command == "rotate-cursor-cli" else "codex"
        self.store = backend.store_dir.absolute()

    def manifest(self, *, migrate=True):
        try:
            return normalize_profile_scopes(self.store) if migrate else load_remote_profiles(self.store, migrate=False)
        except (OSError, ValueError, TypeError):
            raise WorkerError("Cannot read local/remote account ownership") from None

    def validate_name(self, name):
        if (not isinstance(name, str) or not SUFFIX_RE.fullmatch(name)
                or name in {"lock", ".", ".."} or len(name) > 120):
            raise WorkerError("Choose a profile name of 1–120 letters, numbers, dots, underscores or dashes")

    def login_home(self, scope, name):
        self.validate_name(name)
        # A pending remote transfer must stay outside local profile enumeration.
        return self.store.parent / f".{self.store.name}-logins" / scope / name

    def auth_path(self, home):
        return home / (".config/cursor/auth.json" if self.provider == "cursor" else ".codex/auth.json")

    def check_login_home(self, home):
        root = self.store.parent / f".{self.store.name}-logins"
        for path in (root, home.parent, home):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise WorkerError("Private transfer directory was replaced")

    def read_auth(self, source):
        if source.is_symlink() or not source.is_file():
            raise WorkerError("Fresh provider credentials must be a regular file; unhook before signing in")
        try:
            auth = json.loads(source.read_text())
            account_identity(auth, self.provider)
        except (ValueError, TypeError, OSError):
            raise WorkerError("Invalid provider sign-in credentials") from None
        return auth

    def save(self, scope, name):
        self.validate_name(name)
        if scope == "local":
            return self.save_local(name)
        manifest = self.manifest()
        home = self.login_home("remote", name)
        self.check_login_home(home)
        source = self.auth_path(home)
        for parent in source.relative_to(home).parents:
            if (home / parent).is_symlink():
                raise WorkerError("Private transfer directory was replaced")
        pending = source.exists() or source.is_symlink()
        if remote_profile_key(name) in manifest and not pending:
            raise WorkerError("Remote profile already exists; delete it before saving a replacement")
        if pending:
            auth = self.read_auth(source)
            if self.backend.auth.is_file() and not self.backend.auth.is_symlink():
                if self.read_auth(self.backend.auth) != auth:
                    raise WorkerError("Another remote transfer is pending; the current login was kept")
                self.backend.auth.unlink()
        else:
            auth = self.read_auth(self.backend.auth)
            self.local_registrations(auth, manifest)
            source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for path in (home.parent.parent, home.parent, home):
                path.chmod(0o700)
            accounts.write_new(source, auth)
            # A later login must never be removed by this transfer.
            if self.read_auth(self.backend.auth) != auth:
                raise WorkerError("Current login changed; retry the pending remote transfer")
            self.backend.auth.unlink()
        current = self.backend.current_suffix()
        if current and current in self.backend.local_profiles():
            self.backend._restore_auth_link()
        self.save_remote(name, source, accounts.connect(self.provider))
        shutil.rmtree(home)
        return 0

    def save_local(self, name):
        manifest = self.manifest()
        source = self.backend.auth
        if source.is_symlink() or not source.is_file() or not manifest:
            return self.backend.save(name)
        auth = self.read_auth(source)
        identity = account_identity(auth, self.provider)
        fingerprints = set(login_fingerprints(auth, self.provider))
        if any(record.get("ownership") == "local" and record["account_id"] == identity
               and fingerprints.intersection(record.get("fingerprints", [])) for record in manifest.values()):
            return self.backend.save(name)
        cloud = next((alias for alias, record in manifest.items()
                      if record["account_id"] == identity and record.get("ownership", "cloud") == "cloud"), None)
        if cloud:
            args = SimpleNamespace(fresh_login=True, auth_file=source, local_profile=name,
                                   store=self.store, provider=self.provider)
            accounts.register_local(args, manifest, accounts.connect(self.provider), cloud)
        return self.backend.save(name)

    def local_registrations(self, auth, manifest):
        identity = account_identity(auth, self.provider)
        fingerprints = set(login_fingerprints(auth, self.provider))
        local = {alias: {**record, "cloud_enrolled": True} for alias, record in manifest.items()
                 if record["account_id"] == identity and record.get("ownership") == "local"}
        if any(fingerprints.intersection(record["fingerprints"]) for record in local.values()):
            raise WorkerError("Remote credentials reuse a local token or provider session; complete a separate sign-in")
        for alias, path in accounts.saved_profiles(self.store).items():
            saved = json.loads(path.read_text())
            if account_identity(saved, self.provider) != identity:
                continue
            record = manifest.get(alias, {})
            known = set(record.get("fingerprints", [])) | set(login_fingerprints(saved, self.provider))
            if record.get("ownership", "local") != "local" or fingerprints.intersection(known):
                raise WorkerError("Remote credentials reuse a local token or provider session; complete a separate sign-in")
            local[alias] = {**record, "account_id": identity, "ownership": "local",
                            "session_id": record.get("session_id", login_id(saved, self.provider)),
                            "fingerprints": sorted(known), "cloud_enrolled": True}
        return local

    def save_remote(self, name, source, client):
        auth = self.read_auth(source)
        identity = account_identity(auth, self.provider)
        fingerprints = set(login_fingerprints(auth, self.provider))
        manifest = self.manifest()
        self.store.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.store.chmod(0o700)
        local = self.local_registrations(auth, manifest)
        try:
            client.enroll(auth, aliases=[name])
        except WorkerError as error:
            if error.status != 409:
                raise
            existing = client.describe(identity, aliases=[])
            if existing["session_id"] != login_id(auth, self.provider):
                raise WorkerError("This account already has remote credentials; delete its remote profile before replacing them") from None
        # Publish both sets before registration so an interrupted operation never
        # makes existing local profiles look like unregistered cloud credentials.
        key = remote_profile_key(name)
        manifest[key] = {"account_id": identity, "ownership": "cloud", "display_name": name,
                         "fingerprints": sorted(fingerprints | set(manifest.get(key, {}).get("fingerprints", [])))}
        manifest.update(local)
        accounts.write_private(self.store / REMOTE_MANIFEST, manifest)
        for record in local.values():
            checked = client.register_local(identity, record["session_id"], record["fingerprints"])
            manifest[key]["fingerprints"] = sorted(set(manifest[key]["fingerprints"]) | set(checked["cloud_fingerprints"]))
        client.describe(identity, aliases=[name])
        accounts.write_private(self.store / REMOTE_MANIFEST, manifest)
        print(f"{self.backend.command}: saved remote profile {name!r}")
        print("  Local credentials and selection are preserved.")

    def list_remote(self):
        manifest = self.manifest(migrate=False)
        snapshot = accounts.connect(self.provider).snapshot()
        print(f"{self.backend.command}: remote profiles")
        for row in snapshot["accounts"]:
            names = sorted({label for alias, label in remote_names(manifest).items()
                            if manifest[alias]["account_id"] == row["account_id"]})
            names = names or row["aliases"] or [row["account_id"]]
            print(f"  {', '.join(names)} ({row['cloud_state']}; {row['account_id']})")
        if not snapshot["accounts"]:
            print("  No remote profiles")
        return 0

    def remote_login(self, client, identity):
        row, _ = client.get(identity)
        auth = json.loads(row["auth"]) if isinstance(row["auth"], str) else row["auth"]
        if account_identity(auth, self.provider) != identity:
            raise WorkerError("Remote credentials belong to another account")
        metadata = row.get("cloud_login", {})
        return (set(metadata.get("fingerprints", [])) | set(login_fingerprints(auth, self.provider)),
                metadata.get("session_id", login_id(auth, self.provider)))

    def remove_legacy_archive(self, alias, record):
        archive = legacy_archive(self.store, alias, record)
        candidates = [archive] if archive is not None else []
        if record.get("legacy_alias"):
            candidates.append(self.store / "remote-staged" / f"auth.json.{record['legacy_alias']}")
        present = []
        for path in candidates:
            if path.parent.is_symlink():
                raise WorkerError("Legacy remote directory was replaced")
            if path.exists() or path.is_symlink():
                if path.is_symlink() or accounts.digest(path) != record.get("source_sha256"):
                    raise WorkerError("Legacy remote archive changed; refusing to remove it")
                present.append(path)
        for path in present:
            path.unlink()

    def _prompt_profiles(self, names):
        import rotate_auth
        try:
            selected = rotate_auth.prompt_profile_selection(self.backend.command, names)
        except (KeyboardInterrupt, EOFError):
            fail(f"{self.backend.command}: migration cancelled")
        if not selected:
            fail(f"{self.backend.command}: migration cancelled")
        return selected

    def migrate_single_logins(self, *, choose=False):
        """Explicit one-time transfer of accounts with only one configured login."""
        manifest = self.manifest()
        if any(record.get("reclaim") or record.get("remove") for record in manifest.values()):
            raise WorkerError("Finish the pending account transfer or deletion before migrating profiles")
        client = accounts.connect(self.provider)
        remote_ids = {row["account_id"] for row in client.snapshot()["accounts"]}
        groups = {}
        for name, path in accounts.saved_profiles(self.store).items():
            auth = self.read_auth(path)
            identity = account_identity(auth, self.provider)
            groups.setdefault(identity, []).append((name, path, auth, set(login_fingerprints(auth, self.provider))))
        planned = []
        for identity, profiles in groups.items():
            if identity in remote_ids:
                known, _ = self.remote_login(client, identity)
                transfer = []
                for name, path, auth, fingerprints in profiles:
                    record = manifest.get(name, {})
                    if record.get("ownership") == "local" and not fingerprints.intersection(known):
                        # A registered independent login is already a second
                        # session. Keep its local credentials and session ID.
                        continue
                    if not fingerprints.intersection(known):
                        raise WorkerError(f"Cannot establish whether {name!r} is an independent local login")
                    transfer.append((name, path, auth, fingerprints))
            else:
                sessions = []
                for name, _, _, fingerprints in profiles:
                    session_id = manifest.get(name, {}).get("session_id")
                    matching = [session for session in sessions if fingerprints.intersection(session[0])
                                or session_id and session_id in session[1]]
                    combined = (set(fingerprints), {session_id} if session_id else set())
                    for session in matching:
                        combined[0].update(session[0])
                        combined[1].update(session[1])
                        sessions.remove(session)
                    sessions.append(combined)
                if len(sessions) != 1:
                    continue
                transfer = profiles
            if transfer:
                planned.append((identity, transfer))
        if choose:
            options = sorted({name for _, transfer in planned for name, *_ in transfer})
            if not options:
                print(f"{self.backend.command}: no profiles to migrate")
                self.cleanup_migrated_store()
                return 0
            selected = set(self._prompt_profiles(options))
            planned = [
                (identity, [item for item in transfer if item[0] in selected])
                for identity, transfer in planned
            ]
            planned = [(identity, transfer) for identity, transfer in planned if transfer]
        moved = 0
        for identity, transfer in planned:
            names = [name for name, _, _, _ in transfer]
            if any(manifest.get(remote_profile_key(name), {}).get("account_id", identity) != identity for name in names):
                raise WorkerError("Local and remote names identify different accounts; choose another remote name")
            paths = {path.resolve() for _, path, _, _ in transfer}
            if self.backend.auth.is_symlink() and self.backend.auth.resolve() in paths:
                self.backend.auth.unlink()
            if self.backend.current.is_symlink() and self.backend.current.resolve() in paths:
                self.backend.current.unlink()
            if identity not in remote_ids:
                try:
                    client.enroll(transfer[0][2], aliases=names)
                except WorkerError as error:
                    if error.status != 409:
                        raise
                    known, session_id = self.remote_login(client, identity)
                    if not transfer[0][3].intersection(known) and login_id(transfer[0][2], self.provider) != session_id:
                        raise WorkerError("Another remote login was enrolled; local credentials were kept") from None
                remote_ids.add(identity)
            client.describe(identity, aliases=names)
            # The compatibility reader finishes a journaled transfer before
            # normalization frees each name for an independent local login.
            for name, path, _, fingerprints in transfer:
                previous = manifest.pop(remote_profile_key(name), None)
                if previous:
                    if previous["account_id"] != identity:
                        raise WorkerError("Local and remote names identify different accounts; choose another remote name")
                    self.remove_legacy_archive(remote_profile_key(name), previous)
                    fingerprints = fingerprints | set(previous.get("fingerprints", []))
                manifest[name] = {"account_id": identity, "ownership": "cloud",
                                  "source_sha256": accounts.digest(path), "fingerprints": sorted(fingerprints)}
            accounts.write_private(self.store / REMOTE_MANIFEST, manifest)
            manifest = self.manifest()
            moved += len(transfer)
        # Confirm the controller has each remote account before removing old
        # handoff copies. Never upload an archive over refreshed credentials.
        for identity in sorted({record["account_id"] for alias, record in manifest.items() if alias in remote_names(manifest)}):
            labels = [label for alias, label in remote_names(manifest).items() if manifest[alias]["account_id"] == identity]
            client.describe(identity, aliases=labels)
            known, _ = self.remote_login(client, identity)
            for alias in remote_names(manifest):
                record = manifest[alias]
                if record["account_id"] != identity:
                    continue
                record["fingerprints"] = sorted(set(record.get("fingerprints", [])) | known)
                self.remove_legacy_archive(alias, record)
                record.pop("legacy_alias", None)
                record.pop("source_sha256", None)
        accounts.write_private(self.store / REMOTE_MANIFEST, manifest)
        self.cleanup_migrated_store()
        print(f"{self.backend.command}: moved {moved} single-login profiles to remote")
        return 0

    def migrate_remote_to_local(self, *, choose=False):
        """Transfer this host's remote-only accounts back into local profiles."""
        manifest = self.manifest()
        if any(record.get("remove") for record in manifest.values()):
            raise WorkerError("Finish the pending account deletion before migrating profiles")
        groups = {}
        for alias, record in manifest.items():
            if record.get("ownership", "cloud") != "cloud":
                continue
            groups.setdefault(record["account_id"], []).append(alias)
        local_identities = self.local_profile_identities()
        movable = []
        for identity, aliases in groups.items():
            registered = [name for name, record in manifest.items()
                          if record.get("account_id") == identity and record.get("ownership") == "local"]
            if registered:
                continue
            if any(manifest[alias].get("reclaim") for alias in aliases):
                movable.append(aliases)
                continue
            conflicts = local_identities.get(identity, [])
            if conflicts:
                raise WorkerError(
                    f"Local profile {conflicts[0]!r} already belongs to a remote account; refusing to overwrite it")
            movable.append(aliases)
        if choose:
            labeled = []
            for aliases in movable:
                label = ", ".join(sorted(manifest[alias].get("display_name", alias) for alias in aliases))
                labeled.append((label, aliases))
            labeled.sort(key=lambda item: item[0])
            if not labeled:
                print(f"{self.backend.command}: no profiles to migrate")
                self.cleanup_migrated_store()
                return 0
            selected = set(self._prompt_profiles([label for label, _ in labeled]))
            movable = [aliases for label, aliases in labeled if label in selected]
        moved = 0
        if movable:
            client = accounts.connect(self.provider)
            args = SimpleNamespace(store=self.store, provider=self.provider, destination_profile=None)
            for aliases in movable:
                accounts.reclaim(args, manifest, client, aliases[0])
                moved += len(aliases)
        self.cleanup_migrated_store()
        print(f"{self.backend.command}: moved {moved} remote profiles to local")
        return 0

    def local_profile_identities(self):
        found = {}
        for name, path in accounts.saved_profiles(self.store).items():
            try:
                auth = json.loads(path.read_text())
                found.setdefault(account_identity(auth, self.provider), []).append(name)
            except (OSError, ValueError, TypeError, WorkerError):
                raise WorkerError(f"Cannot read local profile {name!r}") from None
        return found

    def cleanup_migrated_store(self):
        for name in (CLOUD_DIRECTORY, "remote-staged"):
            archive_dir = self.store / name
            if archive_dir.is_dir() and not archive_dir.is_symlink() and not any(archive_dir.iterdir()):
                archive_dir.rmdir()
        # Old selections can already be dangling from an interrupted transfer.
        if self.backend.current.is_symlink() and not self.backend.current.exists():
            self.backend.current.unlink()
        if self.backend.auth.is_symlink() and not self.backend.auth.exists():
            self.backend.auth.unlink()

    def delete_remote(self, name):
        self.validate_name(name)
        manifest = self.manifest()
        client = accounts.connect(self.provider)
        matches = {record["account_id"] for alias, record in manifest.items()
                   if record.get("ownership", "cloud") == "cloud"
                   and (record.get("display_name", alias) == name or alias == name)}
        if not matches:
            matches = {row["account_id"] for row in client.snapshot()["accounts"]
                       if name in row["aliases"] or name == row["account_id"]}
        if len(matches) != 1:
            if not matches and self.login_home("remote", name).exists():
                return self.delete_pending("remote", name)
            raise WorkerError("Remote profile is unknown or ambiguous; use --remote --list")
        identity = matches.pop()
        aliases = [alias for alias in remote_names(manifest) if manifest[alias]["account_id"] == identity]
        archives = []
        for alias in aliases:
            path = legacy_archive(self.store, alias, manifest[alias])
            if path is None:
                continue
            if path.is_symlink() or (path.exists() and accounts.digest(path) != manifest[alias].get("source_sha256")):
                raise WorkerError("Remote transfer archive changed; refusing to remove it")
            if path.exists():
                archives.append(path)
        if not confirm_delete(self.backend.command, name, kind="remote account and all its remote aliases"):
            return 1
        key = aliases[0] if aliases else remote_profile_key(name)
        target = self.store / f"auth.json.{key}"
        if key not in aliases and (key in manifest or target.exists() or target.is_symlink()):
            raise WorkerError("Remote deletion journal conflicts with a saved local profile")
        record = manifest.setdefault(key, {"account_id": identity, "ownership": "cloud", "display_name": name})
        transfer = record.setdefault("remove", {"transfer_id": client.transfer_id})
        client.transfer_id = transfer["transfer_id"]
        accounts.write_private(self.store / REMOTE_MANIFEST, manifest)
        try:
            lease = client.claim(identity, "local-reclaim", allow_staged=True)
        except WorkerError as error:
            if error.status != 404:
                raise
        else:
            client.delete(identity, lease["lease"])
        for path in archives:
            path.unlink()
        for alias, value in list(manifest.items()):
            if value["account_id"] != identity:
                continue
            if value.get("ownership", "cloud") == "cloud":
                if "display_name" in value:
                    home = self.login_home("remote", value["display_name"])
                    self.check_login_home(home)
                    if home.exists():
                        shutil.rmtree(home)
                del manifest[alias]
            else:
                value["cloud_enrolled"] = False
        accounts.write_private(self.store / REMOTE_MANIFEST, manifest)
        print(f"{self.backend.command}: deleted remote account {name!r}; local credentials preserved")
        return 0

    def delete_pending(self, scope, name):
        self.validate_name(name)
        home = self.login_home(scope, name)
        self.check_login_home(home)
        if not home.exists():
            raise WorkerError(f"Unknown {scope} profile")
        if not confirm_delete(self.backend.command, name, kind=f"unfinished {scope} save"):
            return 1
        shutil.rmtree(home)
        print(f"{self.backend.command}: discarded unfinished {scope} save {name!r}")
        return 0

    def delete_local(self, name):
        target = self.store / f"auth.json.{name}"
        if target.exists() or target.is_symlink() or name in self.manifest():
            result = self.backend.delete(name)
            if result == 0 and name not in {".", "..", "lock"}:
                home = self.login_home("local", name)
                self.check_login_home(home)
                if home.exists():
                    shutil.rmtree(home)
            return result
        return self.delete_pending("local", name)


def manage(backend, scope, action, name, *, choose=False):
    manager = ScopedAccounts(backend)
    try:
        if action == "save":
            return manager.save(scope, name)
        if action == "list":
            return manager.list_remote()
        if action == "migrate-from-local-to-remote":
            return manager.migrate_single_logins(choose=choose)
        if action == "migrate-from-remote-to-local":
            return manager.migrate_remote_to_local(choose=choose)
        if action == "delete":
            return manager.delete_remote(name) if scope == "remote" else manager.delete_local(name)
    except WorkerError as error:
        fail(f"{backend.command}: {error}")
    except (OSError, ValueError, TypeError):
        if str(action).startswith("migrate-from-"):
            fail(f"{backend.command}: account operation did not finish; retry the same migration")
        fail(f"{backend.command}: account operation did not finish; retry with the same scope and name")
    except KeyboardInterrupt:
        if str(action).startswith("migrate-from-"):
            fail(f"{backend.command}: account operation interrupted; retry the same migration")
        fail(f"{backend.command}: account operation interrupted; retry with the same scope and name")
    raise AssertionError(action)
