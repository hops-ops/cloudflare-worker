"""Deploy, update and delete an isolated Worker in the configured test account."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

NAMESPACE = "cloudflare-worker-e2e"
CONTEXT = "kind-cloudflare-worker-e2e"
XR_TYPE = "cloudflareworkers.workloads.hops.ops.com.ai"
ACCOUNT = os.environ["CLOUDFLARE_TEST_ACCOUNT_ID"]
TOKEN = os.environ["CLOUDFLARE_TEST_API_TOKEN"]
SUBDOMAIN = os.environ["CLOUDFLARE_TEST_WORKERS_SUBDOMAIN"]
RUN = os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
NAME = "hops-worker-e2e-" + RUN
MARKER = Path(os.environ["RUNNER_TEMP"]) / "cloudflare-worker-e2e-owned"
SCRIPTS = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/scripts"

if (not re.fullmatch(r"[0-9a-f]{32}", ACCOUNT)
    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", SUBDOMAIN)
    or not re.fullmatch(r"[0-9]+-[0-9]+", RUN) or len(NAME) > 63 or not TOKEN):
    raise SystemExit("A valid test account, workers.dev label, token, and Actions run identity are required.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def request(url, *, api=False, method="GET"):
    headers = {"Authorization": f"Bearer {TOKEN}"} if api else {}
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(urllib.request.Request(url, headers=headers, method=method), timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.code, response.read(32768)


def k(*args, **kwargs):
    return subprocess.run(["kubectl", "--context", CONTEXT, *args], check=True, timeout=370, **kwargs)


def apply(resource):
    k("apply", "-f", "-", input=json.dumps(resource), text=True)


def absent():
    status, body = request(f"{SCRIPTS}/{NAME}/settings", api=True)
    if status == 404:
        return True
    if status == 200:
        return False
    if status == 400:
        errors = json.loads(body).get("errors", [])
        if any(error.get("code") == 10007 for error in errors):
            return True
    raise RuntimeError(f"Cloudflare Worker lookup returned HTTP {status}.")


def wait_for(check, description, seconds=150):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except urllib.error.URLError:
            pass
        time.sleep(5)
    raise RuntimeError(f"Timed out waiting for {description}.")


def cleanup():
    if not MARKER.exists():
        return
    if MARKER.read_text() != f"{ACCOUNT}/{NAME}":
        raise RuntimeError("Ownership marker did not match this test run.")
    try:
        k("-n", NAMESPACE, "delete", XR_TYPE, NAME, "--ignore-not-found", "--timeout=180s")
        wait_for(absent, "Cloudflare to remove the Worker", seconds=45)
    except (RuntimeError, subprocess.SubprocessError, urllib.error.URLError):
        # Recover the exact resource reserved by this run, and report the failed normal lifecycle.
        if not absent():
            status, _ = request(f"{SCRIPTS}/{NAME}", api=True, method="DELETE")
            if status not in (200, 202, 204, 404):
                raise RuntimeError(f"Emergency deletion returned HTTP {status} for {NAME}.")
            wait_for(absent, "emergency cleanup to finish", seconds=40)
        MARKER.unlink()
        raise RuntimeError(f"Normal cleanup failed. Emergency cleanup confirmed {NAME} is absent.")
    MARKER.unlink()
    print(f"Verified Cloudflare deleted {NAME}.")


def secret(name, values, secret_type="Opaque"):
    resource = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "type": secret_type, "stringData": values,
    }
    k("create", "-f", "-", input=json.dumps(resource), text=True)



def diagnostics():
    resources = (
        (XR_TYPE, NAME),
        ("scripts.workers.upjet-cloudflare.m.upbound.io", NAME),
        ("scriptsubdomains.workers.upjet-cloudflare.m.upbound.io", NAME + "-endpoint"),
    )
    for kind, name in resources:
        output = k("-n", NAMESPACE, "get", kind, name, "--ignore-not-found", "-o", "json",
                   capture_output=True, text=True).stdout
        if not output:
            print(f"{kind}/{name} was never created.", flush=True)
            continue
        resource = json.loads(output)
        status = resource.get("status", {})
        for condition in status.get("conditions", []):
            summary = " ".join(str(condition.get(key, "")) for key in ("type", "status", "reason", "message"))
            for credential in (TOKEN, os.environ.get("GHCR_TOKEN")):
                if credential:
                    summary = summary.replace(credential, "[redacted]")
            print(f"{kind}/{name}: {summary[:1500]}", flush=True)
        enabled = status.get("atProvider", {}).get("enabled")
        if enabled is not None:
            print(f"{kind}/{name}: observed enabled={enabled}", flush=True)


def run():
    if not absent():
        raise RuntimeError("The unique test Worker name already exists.")
    MARKER.write_text(f"{ACCOUNT}/{NAME}")
    try:
        k("create", "namespace", NAMESPACE)
        secret("cloudflare-credentials", {"credentials": json.dumps({"api_token": TOKEN})})
        username = os.environ["GITHUB_ACTOR"]
        registry_token = os.environ["GHCR_TOKEN"]
        auth = base64.b64encode(f"{username}:{registry_token}".encode()).decode()
        secret("fixture-registry", {".dockerconfigjson": json.dumps({
            "auths": {"ghcr.io": {"auth": auth}}
        })}, "kubernetes.io/dockerconfigjson")
        apply({
            "apiVersion": "upjet-cloudflare.m.upbound.io/v1beta1",
            "kind": "ClusterProviderConfig", "metadata": {"name": "cloudflare-e2e"},
            "spec": {"credentials": {"source": "Secret", "secretRef": {
                "name": "cloudflare-credentials", "namespace": NAMESPACE, "key": "credentials",
            }}},
        })
        apply({
            "apiVersion": "apiextensions.crossplane.io/v1beta1", "kind": "EnvironmentConfig",
            "metadata": {"name": "cloudflare-worker-default"},
            "data": {
                "accountId": ACCOUNT, "workersDevSubdomain": SUBDOMAIN,
                "providerConfigRef": {"name": "cloudflare-e2e", "kind": "ClusterProviderConfig"},
            },
        })
        xr = {
            "apiVersion": "workloads.hops.ops.com.ai/v1alpha1", "kind": "CloudflareWorker",
            "metadata": {"name": NAME, "namespace": NAMESPACE},
            "spec": {"workerName": NAME, "exposure": "public", "compatibilityDate": "2025-02-14",
                     "artifact": {"pullSecretRef": {"name": "fixture-registry"}}},
        }
        url = f"https://{NAME}.{SUBDOMAIN}.workers.dev/"
        for version in ("v1", "v2"):
            ref = os.environ[f"BUNDLE_{version.upper()}"]
            digest = ref.rsplit("@", 1)[1]
            xr["spec"]["artifact"]["ref"] = ref
            apply(xr)
            k("-n", NAMESPACE, "wait", XR_TYPE, NAME, "--timeout=300s",
              f"--for=jsonpath={{.status.resolvedArtifactDigest}}={digest}")
            k("-n", NAMESPACE, "wait", XR_TYPE, NAME, "--timeout=300s",
              f"--for=jsonpath={{.status.url}}={url.rstrip('/')}")
            wait_for(lambda: request(url) == (200, f"{NAME}:{version}".encode()),
                     f"the real Worker to serve {version}")
            print(f"Verified Cloudflare served {NAME}:{version}.", flush=True)
    except Exception:
        try:
            diagnostics()
        except (subprocess.SubprocessError, ValueError):
            print("Could not read Kubernetes reconciliation status.", flush=True)
        raise
    finally:
        cleanup()


if __name__ == "__main__":
    if sys.argv[1:] == ["--cleanup"]:
        cleanup()
    else:
        run()
