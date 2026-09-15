# 离线主链路

官网冷却期间，项目已经可以对保存的详情响应完成以下处理：

1. `parse_detail_payload` 将详情 JSON 转成统一的 `TenderRecord`。
2. `evaluate_record` 对标题和正文做保守关键词评分。
   - `摄影设备采购` 不会因为宽泛的“设备采购”被排除。
   - “宣传”单独出现不计为有效命中，需有文旅/推广/新媒体等二级信号。
   - 正文中的“工程技术有限公司”不会单独触发工程排除；标题中的工程/施工等仍会排除。
3. `duplicate_key` 优先使用归一化项目编号，没有项目编号时使用归一化标题+发布日期兜底。
4. `TenderDatabase` 保存公告、筛选结论和附件元数据；重复导入按项目编号或 URL 更新。
5. `download_public_attachment` 只下载公开 URL，限制大小并清理文件名；批量下载时应复用同一个 `RequestGuard`，401/429/限流页直接停止。
6. `extract_pdf_text` 使用 pypdf；文字型 PDF 为 `EXTRACTED`，扫描件为空文本并标记 `EMPTY`，不可读文件标记 `ERROR`。
7. `report` 命令导出带筛选理由的 UTF-8 CSV 或 JSON，供人工复核和后续推送使用。
8. `digest` 命令只输出 `MATCH`、`OVER_BUDGET`、`REVIEW`，生成短摘要供 Server酱、PushPlus 等渠道使用。
9. `review` 命令导出尚未人工核验的可跟进队列，排除明确过滤项和已核验项目，减少人工翻阅噪声。

## 首次使用 Dashboard

Dashboard 是本地网页，不负责采集，也不会绕过验证码。默认只监听本机，本机模式可维护跟进状态；绑定局域网地址时默认只读：

```bash
tender-monitor setup
tender-monitor dashboard --open
```

默认只监听 `127.0.0.1:8765`。局域网共享必须显式使用 `--host 0.0.0.0`，并配合防火墙和可信网络；公网部署暂不属于第一版范围。

## OKCIS 列表采集入口

江苏省级入口默认用于自动循环；如需单独采集泰兴，可使用兼容入口：

```bash
tender-monitor collect-okcis --db data/tenders.sqlite3 --pages 1 \
  --time-type 1 --snapshot-dir data/raw/okcis-taixing
```

泰州全域自动循环：

```bash
tender-monitor auto-refresh --scope taizhou --db data/tenders.sqlite3
```

命令只请求列表页，不请求带算术验证码的详情页。默认请求间隔为 15 秒并加入
0–5 秒抖动，每站每次最多 2 页/100 条；返回 401、429 或限流提示会立即熔断，
不会自动重试。列表项的无标签金额只保存在 `budget_raw`，`budget_status` 保持
`UNKNOWN`，因此目标关键词命中后会进入 `REVIEW`，需人工回到官方源核验。

关键词规则调整后，已有记录不会在导出时隐式改变；显式运行
`tender-monitor reclassify --db data/tenders.sqlite3`，再重新生成报告和摘要。

人工复核队列可单独导出：

```bash
tender-monitor review --db data/tenders.sqlite3 --output data/review.csv
```

日常采集完成后，也可以用 `refresh` 一次完成重新分类、完整报告、摘要和复核队列：

```bash
tender-monitor refresh --db data/tenders.sqlite3
```

列表详情需要验证码时，人工核验结果可在 Dashboard 详情抽屉中写回项目编号、预算、截止时间、正文摘要和核验备注；这一步不自动提交或解析验证码。命令行 `tender-monitor verify` 仍可作为备用入口，例如：

```bash
tender-monitor verify --db data/tenders.sqlite3 \
  --url 'https://taixingshi.okcis.cn/dnww20260902131145324786.html' \
  --project-id 'JSZC-321283-FW2026-15690' \
  --budget-yuan 9000 --budget-raw '¥9,000' \
  --deadline '2026-09-07 13:00' \
  --source '用户截图人工核验' \
  --evidence-path 'data/evidence/okcis-taixing-2026-09-02-video-recording.png'
```

