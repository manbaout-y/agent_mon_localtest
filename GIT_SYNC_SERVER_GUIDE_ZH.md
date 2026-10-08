# 使用自己的 Git 仓库同步 DSV41 项目到服务器

日期：2026-10-08。目的：解决无法直接向服务器传文件的问题，建立“本地开发 → 远程仓库 → 服务器拉取 → 实验”的固定流程。

## 1. 推荐选择

建立 Git 同步前，本地 `E:\agentproject\agent_mon-ver_hetero_state` 是源码快照，没有 `.git`。本次首次发布准备已加入根目录 `.gitignore`；Git 初始化/推送是否完成以实际状态为准。

用户指定远程仓库为 `https://github.com/manbaout-y/agent_mon_localtest`，无需再次 Fork。下文远程命令已使用这一仓库地址。

**推荐建一个自己的私有代码仓库，保存当前完整代码及本次修改。** 可使用服务器能够访问的 GitHub 或企业 Git 平台。服务器能访问内网 Git 平台但不能访问 GitHub 时，优先使用内网平台。

Fork 适合有明确原仓库链接、希望保留上游关联的情况。Fork 只创建远程副本，并不会自动包含当前本地新增的脚本；这些修改仍需要提交并推送到 Fork。若采用 Fork，先克隆自己的 Fork，再将本地变更合入，注意不要漏掉合并后的 agentmon/kv_ana 和新增文档。

不要仅 Fork 历史 AgentMon 仓库就认为当前统一工作区已同步。当前工作区包含两个独立 Python 项目和联合工具，建议放在一个代码仓库中保存。

## 2. 本地首次发布：可以使用 GitHub Desktop

本地发布是 Windows 上的操作，不在服务器终端执行。可以用 GitHub Desktop 或已有 Git 客户端完成，不需要先把文件传到服务器。

1. 先整理忽略规则及待提交文件。
2. 在现有项目目录建立本地 Git 仓库，不创建另一个空项目来替代当前代码。
3. 检查 Changes 中的文件列表，提交当前源码和需要的测试/文档。
4. Publish repository 到自己的账号，保持私有可见性。
5. 记下仓库 Clone URL 和实际分支名。

远程仓库已由用户创建；本地初始化、提交和推送由本次发布任务按实际认证状态执行。私有/公开可见性以 GitHub 设置为准，不能从仓库URL推断。

### 忽略规则的建议起点

在项目根目录建立 `.gitignore`，可先使用以下内容，再按实际目录补充。Git 客户端初始化时若已有模板，合并这些规则即可。

```gitignore
# 实验输出
/outputs/
**/outputs/

# Python 环境与缓存
.venv/
venv/
envs/
__pycache__/
*.py[cod]
.pytest_cache/

# 本地模型和原始状态资产
/models/
**/raw_tensors/
*.safetensors

# 本地凭据配置
.env
.env.*
!.env.example
```

不要忽略所有 JSON/JSONL：测试 fixture 和配置可能依赖它们。不要直接忽略整个 kv_ana 或 runtime_references。需要保留的 paper specs、配置、测试和源码参考仍应提交。

源码文档目录中的 `docs/dsv41_metrics_review_20261008/evidence/user_metrics.prom` 是新测试输入，应随测试一起保留。outputs 中的历史验收日志继续留在本地/服务器，不作为源码上传。

本例忽略规则只是起点，不会识别任意位置的大文件。首次提交前在 Changes 列表确认没有虚拟环境、模型权重、业务数据、原始 Tensor、令牌或旧发布包。

## 3. 服务器先验证能访问仓库

以下全部是服务器 Bash 命令。把 URL 中的用户名替换为实际账号；企业 Git 平台则使用它提供的真实 Clone URL：

```bash
git --version
REPO_URL=https://github.com/manbaout-y/agent_mon_localtest.git
git ls-remote "$REPO_URL" HEAD
```

首次提交已推送后，期望输出一个 commit 和 HEAD。若仓库仍为空，不会有 HEAD；先完成本地提交/推送。

