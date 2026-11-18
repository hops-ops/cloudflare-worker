"""Exercise the actual Crossplane function pipeline with staged observations."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apis/cloudflareworkers"
COMPONENT_KEY = "crossplane.io/composition-resource-name"
DIGEST = "sha256:" + "a" * 64
WORKER_API = "workers.upjet-cloudflare.m.upbound.io/v1alpha1"
ZERO_API = "zero.upjet-cloudflare.m.upbound.io/v1alpha1"
WORKLOAD_API = "workloads.hops.ops.com.ai/v1alpha1"


def fixture(name):
    return yaml.safe_load((ROOT / "examples/cloudflareworkers" / f"{name}.yaml").read_text())


def observed(xr, key, api, kind, name, external_name=None, **status):
    annotations = {COMPONENT_KEY: key}
    if external_name:
        annotations["crossplane.io/external-name"] = external_name
    return {
        "apiVersion": api,
        "kind": kind,
        "metadata": {"name": name, "namespace": xr["metadata"]["namespace"], "annotations": annotations},
        "status": {"conditions": [{"type": "Ready", "status": "True"}], **status},
    }


def render(xr, observed_resources=(), profile=None):
    scratch = ROOT / ".tmp"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        folder = Path(tmp)
        claim = folder / "xr.yaml"
        claim.write_text(yaml.safe_dump(xr))
        platform = ROOT / "examples/platform.yaml"
        if profile is not None:
            platform = folder / "platform.yaml"
            platform.write_text(yaml.safe_dump(profile))
        args = ["up", "composition", "render", f"--xrd={API / 'definition.yaml'}",
                str(API / "composition.yaml"), str(claim), f"--extra-resources={platform}", "--quiet"]
        if observed_resources:
            path = folder / "observed.yaml"
            path.write_text(yaml.safe_dump_all(observed_resources))
            args.append(f"--observed-resources={path}")
        output = subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.PIPE)
        resources = [doc for doc in yaml.safe_load_all(output) if doc]
        schemas = os.environ.get("CLOUDFLARE_WORKER_SCHEMAS")
        if schemas:
            subprocess.run(["crossplane", "resource", "validate", schemas, "-",
                            "--error-on-missing-schemas"], input=yaml.safe_dump_all(resources[1:]),
                           text=True, check=True, stdout=subprocess.DEVNULL)
        return resources[0], {r["metadata"]["annotations"][COMPONENT_KEY]: r for r in resources[1:]}


class WorkerRender(unittest.TestCase):
    def test_default_upload_keeps_endpoint_disabled(self):
        xr = fixture("minimal")
        profile = yaml.safe_load((ROOT / "examples/platform.yaml").read_text())
        profile["data"] = {key: profile["data"][key] for key in ("accountId", "providerConfigRef")}
        _, first = render(xr, profile=profile)
        self.assertEqual(set(first), {"worker"})
        self.assertEqual(first["worker"]["spec"]["artifact"], xr["spec"]["artifact"])
        script = observed(xr, "worker", WORKER_API, "Script", "worker", "example-worker",
                          resolvedArtifactDigest=DIGEST)
        state, resources = render(xr, [script], profile)
        self.assertFalse(resources["endpoint"]["spec"]["forProvider"]["enabled"])
        self.assertFalse(resources["endpoint"]["spec"]["forProvider"]["previewsEnabled"])
        self.assertNotIn("url", state["status"])
        self.assertEqual(state["status"]["resolvedArtifactDigest"], DIGEST)

    def test_public_is_explicit_and_needs_no_access_or_network_stack(self):
        xr = fixture("public")
        xr["metadata"].update(name="123", namespace="true")
        xr["spec"]["workerName"] = "null"
        worker_name = xr["spec"]["workerName"]
        script = observed(xr, "worker", WORKER_API, "Script", "123", worker_name)
        endpoint = observed(xr, "endpoint", WORKER_API, "ScriptSubdomain", "123-endpoint", worker_name,
                            atProvider={"enabled": True})
        state, resources = render(xr, [script, endpoint])
        self.assertEqual(state["status"]["url"], f"https://{worker_name}.example.workers.dev")
        self.assertEqual(set(resources), {"worker", "endpoint"})
        self.assertEqual(resources["worker"]["metadata"]["name"], "123")
        for resource in resources.values():
            self.assertEqual(resource["metadata"]["namespace"], "true")
            self.assertEqual(resource["spec"]["forProvider"]["scriptName"], worker_name)
        self.assertTrue(resources["endpoint"]["spec"]["forProvider"]["enabled"])
        self.assertFalse(resources["endpoint"]["spec"]["forProvider"]["previewsEnabled"])

    def test_private_access_gates_the_worker_and_relay_uses_only_its_connection_secret(self):
        xr = fixture("private")
        xr["spec"]["workerName"] = "true"
        worker_name = xr["spec"]["workerName"]
        fixtures = [
            observed(xr, "worker", WORKER_API, "Script", "gateway", worker_name),
            observed(xr, "endpoint", WORKER_API, "ScriptSubdomain", "gateway-endpoint", worker_name),
            observed(xr, "access-slot", ZERO_API, "TrustAccessApplication", "gateway-access", "id"),
            observed(xr, "relay", WORKLOAD_API, "WebService", "gateway", url="https://gateway.private.example"),
        ]
        state, resources = render(xr, fixtures)
        worker = resources["worker"]["spec"]["forProvider"]
        endpoint = resources["endpoint"]["spec"]["forProvider"]
        self.assertNotIn("scriptName", worker)
        self.assertEqual(worker["accessApplicationRef"]["name"], "gateway-access")
        self.assertEqual(endpoint["accessApplicationRef"], worker["accessApplicationRef"])
        self.assertFalse(endpoint["previewsEnabled"])
        access = resources["access-slot"]["spec"]["forProvider"]
        self.assertEqual(access["name"], worker_name)
        self.assertEqual(resources["access-token"]["spec"]["forProvider"]["name"], worker_name)
        self.assertEqual(access["domain"], f"{worker_name}.example.workers.dev")
        policies = access["policies"]
        self.assertEqual([policy["decision"] for policy in policies], ["non_identity"])
        self.assertEqual(policies[0]["include"][0]["serviceToken"]["tokenIdRef"]["name"], "gateway-access-token")
        relay = resources["relay"]["spec"]
        self.assertEqual(relay["exposure"], "private")
        if os.environ.get("CLOUDFLARE_WORKER_VALIDATE_RELAY"):
            config = next(env["value"] for env in relay["env"] if env["name"] == "RELAY_CONFIG")
            subprocess.run(
                ["docker", "run", "--rm", "-i", "--user", "65532:65532", relay["image"],
                 "caddy", "validate", "--config", "/dev/stdin", "--adapter", "caddyfile"],
                input=config, text=True, check=True,
            )
        self.assertEqual(
            [env["valueFrom"]["secretKeyRef"] for env in relay["env"] if "valueFrom" in env],
            [{"name": "gateway-relay-credentials", "key": name} for name in ("client_id", "client_secret")],
        )
        self.assertEqual(state["status"]["url"], "https://gateway.private.example")
        self.assertEqual({key for key in resources if key.startswith("protect-")},
                         {"protect-worker", "protect-endpoint", "protect-access-slot"})
        origin = next(r for r in resources.values() if r["kind"] == "DirectoryService")
        self.assertEqual(worker["bindings"][0]["serviceIdRef"]["name"], origin["metadata"]["name"])
        self.assertEqual(origin["spec"]["forProvider"]["httpPort"], 8080)

        # An opt-out removes composed VPC resources and leaves existing ingress in place.
        opt_out = copy.deepcopy(xr)
        opt_out["spec"]["privateNetworking"]["enabled"] = False
        _, disabled = render(opt_out, fixtures)
        self.assertNotIn("bindings", disabled["worker"]["spec"]["forProvider"])
        self.assertFalse(any(r["kind"] == "DirectoryService" for r in disabled.values()))
        self.assertEqual(disabled["relay"], resources["relay"])

    def test_private_requires_platform_settings(self):
        xr = fixture("private")
        base = yaml.safe_load((ROOT / "examples/platform.yaml").read_text())
        missing = copy.deepcopy(base)
        missing["metadata"]["name"] = "cloudflare-worker-other"
        no_tunnel = copy.deepcopy(base)
        del no_tunnel["data"]["privateNetworking"]
        no_relay = copy.deepcopy(base)
        del no_relay["data"]["relay"]

        for profile, message in (
            (missing, "EnvironmentConfig cloudflare-worker-default was not found or has no platform settings"),
            (no_tunnel, "private networking requires at least one origin and the platform's shared Tunnel ID"),
            (no_relay, "private exposure requires the platform relay.image"),
        ):
            with self.subTest(message=message), self.assertRaises(subprocess.CalledProcessError) as error:
                render(xr, profile=profile)
            self.assertIn(message, error.exception.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
