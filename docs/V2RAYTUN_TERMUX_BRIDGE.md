# AutoSubHub → V2RayTun（云端候选格式 + Termux 桥接）

候选构建工作流会在生成 `candidates/candidates.yaml` 时，同步生成 V2RayTun Base64 订阅、逐行分享链接和 node_id 映射文件。原始 YAML/Mihomo 流程不变。Termux 桥接仍可用于把手机累计待测队列重新转换成一个合并订阅。此步骤只负责格式准备，不代表已经执行 V2RayTun 真实连接测速。

## 安装

在 AutoSubHub 仓库根目录的 Termux 中：

```sh
pkg update -y
pkg install -y python termux-api
python -m pip install pyyaml
```

使用剪贴板功能还需安装 Android 应用 **Termux:API**，并确保它与 Termux 来自兼容的安装渠道。

## 操作

先下载候选列表（沿用现有 AutoSubHub 流程）：

```sh
python download_candidates.py --output candidate-cache/candidate-local.yaml
```

如果只处理当前单批次，GitHub Actions 产物中已经包含以下文件：\n\n- `candidates/v2raytun-sub.txt`：Base64 订阅。\n- `candidates/v2raytun-links.txt`：逐行分享链接。\n- `candidates/v2raytun-map.json`：原始 node_id 对照表。\n\n手机端若使用累计待测队列，仍可按下面命令对下载后的累计 YAML 重新转换：

```sh
python tools/v2raytun_bridge.py --candidate-file candidate-cache/candidate-local.yaml
```

生成三个文件：

- `candidate-cache/v2raytun-import.txt`：Base64 分享链接订阅。
- `candidate-cache/v2raytun-import.links.txt`：逐行原始分享链接。
- `candidate-cache/v2raytun-import.map.json`：导出行与原始 node_id 的对应关系。

可选地复制 Base64 订阅到剪贴板：

```sh
python tools/v2raytun_bridge.py --candidate-file candidate-cache/candidate-local.yaml --copy
```

在 V2RayTun 中手动选择剪贴板导入。随后手动执行：

1. `Real delay all configurations`（真实连接延迟测试）。
2. `Delete invalid config (Test first)`（测试后删除无效配置）。
3. `Export non-custom configs to clipboard`（导出保留配置）。

回到 Termux 执行：

```sh
bash v2raytun-termux.sh collect
```

脚本会将剪贴板导出的分享链接与 node_id 映射表匹配，生成 `candidate-cache/v2raytun-survivors.json`。只有成功匹配到的导出保留节点会记为用户确认的 passed；未匹配、未导出或缺失的节点会放进 unresolved，不会被误判为 failed。\n\n检查结果文件后，运行下面命令回传 GitHub：\n\n```sh\nbash v2raytun-termux.sh upload\n```\n\n结果会写入独立的 `v2raytun-receipts` 分支（`receipts/v2raytun/`），不会覆盖 Mihomo 回执，也不会推进 Mihomo 的待测批次游标。若使用 HTTPS 上传，请先在 Termux 当前会话设置有仓库写入权限的 `GITHUB_TOKEN`；否则脚本会尝试 SSH。

公开发布分支 `published-subscriptions` 的 `candidate/` 目录也会同步发布云端单批次的三个 V2RayTun 文件。当前版本仍需要用户在 V2RayTun 内手动导入、测速、删除和导出；Termux 不会伪装成自动点击，也不会未经验证调用私有 JNI。

## 支持范围与安全边界

转换逻辑与 AutoSubHub 现有最终订阅转换器保持一致，支持 VMess、SS、VLESS、Trojan、SOCKS5。其他协议记录在 map 文件的 skipped 数组中，不会被误认为测速失败。

运行前校验候选 YAML、manifest 和（如存在）meta 的 SHA256，并验证节点名称与 node_id 一一对应。校验失败拒绝导出。

本阶段不修改 V2RayTun APK、不调用其私有 JNI、不自动测速、不删除节点、不上传结果。下一阶段先实测剪贴板导入，再研究真实测速结果的可靠读取方式。
