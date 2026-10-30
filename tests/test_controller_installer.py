"""Exercise the release switch with a fake service manager and temporary paths."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT=Path(__file__).resolve().parents[1]
SERVICE="clusterfork-controller.service"
COMMANDS=["daemon-reload",f"stop {SERVICE}",f"show --property=ActiveState --value {SERVICE}"]


class ControllerInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.previous=self.root/"previous"
        self.release=self.root/"release"
        self.previous.mkdir()
        self.release.mkdir()
        self.current=self.root/"current"
        self.current.symlink_to(self.previous)
        self.calls=self.root/"calls"
        installer=(ROOT/"automation/controller/host/install.sh").read_text()
        self.cutover=installer[installer.index("cat >/etc/systemd/system/"):installer.index("cf_hostname=")]
        for source,target in (
            ("/etc/systemd/system/clusterfork-controller.service",'"$cf_unit"'),
            ("/usr/local/bin/clusterfork-controller",'"$cf_cli"'),
            ("/opt/clusterfork/current",'"$cf_current"'),
        ):
            self.cutover=self.cutover.replace(source,target)

    def install(self,*,reload_status=0,stop_status=0,state="inactive",show_status=0):
        script=r'''
set -euo pipefail
cf_release=$1
cf_unit=$2
cf_cli=$3
cf_current=$4
cf_calls=$5
cf_reload_status=$6
cf_stop_status=$7
cf_state=$8
cf_show_status=$9
cf_reloaded=false
systemctl() {
    printf '%s\n' "$*" >>"$cf_calls"
    case "$1" in
        daemon-reload)
            test -s "$cf_unit" || return 10
            cf_reloaded=true
            return "$cf_reload_status"
            ;;
        stop)
            test "$cf_reloaded" = true || return 11
            return "$cf_stop_status"
            ;;
        show)
            printf '%s\n' "$cf_state"
            return "$cf_show_status"
            ;;
        *) return 12 ;;
    esac
}
'''
        return subprocess.run([
            "bash","-c",script+self.cutover,"installer-fixture",
            str(self.release),str(self.root/"unit"),str(self.root/"cli"),str(self.current),str(self.calls),
            str(reload_status),str(stop_status),state,str(show_status),
        ],capture_output=True,text=True,timeout=10)

    def assert_preserved(self,result,commands):
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.current.resolve(),self.previous)
        self.assertEqual(self.calls.read_text().splitlines(),commands)

    def test_reload_stop_and_inactive_verification_precede_release_switch(self):
        result=self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.current.resolve(),self.release)
        self.assertEqual(self.calls.read_text().splitlines(),COMMANDS)

    def test_first_install_loads_the_written_unit_before_stopping_it(self):
        self.current.unlink()
        result=self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.current.resolve(),self.release)
        self.assertEqual(self.calls.read_text().splitlines(),COMMANDS)

    def test_failed_reload_preserves_current_release(self):
        self.assert_preserved(self.install(reload_status=1),COMMANDS[:1])

    def test_failed_stop_preserves_current_release(self):
        self.assert_preserved(self.install(stop_status=1),COMMANDS[:2])

    def test_noninactive_or_unverifiable_service_preserves_current_release(self):
        for state,show_status in (("active",0),("deactivating",0),("failed",0),("",0),("inactive",1)):
            with self.subTest(state=state,show_status=show_status):
                self.calls.unlink(missing_ok=True)
                self.assert_preserved(self.install(state=state,show_status=show_status),COMMANDS)


if __name__=="__main__":
    unittest.main()
