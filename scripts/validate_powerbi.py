"""Validate generated PBIR against Microsoft's public JSON schemas.

Run with jsonschema installed. --download-schemas permits downloads of public
Microsoft schemas only; no report data is transmitted. Cache stays under tmp/.
This does not execute DAX/M or substitute for opening Power BI Desktop.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
import warnings

import jsonschema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-schemas", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cache = root / "tmp" / "schema-cache"
    cache.mkdir(parents=True, exist_ok=True)

    def retrieve(uri):
        prefix = "https://developer.microsoft.com/json-schemas/fabric/"
        if not uri.startswith(prefix):
            raise ValueError(f"Only Microsoft Fabric schemas are allowed: {uri}")
        target = cache / (hashlib.sha256(uri.encode()).hexdigest() + ".json")
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
        if not args.download_schemas:
            raise ValueError("Schema missing from cache; use --download-schemas")
        # Microsoft's 2.1 visual schema advertises a dotted embedded $id,
        # while the published file uses a hyphenated filename.
        download_uri = uri.replace("/schema.embedded.json", "/schema-embedded.json")
        with urllib.request.urlopen(download_uri, timeout=30) as response:
            data = json.load(response)
        target.write_text(json.dumps(data), encoding="utf-8")
        return data

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    checked = 0
    failures = []
    for path in sorted((root / "powerbi").rglob("*")):
        if not path.is_file() or path.suffix not in [".json", ".pbip", ".pbir", ".pbism"]:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "$schema" not in data:
            continue
        schema = retrieve(data["$schema"])
        resolver = jsonschema.RefResolver.from_schema(schema, handlers={"https": retrieve})
        validator = jsonschema.Draft7Validator(schema, resolver=resolver)
        for error in validator.iter_errors(data):
            failures.append({"file": str(path.relative_to(root)), "location": ".".join(map(str, error.path)), "message": error.message})
        checked += 1
    result = {"schema_checked_files": checked, "errors": failures, "native_desktop_render_tested": False}
    output = root / "output" / "powerbi_validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
