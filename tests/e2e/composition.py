"""Emit the source composition with the upstream rendering function."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apis/cloudflareworkers"
composition = yaml.safe_load((API / "composition.yaml").read_text())
renderer = composition["spec"]["pipeline"][0]
renderer["functionRef"]["name"] = "function-go-templating"
renderer["input"] = {
    "apiVersion": "gotemplating.fn.crossplane.io/v1beta1",
    "kind": "GoTemplate",
    "source": "Inline",
    "inline": {"template": "\n".join(path.read_text() for path in sorted(
        (ROOT / "functions/render").glob("*.gotmpl")
    ))},
}
print(json.dumps(composition))
