# 容器部署（试用）

[English](container-deployment.md)

这套配置把现有 FastAPI 控制面和编译后的 React 控制台打包为单实例
`linux/amd64` 试用环境。项目仍是非生产用途的示例代码。Agent Runtime 和构建
Worker 继续运行在 AWS，这两个镜像运行控制台。替换正在运行的后端前，先读
[prod 运行手册](agent-runbook-prod.md)。

已有宿主部署参考 [k3s 试用清单](../deploy/kubernetes/trial.yaml)、
[设置示例](../deploy/kubernetes/settings.example.yaml) 和
[Caddy 示例](../deploy/kubernetes/Caddyfile.example)。核对目标集群后替换镜像和
origin 占位。前端的可写 `/tmp` 须设置 `fsGroup: 101`，nginx 配置目录保持只读。

## 构建与来源

提交已审阅源码后，在仓库根目录运行：

```bash
./deploy/containers/build-images.sh HEAD /absolute/path/build-evidence launchpad-trial
```

脚本只从 `git archive <commit>` 构建并加载两个本地镜像：
`launchpad-trial-backend:<12-char-sha>` 和
`launchpad-trial-frontend:<12-char-sha>`。未提交文件和忽略文件不进入构建。
输出目录包含源码 tar、SHA-256、完整 commit、构建日志与元数据、镜像检查结果。
OCI 标签记录 commit 和源码归档哈希。脚本只构建；发布后记录 registry digest，
部署时使用 `repository@sha256:...`。

目标宿主无需 registry 时，运行脚本时设置 `EXPORT_IMAGES=1`。脚本额外生成
`backend.image.tar.gz`、`frontend.image.tar.gz` 和 SHA-256 文件。通过获批产物
通道传输并校验哈希后，使用 `docker load` 或
`sudo k3s ctr images import <archive>` 导入，再记录导入后的 image ID／digest。
此路径无需复制 registry token。

Dockerfile 固定 Python、uv、Node 和 nginx 的基础镜像 digest。后端使用
`uv sync --frozen --no-dev` 安装 `backend/uv.lock`；前端使用 `npm ci`。
两个 XML 绑定使用同一套系统 libxml2 编译，并在构建期间共同导入验证。
Debian 软件源及 Python 构建依赖仍可能随时间变化，因此这里提供源码溯源和
不可变部署产物，重新构建的镜像字节可能变化。

后端保留 `/app/backend`、`/app/vendor`、`/app/samples` 和 `/app/scripts`
的相对布局，Python 环境位于 `/opt/venv`。前端构建同时复制视频校验脚本和
`backend/app/data`，nginx 只提供编译产物。

## 私有配置与本地 Compose

后端使用 UID/GID `10001:10001`，nginx 使用 `101:101`。持久化 `/app/data`
（SQLite、上传文件、产物和运行设置）与 `/app/config`（私有 `launchpad.yaml`）。
镜像中的目录已归后端用户所有。Kubernetes PVC 或已有 bind mount 须设置相应
所有权或 `fsGroup: 10001`，启动前验证写权限。后端保持单副本，Kubernetes 更新
使用 `Recreate`；SQLite、后台任务和登录限流按单进程运行。

在 shell 中设置镜像与实际 HTTPS 地址：

```bash
export BACKEND_IMAGE=launchpad-trial-backend:<12-char-sha>
export FRONTEND_IMAGE=launchpad-trial-frontend:<12-char-sha>
export PUBLIC_BASE_URL=https://launchpad.example.test
```

以 `deploy/containers/launchpad.example.yaml` 为基础，在仓库外准备私有 YAML，
通过凭据存储流程添加强 `auth_password`。明文只保留在私有配置中。以下命令通过
标准输入把配置写入独立 Compose volume：

```bash
docker compose -f deploy/containers/compose.yaml run --rm --no-deps -T backend \
  uv run --no-sync python -c \
  'import pathlib,sys; p=pathlib.Path("/app/config/launchpad.yaml"); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o600)' \
  < /absolute/private/launchpad.yaml
docker compose -f deploy/containers/compose.yaml up -d
```

控制台只监听 `127.0.0.1:18080`，HTTPS 由已有可信边缘代理终止。`prod` 的登录
Cookie 始终带 Secure，浏览器登录须使用 HTTPS；普通 HTTP 可用于健康探测。
Compose 不发布后端端口。默认网络为 `172.30.247.0/24`；若冲突，同时修改
IPAM subnet 与 `LAUNCHPAD_AUTH_TRUSTED_PROXIES`。

后端在未配置认证时拒绝启动 prod。默认关闭注册和 Studio 本地执行，容器没有
Docker socket 或额外 Linux capabilities。`AWS_EC2_METADATA_DISABLED=true`
阻止 SDK 自动使用宿主节点角色；初始部署在网络层同时拦截 pod 的实例元数据访问。
Compose 不提供 AWS 身份；真实 AWS 调用使用获批的身份、资源配置和 bootstrap
方案。

