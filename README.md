# 江苏文化招标信息采集器

面向江苏省摄影、文旅宣传和文化服务采购公告的本地采集与提醒工具。

完整安装、运行、验证码、Dashboard、调度和 GitHub 发布说明见 [MANUAL.md](MANUAL.md)。

当前已完成自动循环、官方样本验收和本地报告刷新；省网官方列表仍保留人工验证码边界。

## 第一版边界

- 自动来源：OKCIS 江苏省级聚合入口（可用 `--scope taixing` 退回泰兴单点）只采集静态列表元数据；详情有算术验证码，必须回到官方源核验，不作为权威预算来源。
- 官方核验源：江苏政府采购网（`ccgp-jiangsu`）检索页、公开列表接口和可见浏览器人工验证码流程。
- 公告分类：官方列表接口使用 `cglx`，详情链接使用 `gglb`；分类清单以实测页面为准。
- 苏采云：目前只登记公开可见的列表/详情元数据，不绕过登录、CA 或附件权限。
- 当前限制：官方列表接口要求验证码；同一查询会话可用一个验证码翻页，但不能无人值守直连。自动任务只使用无需验证码的 OKCIS 列表。
- 访问保护：自动源默认每次请求间隔 15 秒并加入 0–5 秒抖动，单次最多 2 页/100 条。遇到 401、429 或 `overLimitIP`/“访问过于频繁”后立即熔断，冷却 1 小时且不自动重试。
- 存储：SQLite；原始 HTML 和附件放在本地 `data/` 目录。
- 推送：支持免费 Server酱 Turbo 微信通知，并保留 PushPlus 兼容命令；未配置凭据时仍只生成本地报告。

## 目录

```text
config/                  来源和筛选配置
docs/                    阶段记录、字段和验收标准
src/tender_monitor/      Python 包
tests/                   自动化测试
data/raw/                原始页面快照（不提交）
data/attachments/        附件缓存（不提交）
```

## 本地开发

Python 3.9+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

Windows 下将 `source` 替换为 `.venv\\Scripts\\activate`。

## 首次使用 Dashboard

首次使用时，在项目目录执行：

```bash
tender-monitor setup
tender-monitor dashboard --open
```

`setup` 会创建本地数据目录和 SQLite 数据库，已有数据不会被覆盖。
`dashboard` 默认只监听本机 `127.0.0.1:8765`；本机模式可以在公告详情里维护跟进状态和备注，不会访问官网。启动后可在浏览器中查看统计、筛选公告并打开原文。按 `Ctrl+C` 停止服务。

如果要让同一可信局域网的同事查看，可显式指定监听地址：

```bash
tender-monitor dashboard --host 0.0.0.0 --port 8765
```

此模式默认只读，适合给同事查看；请配合系统防火墙，仅在可信网络中使用，不要把端口直接暴露到公网。若确实需要在局域网内维护跟进状态，必须显式增加 `--allow-write`，并仅对可信网络开放。

## 离线导入与报告

官网冷却期间可直接用已保存的详情快照验证完整本地链路：

```bash
tender-monitor db-init --db data/tenders.sqlite3
tender-monitor import-detail data/raw/sample_detail.json \
  --url 'http://www.ccgp-jiangsu.gov.cn/jiangsu/js_cggg/details.html?gglb=gkzb&ggid=165b9138b486435bb2e10276f29e5618' \
  --db data/tenders.sqlite3
tender-monitor report --db data/tenders.sqlite3 --format csv --output data/report.csv
tender-monitor digest --db data/tenders.sqlite3 --output data/digest.txt
```

导入会保存筛选状态、命中理由和公开附件元数据；重复导入同一项目不会新增记录。PDF 附件下载和文字提取接口见 `tender_monitor.attachments`、`tender_monitor.pdf`，不处理登录、CA 或验证码。

`import-detail`、`verify`、`collect-okcis` 和 `collect-ccgp-manual` 在修改数据库后都会自动刷新
`data/report.csv`、`data/digest.txt` 和 `data/review.csv`；也可以单独运行 `tender-monitor
refresh` 重建三份输出。

