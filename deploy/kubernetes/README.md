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
application identity have been reviewed and approved. The optional instance-role
path below adds only one scoped AssumeRole policy to the shared node role.

初始部署只启动控制台、登录和持久化，不携带 AWS 凭据。真实 AgentCore 工作区初始化与
应用身份单独审阅；跳过账号级追踪和 Skill Lab 的选项见容器指南。回退时先停止两个
Deployment、恢复原 Caddy 配置，保留两个 PVC；删除数据与 AWS 资源另行确认。

## Approved instance-role integration

Use this optional path only for the approved single-node trial. First render and
review the [AWS templates](../aws/README.md), then deploy the base and access
stacks through the operator's CloudFormation workflow. The workspace role keeps
the upstream permission set. Its trust and the node's new inline policy both
require the same ExternalId and name exactly one role on the other side.

Prepare a private copy of [aws-config.example](../aws/aws-config.example) through
the credential store. It uses `credential_source = Ec2InstanceMetadata` to obtain
temporary node credentials and assume the workspace role. Create a dedicated
Secret from that file; existing application credentials stay in their stores:

```bash
kubectl -n launchpad-trial create secret generic launchpad-aws-access \
  --from-file=config=/absolute/private/launchpad-aws-config
kubectl -n launchpad-trial patch networkpolicy launchpad-block-instance-metadata \
  --type=merge -p '{"spec":{"podSelector":{"matchLabels":{"app":"launchpad-frontend"}}}}'
kubectl -n launchpad-trial patch deployment launchpad-backend --type=strategic \
  --patch-file deploy/kubernetes/aws-instance-role.patch.yaml
```

The Deployment patch enables `hostNetwork` and `ClusterFirstWithHostNet`, sets
`AWS_PROFILE=launchpad-trial`, and mounts only the profile's `config` key at
`/var/run/launchpad-aws/config` read-only with mode `0440` and the existing
`fsGroup: 10001`. It enables SDK IMDS discovery. No access key is stored in source
or the Secret. The backend listens on host port **8100**, because the existing
CubePlex process uses 8000. The patch replaces the named `http` port rather than
adding a second port; the Service still exposes 8000 and resolves `targetPort:
http` to 8100, as do the health probes. Caddy's Service URL stays unchanged.

This is an explicitly accepted **shared node identity** boundary. The backend
can obtain the node role through IMDS; other node processes with the same access
and ExternalId can also assume the workspace role. Host-network pods also share
the node's network namespace. Kubernetes NetworkPolicy does not provide a
portable IMDS or ingress boundary for that backend, so scope the original IMDS
policy to the frontend as shown above and restrict host port 8100 using the
approved host/network controls. Keep Studio local execution disabled.

Verify the pod's host-network setting, its single port 8100, Service endpoints,
Secret mode/readability and STS account/assumed-role identity without printing
credential values. Continue bootstrap with
`--skip-cdk --skip-observability --skip-skill-lab`, then verify HTTPS login and the
approved limited AgentCore calls. These switches control bootstrap activity;
the upstream role's existing capabilities remain visible in the access template.
Application images stay unchanged for this deployment-only integration.

To reverse the integration, stop the backend, remove this optional configuration
explicitly (`hostNetwork: false`, `dnsPolicy: ClusterFirst`, remove the command
override/AWS profile variables/aws-access volume and mount, restore the one named
port 8000 and `AWS_EC2_METADATA_DISABLED=true`), and restore the original all-pod
IMDS policy. Start and verify the console with its existing PVCs before removing
the dedicated Secret and approved node AssumeRole policy. Reapplying `trial.yaml`
alone can retain fields added by a strategic patch.

此可选接入只用于已批准的单节点试用。先生成并审阅 AWS 模板，再部署基础与访问
栈。独立 Secret 只保存 AssumeRole profile（含 ExternalId），通过 IMDS 获取临时
节点凭据，源码和 Secret 中都没有 access key。后端改用 host network 和 8100，
Service 仍对集群提供 8000，健康探测沿用命名端口；前端继续受 IMDS 阻断策略约束。

这里明确接受共享节点身份边界：其他能取得节点凭据和 ExternalId 的进程也可
AssumeRole。Host-network 后端的 IMDS 与入口隔离由获批的宿主／网络控制负责，
保留 Studio 本地执行关闭。验收查看实际 STS 身份、端口、Service、Secret 读权限、
HTTPS 登录和批准范围内的 AgentCore 调用。回退须显式移除 patch 新增字段；只重放
初始清单可能保留这些字段。两个 PVC 保留，云资源撤销按相应授权执行。