可选的[实例角色接入](../deploy/kubernetes/README.md#approved-instance-role-integration)
使用既有节点角色 AssumeRole 到一个独立工作区角色。该模式下后端使用 host
network 和 8100 端口，挂载私有 AWS profile Secret，Service 仍提供 8000。
这里明确接受共享节点身份：其他取得节点凭据及 ExternalId 的进程也可 AssumeRole。
启用时将初始 IMDS 阻断策略缩小到前端，后端使用获批宿主／网络控制。此变更只涉及
部署配置，沿用已有应用镜像。

精确云资源与身份方案获批后，可在具备 bootstrap 前置依赖（含 Node／CDK）的
操作宿主上，从完整源码 checkout 运行初始 bootstrap：

```bash
cd backend
uv run python ../scripts/bootstrap.py --skip-cdk --skip-observability --skip-skill-lab
```

先部署已审阅的[基础与访问模板](../deploy/aws/README.md)。生成脚本内联三个受支持
的 Lambda 资产，移除基础角色的 Marketplace 订阅语句，无需新增 CDKToolkit。
`--skip-cdk` 保留这一部署路径，其他选项保留账号级 observability 设置并延后
Skill Lab Worker。Bootstrap 仍会创建或更新共享 AgentCore 资源，执行时沿用精确
获批方案及已开通的模型。生成脚本记录确定性的 SHA-256 manifest，并拒绝未知
资产布局。完成后只向容器转移获批的生成配置和应用身份。

## 代理合同

nginx 监听 8080。`/api`、`/v1`、`/share` 及其裸路径保留路径、查询和请求体，
转发至后端；其他路径使用 SPA fallback。不存在的 `/assets/*` 返回 404。
请求和响应均关闭 buffering，读写超时为 1100 秒，支持 SSE 和长调用。
`/healthz` 检查 nginx，`/api/health` 检查后端。

| 设置 | 用途 |
| --- | --- |
| `BACKEND_UPSTREAM` | `host:port`，默认 `backend:8000`。k3s 使用完整 Service DNS，例如 `backend.launchpad-trial.svc.cluster.local:8000`。 |
| `DNS_RESOLVER` | 可选 DNS 地址，默认读取 `/etc/resolv.conf` 的首个 nameserver。nginx 每 10 秒刷新上游地址，支持替换后端。 |
| `TRUSTED_PROXY_CIDR` | 允许提供 `X-Forwarded-For` 的精确边缘代理地址或 CIDR，默认只信任 loopback。部署时改为实测 Caddy 来源。 |

nginx 只通过上述可信边缘代理解析客户端，并把上游 `X-Forwarded-For` 替换为
单个客户端地址，同时清除 `CloudFront-Viewer-Address`。后端使用
`--no-proxy-headers`；把 `LAUNCHPAD_AUTH_TRUSTED_PROXIES` 设为 nginx 实际来源
CIDR，`LAUNCHPAD_AUTH_TRUSTED_PROXY_HOPS` 设为 `1`，供登录限流使用。
后端入口只向 nginx 开放。实测伪造转发 IP header 后，限流仍应使用真实客户端。
`LAUNCHPAD_PUBLIC_BASE_URL` 填外部 HTTPS origin，用于 OAuth 回调地址。
启用 SSO 前，通过真实边缘代理验收回调。

已有边缘代理也可以把 `/api`、`/v1`、`/share` 直接路由至后端，其他路径发往
nginx。此时 `LAUNCHPAD_AUTH_TRUSTED_PROXIES` 只信任实测边缘代理来源，hop
数量保持为 `1`。边缘代理负责流式传输、清洗客户端地址和限制后端访问。
这一实际路径须独立于镜像内 nginx 代理验收。

## 初始功能范围

| 功能 | 初始镜像状态 |
| --- | --- |
| 控制台登录、本地账号管理、SQLite、静态 UI | 已打包，部署后须验证。 |
| Managed Harness、AgentCore 调用、Memory、Registry、评估、AWS CodeBuild 构建 | 控制面代码已打包；各流程仍须获批的 AWS 资源、角色、模型权限和在线验收。 |
| ZIP 打包与 Python 依赖解析 | 包含 uv、pip、git 和模板，无需本机 Docker daemon。 |
| Studio 本地调试与 AI Fix | 由 `LAUNCHPAD_STUDIO_LOCAL_EXEC_ENABLED=false` 显式关闭；未安装执行 venv，未挂载 Docker socket。 |
| Claude SDK 代码生成 | 未安装可选 Claude CLI 和 Strands MCP 工具；现有可用性检查会报告 CLI 缺失。Python SDK 本身只构成部分依赖。 |
| Harness 导出与转换 | 未安装 `data/agentcore-cli` 下的 Node `@aws/agentcore` CLI。Managed Harness 可直接使用，转换须另行打包并验证。 |
| Skill Lab 任务集校验、任务生成、评估、训练和 agentic judge | 保留 vendor 源码；未安装独立解释器、sandbox 和 judge CLI。`/api/skill-lab/status` 返回依赖状态；镜像不会创建 Skill Lab Worker。 |
| 在线自更新 | `LAUNCHPAD_UPDATE_ENABLED=false`，通过部署流程替换镜像。源码归档没有 Git 元数据，控制台 Git 版本 SHA 为空；使用 OCI 标签与部署 digest 溯源。 |

扩展上述功能时，须打包完整运行依赖并实测。直接在容器中运行宿主 bootstrap
可能创建可变环境和 AWS 资源，这些影响作为独立变更审阅。

## 验证、替换与恢复

源码修改运行 `make verify`。镜像验证覆盖非 root 身份、只读代码、未配置认证时
启动失败、匿名 API 拒绝、登录成功、Cookie 的 Secure／HttpOnly／SameSite 属性、
静态资源、深链接、三个代理前缀和无缓冲流式响应。创建临时本地用户，复用原
volume 重建后端，确认用户和 session 保留。读取 Skill Lab 状态并检查 Studio
拒绝结果，确认功能边界。合成的本地检查只验证容器；真实 AWS 创建、模型调用、
外部 HTTPS 和 SSO 各自需要在线验收。

升级前按 prod 手册运行 `backend/scripts/ledger_backup.py` 和
`backend/scripts/inflight.py`。保留两个 volume，并记录原镜像 digest。
先停止旧后端再创建新后端，保持单个 ledger writer。若升级发生 schema 迁移，
回退镜像前先停止后端并恢复已验证的升级前备份。`docker compose down` 保留
volume；`docker compose down -v` 会删除试用数据。