后续再次导入同一条稀疏列表摘要时，已标记 `VERIFIED` 的项目编号、预算、截止时间和正文不会被摘要中的空字段覆盖。

这条链路没有调用官网，全部由 `tests/` 中的离线测试和 `data/raw/sample_detail.json` 验证。

### 跟进状态

Dashboard 的项目详情支持四种跟进状态：PENDING（待跟进）、IN_PROGRESS（跟进中）、DONE（已完成）和 NOT_REQUIRED（暂不跟进）。本机 Dashboard 可直接保存核验字段、状态和备注；已完成或暂不跟进的公告不会再次计入待处理统计、复核队列或 digest 摘要中。绑定局域网地址时默认只读，如需写入必须显式使用 --allow-write。

## 低频自动循环

自动循环入口为 `tender-monitor auto-refresh`，默认采集泰州全域
七个 OKCIS 子站（市级 `taizhou.okcis.cn` + 海陵 + 高港 + 泰兴 + 靖江 + 兴化 + 姜堰）；
`--scope cities13` 可扩到江苏 13 个地级市市级站（南京 → 宿迁，均已于 2026-09-15
低频实测，列表路径均返回 HTTP 200 且解析器兼容）；
`--scope taixing` 可退回旧的泰兴单点入口。江苏省级聚合入口（`jiangsu.okcis.cn`）
已退出自动调度，`--scope jiangsu` 仅保留为手动单跑对照入口。多站共享同一 RequestGuard：
节流间隔保持 15 秒 + 0–5 秒抖动不变，单次运行的
页数/记录数预算按站数动态放宽。江苏政府采购网仍由
`collect-ccgp-manual` 负责人工验证码流程。每站每次最多 2 页，但会按源站实际页数提前停止，
只有确实存在下一页时才请求；任一站返回 401、429 或限流提示都会立即停止整轮并熔断。
循环结束后自动重分类并生成报告、摘要和人工复核队列，
SQLite 的项目编号/标题日期去重保证重复调度不会堆积重复记录。

数据保留：每轮结束时自动清理超过保留期（默认 7 天）的旧公告——附件记录与
通知发送账本依赖外键级联一并删除——以及快照目录中超过保留期的 HTML 文件；
也可用 `tender-monitor cleanup --db data/tenders.sqlite3 --retention-days 7`
手动触发，输出 JSON 汇总（`purged_tenders` / `purged_snapshots`）。

若设置 `SERVERCHAN_SENDKEY`，调度脚本会在采集完成后仅推送尚未发送过的新命中；
发送成功的公告记录在 `notification_deliveries` 表，失败则不记账，下一次会安全重试。

验证码、限流或自动采集失败会写入本地 `data/attention.json`。程序按操作系统尝试
使用原生通知（macOS `osascript`、Windows PowerShell、Linux `notify-send`），不安装
额外 Python 通知依赖；Dashboard 同时提供本地事件提示和“打开官方查询”入口。

macOS/Linux 可直接调度 scripts/run-auto-refresh.sh；Windows 任务计划程序则调用 .venv\Scripts\tender-monitor.exe auto-refresh，并把“起始位置”设为项目目录。

调度频率为全天每 1 小时 1 轮（24 轮）：每轮跑泰州全域 7 站各 1 次
（省站轮已退役）。泰州市级站为全域聚合站，每小时 1 页（50 条）即可覆盖，
无需调大 `--pages`。
Windows 任务计划同样配置为每小时触发一次。不要使用常驻死循环，也不要为了追求实时性缩短站点请求间隔。

采集观察：`tender-monitor analyze-log` 读取 `data/auto-refresh.stdout.log`
（每轮一行 JSON），按中国时区分天×小时汇总轮数与入库量，并把小时级明细写入
`data/observe-report.csv`；`--days` 控制只统计最近 N 天。

macOS 的静默 launchd 配置见 `scripts/com.jiangsu.tender-monitor.auto-refresh.plist`；带通知占位值的 `.plist.example` 只用于参考。安装前请确认项目绝对路径和日志目录。