私有仓库需要服务器认证。使用已有 Git 凭据配置或平台支持的 SSH key；不要把 token 写进仓库URL、脚本或对话。若使用 SSH，将 REPO_URL 换成平台提供的 SSH Clone URL，并按现有认证方式执行。

若网络/代理/认证失败，先按服务器已有企业代理和账号配置处理，不重新下载模型，也不把 Git 错误当成 DSV41 服务故障。服务器需要能访问选定 Git 平台，否则 Git 同步同样无法成立。

## 4. 首次克隆：用新的代码目录

保留现有 `~/agent_mon-ver_hetero_state` 和其中的实验输出，先克隆到新目录：

```bash
cd ~
git clone "$REPO_URL" agentmon-dsv41-dev
cd ~/agentmon-dsv41-dev
git status --short
git branch --show-current
git log -1 --oneline
```

若 agentmon-dsv41-dev 已存在，先核对它是否是同一仓库，不删除它、不覆盖目录。

新目录只存代码。原模型路径、Python环境、Docker socket和运行容器不需要搬迁，既有成功 smoke 仍在原来的绝对路径。

## 5. 在新目录运行现有只读采集

```bash
cd ~/agentmon-dsv41-dev
export PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
export V41_SOCKET=unix:///run/docker-v41.sock
export CONTAINER_PY=/usr/local/python3.12.13/bin/python3
export ASCEND_ROOT=/vllm-workspace/vllm-ascend/vllm_ascend

docker -H "$V41_SOCKET" ps -a \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

按实际运行目标设置容器名；下面只是此前 live 使用的名字：

```bash
export V41_CONTAINER=dsv41-smoke-256k
bash -n agentmon/scripts/dsv41_metrics_collect_readonly.sh
bash agentmon/scripts/dsv41_metrics_collect_readonly.sh
```

采集在新代码目录创建新的 outputs，不改原实验记录。容器/Python/源码路径核查及失败处理见同一仓库根目录 `DSV41_HOOK_START_HERE_ZH.md`。

clone/pull 只更新宿主机代码文件，不会自动把 Hook 注入正在运行的 DSV41 容器。Hook 部署与重启仍是后续单独的人工操作。

## 6. 以后每次修改的固定流程

本地：我在当前工作区修改代码后，你通过 Git 客户端检查 Changes、Commit、Push。完成 Push 后，服务器执行：

```bash
cd ~/agentmon-dsv41-dev
git status --short
git pull --ff-only
git log -1 --oneline
```

先确认 status 没有需要保留的本地修改。若 pull 报冲突、分叉或无法 fast-forward，保留现场再处理，不使用 reset --hard、clean 或强制覆盖来解决。

然后执行对应阶段脚本。当前阶段仍是只读采集：

```bash
bash agentmon/scripts/dsv41_metrics_collect_readonly.sh
```

源码 commit 应记入每次实验目录。将 OUT 设置为本次脚本实际输出的目录：

```bash
OUT=/替换为本次实际输出目录
git rev-parse HEAD > "$OUT/source_commit.txt"
git status --short > "$OUT/source_worktree_status.txt"
```

若 worktree status 非空，commit 不能独自代表本次执行的完整代码；保留修改差异并说明。Git commit 标识 AgentMon 工作区版本，不替代容器 vLLM/Ascend 源码版本。

## 7. 当前先完成什么

1. 选择服务器能访问的 Git 平台。
2. 建立自己的私有仓库并推送这份完整代码。
3. 当前仓库URL已明确；核对首次提交推送成功和实际分支名。
4. 服务器执行 ls-remote → clone → 当前只读采集。

建立这条同步通道后，后续 Hook 适配、离线测试和实验脚本更新都走同一个 commit/push/pull 流程，无需逐个传文件。

官方参考：[创建仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)、[Fork 仓库](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/fork-a-repo?tool=webui)、[GitHub Desktop 克隆与 Fork](https://docs.github.com/en/desktop/adding-and-cloning-repositories/cloning-and-forking-repositories-from-github-desktop)。