筛选结果使用 `MATCH`、`OVER_BUDGET`、`REVIEW`、`NO_MATCH`、`FILTERED` 五种状态；排除词和命中词会以 JSON 字段写入数据库，方便人工复核和调整词表。

## 当前下一步

1. 先用 OKCIS 的公开列表跑通一次低频采集（只抓列表，不打开详情验证码页）：

   ```bash
   tender-monitor collect-okcis --db data/tenders.sqlite3 --pages 1 \
     --time-type 1 --snapshot-dir data/raw/okcis-taixing
   ```

   默认每次请求间隔 15 秒并加 0–5 秒抖动；一次最多 2 页/100 条。需要扩大到近三天时可将 `--time-type` 改为 `8`，仍不要连续扫历史页。
2. 查看 `report`/`digest`，人工标注 20–50 条公告，校准预算字段和关键词评分；OKCIS 列表预算默认只是提示，都会进入 `REVIEW`。
3. 官网冷却后，在人工输入一次验证码的浏览器会话中做一轮**低频**列表联调；每次运行先限定当天/24 小时窗口，再把官方结果与辅助源去重合并。

官方半自动联调入口已经准备好。首次使用需安装可选浏览器依赖（只需一次）：

```bash
python -m pip install -e '.[browser]'
python -m playwright install chromium
```

然后运行当天、1 页查询：

```bash
tender-monitor collect-ccgp-manual --db data/tenders.sqlite3 --pages 1 \
  --start-date 2026-09-02 --end-date 2026-09-02 \
  --snapshot-dir data/raw/ccgp-manual
```

日常建议省略 `--title`，用一次宽查询抓取当天列表，再由本地关键词规则筛选“摄影/拍摄/视频/文旅宣传”等目标；不要为每个关键词单独提交查询。浏览器打开后，验证码必须由人工直接填写；程序不会 OCR、计算、提交第三方打码，也不会自动重试限流。同一查询会话内翻页复用这次验证码，但更换查询条件通常需要重新验证。

修改筛选规则后，可用以下命令让已有数据库记录重新评估：

```bash
tender-monitor reclassify --db data/tenders.sqlite3
```

为人工核验准备一份去掉噪声的复核队列（只保留尚未核验的 `MATCH`、
`OVER_BUDGET`、`REVIEW`）：

```bash
tender-monitor review --db data/tenders.sqlite3 --output data/review.csv
```

核验后再次运行 `report` 和 `digest` 即可刷新完整报告与跟进摘要；复核队列不会
把已经标记为 `VERIFIED` 的公告重复列出。

也可以用一条命令完成重新分类和三份本地输出：

```bash
tender-monitor refresh --db data/tenders.sqlite3
```

### 微信通知（免费 Server酱）

