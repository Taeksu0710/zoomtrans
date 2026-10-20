#!/usr/bin/env python3
"""Refresh vendored upstream license notices; not run during normal builds."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import subprocess

ROOT = Path(__file__).resolve().parents[1] / "packaging/licenses"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ZoomTrans-release"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gh", help="Authenticated GitHub CLI path (avoids anonymous API rate limits)")
    args = parser.parse_args()
    def api(path):
        if args.gh:
            return json.loads(subprocess.check_output([args.gh, "api", path]))
        return json.loads(fetch("https://api.github.com/" + path))
    entries = []
    for repository in ("qtbase", "qtsvg", "qtimageformats"):
        tree = api(f"repos/qt/{repository}/git/trees/v6.8.3?recursive=1")
        for item in tree["tree"]:
            path = item["path"]
            name = Path(path).name.lower()
            if item["type"] == "blob" and (path.startswith("LICENSES/") or
                    ("3rdparty/" in path and (any(x in name for x in ("license", "copying", "copyright", "notice"))
                                              or name == "qt_attribution.json"))):
                url = f"https://raw.githubusercontent.com/qt/{repository}/v6.8.3/{path}"
                entries.append((repository + "/" + path, url))
    commit = api("repos/ml-explore/mlx-examples/commits?path=LICENSE&per_page=1")[0]["sha"]
    entries.append(("mlx-whisper/LICENSE", f"https://raw.githubusercontent.com/ml-explore/mlx-examples/{commit}/LICENSE"))
    def save(entry):
        relative, url = entry
        data = fetch(url)
        destination = ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return {"path": relative, "source": url, "sha256": hashlib.sha256(data).hexdigest()}
    with ThreadPoolExecutor(max_workers=6) as executor:
        records = list(executor.map(save, entries))
    (ROOT / "sources.json").write_text(json.dumps(records, indent=2) + "\n")
    print(f"Vendored {len(records)} upstream license/attribution files")


if __name__ == "__main__":
    main()
