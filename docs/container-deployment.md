# Container deployment (trial)

[中文版](container-deployment.zh-CN.md)

This packages the existing FastAPI control plane and built React console for a
single-instance `linux/amd64` trial. It remains sample code for non-production
usage. Agent runtimes and build workers still run in AWS; these images are the
console, not an AgentCore runtime image. Read the [prod runbook](agent-runbook-prod.md)
before replacing a running backend.

The [k3s trial manifest](../deploy/kubernetes/trial.yaml),
[settings example](../deploy/kubernetes/settings.example.yaml) and
[Caddy example](../deploy/kubernetes/Caddyfile.example) describe the existing-host
deployment. Replace their image and origin placeholders after reviewing the
target cluster. The frontend's writable `/tmp` needs `fsGroup: 101`; no writable
nginx configuration directory is required.

## Build and provenance

Commit the reviewed source first, then run from the repository root:

```bash
./deploy/containers/build-images.sh HEAD /absolute/path/build-evidence launchpad-trial
```

The script builds only `git archive <commit>` and loads two images locally:
`launchpad-trial-backend:<12-char-sha>` and
`launchpad-trial-frontend:<12-char-sha>`. Uncommitted and ignored files are outside
the build. The output directory contains the source tar, its SHA-256, the full
commit, build logs/metadata and image inspection results. OCI labels record the
commit and source-archive hash. The script does not push images. After publishing,
record the registry digests and deploy `repository@sha256:...` references.

For a host without registry access, set `EXPORT_IMAGES=1` when running the script.
It also emits `backend.image.tar.gz` and `frontend.image.tar.gz` plus SHA-256
files. Transfer the archives through an approved artifact channel, verify their
hashes, then import with `docker load` or `sudo k3s ctr images import <archive>`.
Record the imported image IDs/digests; loading an archive needs no registry token.

The Dockerfiles pin Python, uv, Node and nginx base-image digests. Python resolves
from `backend/uv.lock` with `uv sync --frozen --no-dev`; npm uses `npm ci` and the
committed lockfile. XML bindings compile against the same system libxml2 and are
imported together during the build. Debian package repositories and Python build
dependencies can still change between rebuilds; this is source provenance and
immutable deployed artifacts, not a byte-for-byte reproducible build claim.

Backend code remains at `/app/backend`, with `/app/vendor`, `/app/samples` and
`/app/scripts` preserving source-relative paths. Its Python environment is
`/opt/venv`. The frontend build includes `scripts/validate_video_catalog.mjs` and
`backend/app/data`; nginx serves only its compiled assets.

## Private settings and a local Compose trial

The backend runs as UID/GID `10001:10001`; nginx runs as `101:101`. Persist both
`/app/data` (SQLite, uploads, artifacts and runtime settings) and `/app/config`
(private `launchpad.yaml`). The image creates these directories with backend
ownership. Kubernetes PVCs or pre-existing bind mounts require matching ownership
or `fsGroup: 10001`; test writes before starting the application. Keep the backend
at one replica, with `Recreate` for Kubernetes upgrades: the ledger, background
jobs and login throttles assume one process.

Set the image references and the actual HTTPS origin in your shell:

```bash
export BACKEND_IMAGE=launchpad-trial-backend:<12-char-sha>
export FRONTEND_IMAGE=launchpad-trial-frontend:<12-char-sha>
export PUBLIC_BASE_URL=https://launchpad.example.test
```

Prepare a private YAML file outside the checkout using
`deploy/containers/launchpad.example.yaml`. Add a strong `auth_password` through
your credential-store workflow. Keep the plaintext out of Git, build arguments,
shell command arguments and build logs. The following transfers that private file
through stdin into the dedicated Compose config volume:

```bash
docker compose -f deploy/containers/compose.yaml run --rm --no-deps -T backend \
  uv run --no-sync python -c \
  'import pathlib,sys; p=pathlib.Path("/app/config/launchpad.yaml"); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o600)' \
  < /absolute/private/launchpad.yaml
docker compose -f deploy/containers/compose.yaml up -d
```

The console binds only `127.0.0.1:18080`; terminate HTTPS with an existing trusted
edge proxy. `prod` always sets Secure session cookies, so browser login requires
HTTPS. A plain HTTP health probe is still useful. The compose backend has no
published port. Compose uses `172.30.247.0/24`; if that subnet conflicts, change
both its IPAM subnet and `LAUNCHPAD_AUTH_TRUSTED_PROXIES` together.

The image refuses an unauthenticated prod startup. Registration and local Studio
execution are off; the backend has no Docker socket or elevated capabilities.
`AWS_EC2_METADATA_DISABLED=true` prevents the SDK from silently using a host node
role; it is not a network firewall. The initial deployment also blocks pod access
to instance metadata. Compose supplies no AWS identity. Real AWS calls require
the approved identity and resource configuration from the bootstrap plan.

