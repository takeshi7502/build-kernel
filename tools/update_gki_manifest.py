"""Refresh the bot's portable version catalog from a reviewed local GKI clone.

Preview by default; --apply updates only bot/gki_manifest.json, never workflows.
Requires tools/requirements.txt. No GitHub requests or credentials are used.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path


def build_manifest(repository):
    from ruamel.yaml import YAML

    repository = Path(repository).resolve()
    yaml = YAML(typ="safe")
    targets = {}
    for path in sorted((repository / ".github/workflows").glob("kernel-a*.yml")):
        workflow = yaml.load(path.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["prepare-matrix"]["steps"]
        env = next(step["env"] for step in steps if "MATRIX_JSON" in step.get("env", {}))
        android, kernel = env["ANDROID_VERSION"], str(env["KERNEL_VERSION"])
        rows = json.loads(env["MATRIX_JSON"])
        by_sub = {
            str(row["sub_level"]): {**row, "revision": row.get("revision", "r1")} for row in rows
        }
        data = json.loads(
            (repository / "data" / android / f"{kernel}.json").read_text(encoding="utf-8")
        )
        for entry in sorted(data["entries"], key=lambda entry: entry["date"], reverse=True):
            if not re.fullmatch(re.escape(kernel) + r"\.\d+", entry["kernel"]):
                raise ValueError(f"Invalid version: {entry['kernel']}")
            sub = entry["kernel"].rsplit(".", 1)[-1]
            by_sub.setdefault(
                sub, {"sub_level": sub, "os_patch_level": entry["date"], "revision": "r1"}
            )
        if kernel == "6.12":
            by_sub.pop("X", None)
        builds = [
            by_sub[sub]
            for sub in sorted(by_sub, key=lambda sub: (sub == "X", int(sub) if sub != "X" else 0))
        ]
        targets[f"build_a{android.removeprefix('android')}_{kernel.replace('.', '_')}"] = {
            "android": android,
            "kernel": kernel,
            "builds": builds,
        }
    if len(targets) != 5:
        raise ValueError("Expected five modern GKI workflows with prepare-matrix")
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "upstream/dev"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {"schema_version": 1, "upstream_commit": result.stdout.strip(), "targets": targets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        content = json.dumps(build_manifest(args.repository), ensure_ascii=False, indent=2) + "\n"
        if args.apply:
            destination = Path(__file__).resolve().parents[1] / "bot/gki_manifest.json"
            destination.write_text(content, encoding="utf-8")
            print(f"Updated {destination}; review and commit before deploying.")
        else:
            print(content, end="")
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Cannot refresh GKI catalog: {error}\n")


if __name__ == "__main__":
    main()
