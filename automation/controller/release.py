"""Identify runtime inputs independently of the target repository's task commit."""

import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import re
import subprocess
import tarfile
from urllib.parse import quote
import zipfile

from .workflows import ENGINE

# Entire directory trees are inputs, including imported helpers, workflow/action
# definitions, policy/configuration, dependencies and infrastructure configuration.
# Target docs/skills are read from the task checkout rather than this runtime.
INPUT_ROOTS=(".github","automation","infra","scripts")


def tree_digest(entries):
    indexed={entry["path"]:entry for entry in entries}
    if any(root not in indexed or indexed[root].get("type")!="tree"
           or not re.fullmatch(r"[0-9a-f]{40}",indexed[root].get("sha", "")) for root in INPUT_ROOTS):
        raise ValueError("Incomplete runtime input trees")
    return hashlib.sha256(json.dumps({root:indexed[root]["sha"] for root in INPUT_ROOTS},sort_keys=True).encode()).hexdigest()


def local_manifest(root,revision):
    if not re.fullmatch(r"[0-9a-f]{40}",revision):
        raise ValueError("A full source commit is required")
    result=subprocess.run(["git","ls-tree","-z",revision,"--",*INPUT_ROOTS],cwd=root,capture_output=True,check=True)
    entries=[]
    for line in result.stdout.split(b"\0"):
        if line:
            info,path=line.decode().split("\t",1)
            _,kind,sha=info.split()
            entries.append({"path":path,"type":kind,"sha":sha})
    return {"revision":revision,"digest":tree_digest(entries),"input_roots":list(INPUT_ROOTS),"format":1}


class RequiredRuntime:
    def __init__(self,github,manifest,*,branch="main"):
        self.github,self.manifest,self.branch=github,manifest,branch

    def __call__(self):
        token=self.github.installation_token(ENGINE,permissions={"contents":"read"})
        commit=self.github.request(f"https://api.github.com/repos/{ENGINE}/commits/{quote(self.branch,safe='')}",token=f"Bearer {token}")
        tree=self.github.request(f"https://api.github.com/repos/{ENGINE}/git/trees/{commit['commit']['tree']['sha']}",token=f"Bearer {token}")
        if tree.get("truncated"):
            raise ValueError("Incomplete runtime input observation")
        return tree_digest(tree["tree"])==self.manifest["digest"]


def package(root,revision,destination):
    manifest=local_manifest(root,revision)
    source=subprocess.run(["git","archive","--format=tar",revision,*INPUT_ROOTS],cwd=root,capture_output=True,check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(source)) as archive, zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as output:
        for entry in archive.getmembers():
            if entry.isdir():
                continue
            path=PurePosixPath(entry.name)
            if (not entry.isfile() or path.is_absolute() or ".." in path.parts or path.parts[0] not in INPUT_ROOTS):
                raise ValueError("Unsupported runtime archive entry")
            item=zipfile.ZipInfo(entry.name)
            item.create_system=3
            item.compress_type=zipfile.ZIP_DEFLATED
            item.external_attr=(0o100000|entry.mode)<<16
            output.writestr(item,archive.extractfile(entry).read())
        output.writestr("release.json",json.dumps(manifest,sort_keys=True))
    return manifest
