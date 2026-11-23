# AutoSubHub → V2RayTun Termux Bridge（第一阶段）

本工具先完成“把 AutoSubHub 待测速候选节点交给 V2RayTun”。它不会调用 Mihomo，也不会声称已经执行 V2RayTun 真实连接测速。

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

转换成 V2RayTun 分享链接：

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

然后在 V2RayTun 中使用剪贴板/分享链接导入入口。第一阶段不自动点击测速或删除，避免未验证 UI 行为时误操作。

## 支持范围与安全边界

转换逻辑与 AutoSubHub 现有最终订阅转换器保持一致，支持 VMess、SS、VLESS、Trojan、SOCKS5。其他协议记录在 map 文件的 skipped 数组中，不会被误认为测速失败。

运行前校验候选 YAML、manifest 和（如存在）meta 的 SHA256，并验证节点名称与 node_id 一一对应。校验失败拒绝导出。

本阶段不修改 V2RayTun APK、不调用其私有 JNI、不自动测速、不删除节点、不上传结果。下一阶段先实测剪贴板导入，再研究真实测速结果的可靠读取方式。