在 [Server酱 Turbo](https://sct.ftqq.com/) 用微信扫码获取以 `SCT` 开头的 SendKey，
只通过环境变量提供，不要写入配置文件：

```bash
export SERVERCHAN_SENDKEY='你的 Server酱 Turbo SendKey'
tender-monitor notify-serverchan --db data/tenders.sqlite3
```

程序只在存在 `MATCH`、`OVER_BUDGET` 或 `REVIEW` 候选时发送；没有命中目标，或该公告
已经通过 Server酱发送过时，不会重复提醒。发送成功后会把公告 ID 写入 SQLite 的
`notification_deliveries` 表；因此重复调度、重复抓取或重新分类都不会重复推送。

`scripts/run-auto-refresh.sh` 会优先使用 `SERVERCHAN_SENDKEY` 发送新的命中摘要；未设置时
不会发送网络通知。通知失败会让调度脚本返回非零，便于调度器发现问题。原有 PushPlus
命令仍保留作兼容，但不再是默认渠道：

```bash
export PUSHPLUS_TOKEN='你的 PushPlus Token'
tender-monitor notify-pushplus --db data/tenders.sqlite3
```

人工核验详情（例如从截图确认了项目编号、预算和截止时间）可在 Dashboard 详情抽屉中直接补充并保存；保存会标记 `VERIFIED`，重新计算筛选状态，并保留核验备注。命令行 `verify` 仍可作为批量或备用入口。

列表接口的已确认响应形状和离线解析边界见 [`docs/list-api-contract.md`](docs/list-api-contract.md)；泰兴辅助源记录见 [`docs/okcis-taixing-recon-2026-09-02.md`](docs/okcis-taixing-recon-2026-09-02.md)。

详细边界见 [`docs/phase-1-recon.md`](docs/phase-1-recon.md)。

### 跟进状态

Dashboard 默认只监听本机；本机模式可以在公告详情里编辑人工核验字段、维护跟进状态和备注。首次打开未核验公告时会提示补充详情，保存后提示消失。已经人工确认且仍需要行动的项目会显示为“已核验待跟进”；已完成和暂不跟进的项目会从待处理统计与摘要中移出。

如果使用 --host 0.0.0.0 给同事查看，网页默认只读；只有在可信局域网内确实需要共同维护时，才显式增加 --allow-write。

## 低频自动采集

自动循环默认使用不需要验证码的 OKCIS 江苏省级聚合入口（`jiangsu.okcis.cn`），
读取其收录的江苏各地公开列表；官方江苏政府采购网仍需人工验证码，不会被后台任务强行调用。
一次循环会采集最新列表、按项目编号或标题/日期去重、刷新 CSV/摘要/复核队列，并写入原始 HTML 快照。

运行命令：

    tender-monitor auto-refresh --db data/tenders.sqlite3 --scope jiangsu

在 macOS/Linux 上也可以直接把 scripts/run-auto-refresh.sh 交给调度器，它会自动定位项目目录并写入 data 下的报告文件。

`--scope taixing` 可退回旧的泰兴单点入口。默认每次最多采集 2 页，但会按源站返回的实际页数提前停止；因此只有确实存在第 2 页时才会发出第 2 个请求。程序内置每次请求至少间隔 15 秒并加入 0–5 秒抖动，单次最多 2 页/100 条；检测到 401、429 或“访问过于频繁”会立即熔断，不自动重试。锁文件会防止两个调度任务同时运行。

建议使用 macOS launchd、Windows 任务计划程序或 Linux cron，在每天 08:00 到 20:00 之间每 2 小时触发一次上述命令。调度器只负责启动进程，进程完成采集和本地输出后自动退出。

macOS 运行副本默认位于 `~/Library/Application Support/zhao-biao-pa-chong`。首次安装请在项目目录执行
`scripts/install-launchagent.sh`，脚本会根据当前用户生成 LaunchAgent 并启用每天 08:00–20:00、每 2 小时运行的任务。
`scripts/com.jiangsu.tender-monitor.auto-refresh.plist` 中的 `__RUNTIME_DIR__` 是模板占位符，不要直接复制；
带有 `EnvironmentVariables.SERVERCHAN_SENDKEY` 占位值的 `.plist.example` 只用于参考。Desktop 下的目录保留为源码和手动操作入口；后台任务和 Dashboard 如需读取最新自动采集结果，应使用运行副本路径。

从项目目录启动 Dashboard：

    scripts/run-dashboard.sh

该入口使用项目下的当前源码，但读取 `~/Library/Application Support/zhao-biao-pa-chong/data/tenders.sqlite3`，因此会显示后台自动采集的最新数据；可继续追加 `--port` 等 Dashboard 参数。

后台任务遇到验证码、限流、DNS/网络错误或采集失败时会写入 `data/attention.json` 并尝试发送系统通知：macOS 使用系统通知，Windows 使用 PowerShell 气泡通知，Linux 使用 `notify-send`（均为系统能力，不新增 Python 依赖）。Dashboard 也会轮询该事件并提供备用提示；通知不可用时仍可在本地 Dashboard 看到待处理事件。