The optional [instance-role integration](../deploy/kubernetes/README.md#approved-instance-role-integration)
uses the existing node role to assume one dedicated workspace role. Its backend
uses host networking on port 8100 and a private AWS profile Secret, while the
Service remains on 8000. That mode explicitly accepts shared node identity;
other processes with node credentials and the ExternalId can assume the same
role. Scope the initial IMDS-block policy to the frontend in that mode and apply
the approved host/network controls to the backend. This deployment-only change
does not require rebuilding the application images.

After approval of the exact cloud resources and identities, an operator can run
the initial bootstrap from a full source checkout on the prepared operator host:

```bash
cd backend
uv run python ../scripts/bootstrap.py --skip-cdk --skip-observability --skip-skill-lab
```

First deploy the reviewed [rendered base/access templates](../deploy/aws/README.md).
They inline the three supported Lambda assets, remove the base Marketplace
subscription statement and need no new CDKToolkit. `--skip-cdk` retains that
deployment path; the other flags preserve account-wide observability settings
and defer the Skill Lab worker. Bootstrap still creates or updates shared
AgentCore resources, so use the exact approved plan and already-enabled model
access. The renderer records deterministic SHA-256 manifests and rejects unknown
asset layouts. Transfer only approved configuration and identity to the container.

## Proxy contract

The nginx image listens on 8080. `/api`, `/v1` and `/share` (including their bare
paths) preserve path/query/body and proxy to the backend; other routes use SPA
fallback. Missing `/assets/*` files return 404. Request and response buffering are
disabled and read/send timeouts are 1100 seconds for SSE and long invocations.
`/healthz` checks nginx; `/api/health` checks the backend.

| Setting | Meaning |
| --- | --- |
| `BACKEND_UPSTREAM` | `host:port`, default `backend:8000`. For k3s use a complete Service DNS name such as `backend.launchpad-trial.svc.cluster.local:8000`. |
| `DNS_RESOLVER` | Optional DNS server address; defaults to the first nameserver in `/etc/resolv.conf`. nginx refreshes upstream DNS every 10 seconds, including after backend replacement. |
| `TRUSTED_PROXY_CIDR` | Exact edge-proxy address/CIDR permitted to supply `X-Forwarded-For`; default loopback only. Set this to Caddy's observed source, not all callers. |

nginx resolves the client through that trusted edge and overwrites the upstream
`X-Forwarded-For` with one client address. It clears `CloudFront-Viewer-Address`.
The backend uses `--no-proxy-headers`; configure
`LAUNCHPAD_AUTH_TRUSTED_PROXIES` to the actual nginx source CIDR and
`LAUNCHPAD_AUTH_TRUSTED_PROXY_HOPS=1` for login throttling. Restrict backend ingress
to nginx. Verify a forged forwarded-IP header does not change the throttle key.
Use `LAUNCHPAD_PUBLIC_BASE_URL` for the external HTTPS origin; it controls OAuth
return URLs. Retest SSO callbacks through the real edge before enabling SSO.

An existing edge can also route `/api`, `/v1` and `/share` directly to the backend,
with other paths going to nginx. In that shape, trust only the edge's observed
source in `LAUNCHPAD_AUTH_TRUSTED_PROXIES` and keep the hop count at one. The edge
must preserve streaming, sanitize forwarded client addresses and restrict backend
access. Verify this actual route independently of the bundled nginx proxy.

## Initial feature boundary

| Surface | Initial image support |
| --- | --- |
| Console login, local account administration, SQLite ledger, static UI | Packaged; validate after deployment. |
| Managed Harness, AgentCore invoke, Memory, Registry, evaluation, AWS CodeBuild-backed packaging | Control-plane code is packaged. Each workflow still requires its approved AWS resources, role, model access and live acceptance. |
| ZIP packaging / Python dependency resolution | uv, pip, git and source templates are packaged. No local Docker daemon is required. |
| Studio local debug / AI Fix | Explicitly disabled by `LAUNCHPAD_STUDIO_LOCAL_EXEC_ENABLED=false`. No execution venv or Docker socket. |
| Claude SDK code generation | Optional Claude CLI/Strands MCP tooling is not installed; its availability check reports the missing CLI. The Python SDK alone does not enable this workflow. |
| Harness export / conversion | Optional Node `@aws/agentcore` CLI under `data/agentcore-cli` is not installed. Use managed Harness directly; conversion needs a separately packaged and validated extension. |
| Skill Lab task-set validation, task generation, evaluation/training and agentic judge | Vendored sources are packaged, but the dedicated interpreter, sandbox and judge CLIs are not installed. `/api/skill-lab/status` reports those prerequisites; no Skill Lab worker is provisioned by these images. |
| Online self-update | Disabled with `LAUNCHPAD_UPDATE_ENABLED=false`; replace images through the deployment workflow. The archive has no Git metadata, so the console's Git-derived version SHA is empty; use OCI labels and deployment image digests. |

Extending a row requires packaging its actual runtime dependencies and testing
the workflow. Running a host bootstrap inside this container can create mutable
environments and cloud resources; review those effects as a separate change.

## Verification, replacement and recovery

Run `make verify` for source changes. For the built images, verify a non-root
process and read-only code, a failed startup without auth, denied anonymous API
access, successful login, Secure/HttpOnly/SameSite cookie flags, static/deep links,
all three proxy prefixes, and streamed responses without buffering. Create a
throwaway local user, recreate the backend against the same volumes and verify
that the user and its session still work. Inspect Skill Lab readiness and the
Studio refusal to confirm the feature boundary. Synthetic local checks do not
prove AWS provisioning, real model invocation or external HTTPS/SSO acceptance.

Before an upgrade, use `backend/scripts/ledger_backup.py` and
`backend/scripts/inflight.py` as documented in the prod runbook. Preserve both
volumes and record the previous image digests. Stop/recreate the backend rather
than running old and new ledger writers concurrently. If a migration has changed
the ledger, restore the verified pre-upgrade backup with the backend stopped
before reverting the image. `docker compose down` keeps the volumes;
`docker compose down -v` deletes the trial's stored state.
