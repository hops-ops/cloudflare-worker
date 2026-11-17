# CloudflareWorker

Deploy an immutable OCI Worker bundle with the Hops `CloudflareWorker` API.
A cluster operator supplies the Cloudflare account and provider configuration
once. Each workload chooses its endpoint and can independently opt into private
HTTP origins.

## Getting started

Install Crossplane 2 and the namespaced Cloudflare provider packages with
OCI-backed Workers `Script` support. Build a Worker OCI bundle using the
provider's bundle format, publish it to a registry, and pin its manifest digest.
For a private registry, set `artifact.pullSecretRef.name` to a
`kubernetes.io/dockerconfigjson` Secret in the Worker's namespace.

Install the Hops configuration and its render function. Create the
operator-owned [platform profile](examples/platform.yaml) with your Cloudflare
account ID and an existing `ProviderConfig` or `ClusterProviderConfig`. The
profile name is `cloudflare-worker-default` unless the workload selects a
different `spec.platformRef.name`.

```yaml
apiVersion: workloads.hops.ops.com.ai/v1alpha1
kind: CloudflareWorker
metadata:
  name: api
  namespace: example
spec:
  workerName: example-api
  artifact:
    ref: ghcr.io/example/worker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  compatibilityDate: "2025-02-14"
```

This uploads the Worker and keeps its `workers.dev` endpoint disabled. Pick an
account-wide unique `workerName`. To publish a new build, change only the
digest. Crossplane reports the fetched digest at
`status.resolvedArtifactDigest` and reports deployment health through the
normal XR conditions.

## Growing

| `spec.exposure` | Request path |
| --- | --- |
| `none` (default) | Worker with its `workers.dev` endpoint disabled |
| `public` | Direct HTTPS at `<worker>.<account>.workers.dev` |
| `private` | Private WebService URL → service-token-protected Worker |

Public and private modes need `workersDevSubdomain` in the profile. A private
Worker also requires an installed WebService configuration and a profile
`relay.image` pointing to a Caddy image. The provider must support the
`accessApplicationRef` guard and publish service token connection details.
Crossplane Usage protection must be enabled so Access stays in place through
Worker deletion. The relay reads credentials from the token's namespace-local
connection Secret. `status.url` reports the observed WebService URL. Use an
immutable relay image pin and plan renewal before the Access token expires
(`relay.serviceTokenDuration` defaults to one year).

Use private HTTP origins with any exposure mode. Set
`privateNetworking.enabled: true` and list the Worker binding name, hostname,
and port for each origin. The profile's `privateNetworking.tunnelId` points at
an existing shared Cloudflare Tunnel that can resolve and reach the requested
origins. Each origin becomes a namespaced VPC service and a Worker binding.
Disabling private networking removes those managed bindings and services.
See the [private example](examples/cloudflareworkers/private.yaml).

## Enterprise scale

Use separate platform profiles for separate accounts or network trust
boundaries. Access to a profile should follow the environment's namespace
and admission policies. Use Worker-specific bundle credentials and scope the
shared Tunnel to the private networks its workloads need. The XR uses the
tunnel as a dependency and leaves its lifecycle with the platform team.

## Import existing

Start with a new Worker name for this alpha. Plan ownership transfer and verify
traffic before retiring an existing Worker. Changing the Worker identity,
platform profile, or exposure requires a separate XR. Private origin bindings
can change in place.

## Development

Run `make render` for the example renders and `make test` for lifecycle
contracts. The latter needs Python with PyYAML. Set
`CLOUDFLARE_WORKER_SCHEMAS` to a directory of the installed provider,
WebService, and Usage CRDs to validate the rendered resources against them.

## Release and live CI

Pull requests build and test the package. Same-repository pull requests labeled
`cloudflare-e2e` also run the Kind lifecycle test. Main runs the lifecycle test
before vnext creates a release tag. The tagged workflow publishes the package
and GitHub release.

Configure the repository's `cloudflare-e2e` GitHub Actions environment:

| Setting | Kind | Value |
| --- | --- | --- |
| `CLOUDFLARE_TEST_API_TOKEN` | Secret | API token restricted to a dedicated test Cloudflare account with a Workers write permission |
| `CLOUDFLARE_TEST_ACCOUNT_ID` | Variable | That test account's ID |
| `CLOUDFLARE_TEST_WORKERS_SUBDOMAIN` | Variable | The account label in `<label>.workers.dev`, enabled in Cloudflare |
| `CLOUDFLARE_WORKERS_PROVIDER_PACKAGE` | Variable | Pullable Crossplane provider package that serves namespaced `Script.spec.artifact` |
| `CLOUDFLARE_FAMILY_PROVIDER_PACKAGE` | Variable, optional | Explicit compatible family package if the Workers package needs a separately installed family |

Grant the token permission to create, update, read, and delete Worker scripts
and manage their `workers.dev` endpoint. The workflow uses the repository's
automatic Actions token to publish and pull its synthetic OCI bundles.

The test boots a fresh Kind cluster, installs Crossplane and the compatible
provider, and reconciles the source composition with its go-templating renderer.
It publishes two versions of a synthetic response Worker under a run-specific
name, checks the live Cloudflare response before and after updating the digest,
and confirms Cloudflare deletion. The workflow tests the public Worker
lifecycle. The render tests cover the private relay and private origin bindings.
