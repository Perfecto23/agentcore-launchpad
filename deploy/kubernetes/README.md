# Single-node trial / 单节点试用

This overlay targets an existing k3s node with `local-path` storage and a Caddy
HTTPS entrypoint. It installs one Backend and one static Frontend in
`launchpad-trial`; SQLite data and generated configuration use separate PVCs.
The Backend uses `Recreate` so two writers do not share the ledger during rollout.

本目录用于已有 k3s 节点：独立命名空间、单实例 Backend/Frontend、两个持久卷和现有 Caddy
HTTPS 入口。先按 [English container guide](../../docs/container-deployment.md) 或
[中文容器指南](../../docs/container-deployment.zh-CN.md) 构建并验证镜像。

1. Build from a committed revision and set `EXPORT_IMAGES=1`. Publish the image
   archives, their SHA-256 files and source revision together in the fork release.
2. On the target node, verify the archive checksums, then import each archive with
   `k3s ctr images import <archive>`. Replace both image names in `trial.yaml`
   with the exact tags from the release's `*.image.txt`. `imagePullPolicy: Never`
   keeps this trial independent of registry credentials; a missing image fails
   explicitly instead of selecting a mutable remote tag.
3. Create the namespace, then apply a private copy of `settings.example.yaml`.
   Set the account, public origin and the actual transport address of Caddy.
   Create Secret `launchpad-login` with `LAUNCHPAD_AUTH_PASSWORD` from a protected
   file through the Kubernetes API. Keep this file and its value outside Git,
   shell history, logs and image layers.
4. Apply `trial.yaml`; verify both Deployments and both PVCs. The manifest uses
   non-root containers, read-only root filesystems and no service-account token.
5. Add the separate hostname from `Caddyfile.example` to the existing Caddy
   configuration, validate it and reload. Preserve the other virtual hosts.
   Caddy routes API traffic directly to Backend and overwrites client forwarding
   headers. Configure the Backend's trusted proxy to that observed Caddy address
   and one hop; its container disables Uvicorn's automatic proxy rewriting.
6. Verify HTTPS, unauthenticated API denial, login, client-IP throttling and data
   after Backend recreation. Save the previous Caddy configuration, image
   identity and an online SQLite backup before subsequent upgrades.

Initial pods have no AWS credentials, disable IMDS discovery, and deny egress to
`169.254.169.254`. This installs the console, not the AWS workspace substrate.
Enable real AgentCore operations only after the exact bootstrap resources and
application identity have been reviewed and approved. Do not copy another
application's credentials or add broad policies to the shared node role.

初始部署只启动控制台、登录和持久化，不携带 AWS 凭据。真实 AgentCore 工作区初始化与
应用身份单独审阅；跳过账号级追踪和 Skill Lab 的选项见容器指南。回退时先停止两个
Deployment、恢复原 Caddy 配置，保留两个 PVC；删除数据与 AWS 资源另行确认。
