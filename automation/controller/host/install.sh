#!/bin/bash
# Invoked by the trusted deployment command after CI and verified drain.
set -euo pipefail
umask 077
cf_revision='__REVISION__'
cf_config_b64='__CONFIG_B64__'
cf_manifest_b64='__MANIFEST_B64__'
cf_migration_b64='__MIGRATION_B64__'
exec 9>/run/clusterfork-install.lock
flock -n 9
exec >>/var/log/clusterfork-install.log 2>&1

python3 - "$cf_config_b64" <<'PY'
import base64,json,sys,urllib.request
expected=json.loads(base64.b64decode(sys.argv[1]))['vm_resource_id']
request=urllib.request.Request('http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01',headers={'Metadata':'true'})
with urllib.request.urlopen(request,timeout=5) as response:
    observed=json.load(response)['resourceId']
assert observed.lower()==expected.lower(),'Wrong controller host'
PY

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv git curl caddy
id clusterfork >/dev/null 2>&1 || useradd --system --home-dir /var/lib/clusterfork --shell /usr/sbin/nologin clusterfork

# Only the dedicated Azure LUN is eligible. Never format a device with signatures
# or mount over an existing database directory.
cf_disk=/dev/disk/azure/scsi1/lun0
test -b "$cf_disk"
test "$(lsblk -dn -o TYPE "$(readlink -f "$cf_disk")")" = disk
if ! blkid "$cf_disk" >/dev/null 2>&1; then
    test -z "$(wipefs -n --noheadings -o TYPE "$cf_disk")"
    mkfs.ext4 -q "$cf_disk"
fi
test "$(blkid -s TYPE -o value "$cf_disk")" = ext4
cf_uuid=$(blkid -s UUID -o value "$cf_disk")
mkdir -p /var/lib/clusterfork
if mountpoint -q /var/lib/clusterfork; then
    test "$(findmnt -n -o UUID --target /var/lib/clusterfork)" = "$cf_uuid"
else
    test -z "$(find /var/lib/clusterfork -mindepth 1 -maxdepth 1 -print -quit)"
    mount -o nodev,nosuid,noexec "$cf_disk" /var/lib/clusterfork
fi
if ! findmnt --fstab --mountpoint /var/lib/clusterfork >/dev/null; then
    printf 'UUID=%s /var/lib/clusterfork ext4 defaults,nodev,nosuid,noexec 0 2\n' "$cf_uuid" >>/etc/fstab
fi
chown clusterfork:clusterfork /var/lib/clusterfork
chmod 700 /var/lib/clusterfork

mkdir -p /opt/clusterfork/releases /var/cache/clusterfork /etc/clusterfork
chmod 755 /opt/clusterfork /opt/clusterfork/releases /etc/clusterfork
if ! test -d /var/cache/clusterfork/source.git; then
    git init --bare -q /var/cache/clusterfork/source.git
fi
git --git-dir=/var/cache/clusterfork/source.git fetch --no-tags --depth=1 https://github.com/KianBahasadri/clusterfork.git "$cf_revision"
test "$(git --git-dir=/var/cache/clusterfork/source.git rev-parse FETCH_HEAD)" = "$cf_revision"
cf_release=/opt/clusterfork/releases/$cf_revision
if ! test -f "$cf_release/.installed"; then
    mkdir -p "$cf_release"
    git --git-dir=/var/cache/clusterfork/source.git archive "$cf_revision" .github automation infra scripts | tar -x -C "$cf_release"
    printf '%s' "$cf_manifest_b64" | base64 -d >"$cf_release/release.json"
    python3 - "$cf_release" /var/cache/clusterfork/source.git <<'PY'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(root/'automation'))
from controller.release import local_manifest
manifest=json.loads((root/'release.json').read_text())
assert local_manifest(sys.argv[2],manifest['revision'])==manifest,'Runtime tree identity mismatch'
PY
    python3 -m venv "$cf_release/.venv"
    "$cf_release/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$cf_release/automation/controller/requirements.txt"
    "$cf_release/.venv/bin/python" -c "import sys; sys.path[:0]=['$cf_release/automation','$cf_release/automation/azure']; import controller_main"
    touch "$cf_release/.installed"
fi
# Installed source is readable by the service, writable only by root.
chmod -R u=rwX,go=rX "$cf_release"
printf '%s' "$cf_config_b64" | base64 -d >/etc/clusterfork/controller.json
chmod 644 /etc/clusterfork/controller.json
if test -n "$cf_migration_b64"; then
    printf '%s' "$cf_migration_b64" | base64 -d >/etc/clusterfork/migration.json
    chown root:clusterfork /etc/clusterfork/migration.json
    chmod 640 /etc/clusterfork/migration.json
fi

cat >/etc/systemd/system/clusterfork-controller.service <<'UNIT'
[Unit]
Description=Clusterfork task controller
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/var/lib/clusterfork
ConditionPathIsMountPoint=/var/lib/clusterfork

[Service]
User=clusterfork
Group=clusterfork
ExecStart=/opt/clusterfork/current/.venv/bin/python /opt/clusterfork/current/automation/controller_main.py serve
Restart=on-failure
RestartSec=10
TimeoutStopSec=90
RuntimeDirectory=clusterfork
RuntimeDirectoryMode=0700
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/clusterfork /run/clusterfork

[Install]
WantedBy=multi-user.target
UNIT
cat >/usr/local/bin/clusterfork-controller <<'CLI'
#!/bin/sh
exec /opt/clusterfork/current/.venv/bin/python /opt/clusterfork/current/automation/controller_main.py "$@"
CLI
chmod 755 /usr/local/bin/clusterfork-controller

# Load the written unit on first installation, then require the old owner to stop
# before switching the release. There is never an overlapping task owner.
systemctl daemon-reload
systemctl stop clusterfork-controller.service
cf_service_state=$(systemctl show --property=ActiveState --value clusterfork-controller.service)
test "$cf_service_state" = inactive
ln -sfn "$cf_release" /opt/clusterfork/current
cf_hostname=$(python3 -c 'import json; print(json.load(open("/etc/clusterfork/controller.json"))["hostname"])')
printf '%s {\n handle /api/* {\n  reverse_proxy 127.0.0.1:8765\n }\n respond 404\n}\n' "$cf_hostname" >/etc/caddy/Caddyfile
chmod 644 /etc/caddy/Caddyfile
systemctl enable --now clusterfork-controller.service
systemctl enable --now caddy.service
systemctl reload caddy.service
