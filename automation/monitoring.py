"""Provision bounded Azure monitoring without reading or printing runtime secrets."""

import json
import os
from pathlib import Path
import tempfile


def configure(names, location, command):
    template = Path(__file__).with_name("monitoring.json")
    command(["az", "deployment", "group", "create", "-g", names["group"], "-n", "clusterfork-monitoring",
             "--template-file", str(template), "--parameters", f"functionAppName={names['app']}",
             f"location={location}", "-o", "none", "--only-show-errors"], sensitive=True)
    connection = command(["az", "resource", "show", "-g", names["group"], "-n", names["app"] + "-insights",
                          "--resource-type", "Microsoft.Insights/components", "--api-version", "2020-02-02",
                          "--query", "properties.ConnectionString", "-o", "tsv", "--only-show-errors"], sensitive=True)
    if not connection.startswith("InstrumentationKey="):
        raise RuntimeError("Azure did not return an Application Insights connection string")
    with tempfile.TemporaryDirectory(prefix="clusterfork-monitoring-") as directory:
        path = Path(directory) / "settings.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"APPLICATIONINSIGHTS_CONNECTION_STRING": connection}, stream)
        command(["az", "functionapp", "config", "appsettings", "set", "-g", names["group"], "-n", names["app"],
                 "--settings", f"@{path}", "-o", "none", "--only-show-errors"], sensitive=True)
    print(f"Monitoring enabled: {names['app']}-insights; retention 30 days; workspace ingestion cap 0.1 GB/day.")
