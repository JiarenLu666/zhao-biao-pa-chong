# 招标爬虫（zhao-biao-pa-chong）使用手册

本手册覆盖安装、运行、数据源、验证码、自动调度、Dashboard、人工核验、跟进、通知、附件、报告、排障和 GitHub 发布。

## 1. 项目定位与边界

项目用于发现江苏省摄影、文旅宣传和公共文化类采购公告，采用“辅助源自动发现 + 官方源人工核验”。

只访问公开内容，不绕过验证码、登录、CA、付费墙或其他访问控制。聚合站列表中的预算只是线索，最终项目编号、预算、截止时间以原文核验为准。

主链路：

```text
调度/命令 → 公开列表 → 解析 → 关键词筛选 → 去重入库
       → CSV/摘要/复核队列 → Dashboard 核验与跟进
```

## 2. 支持平台和依赖

- Python 3.9 或更高版本。
- macOS、Linux、Windows 均支持核心 CLI 和 Dashboard。
- 运行依赖：`httpx`、`beautifulsoup4`、`pypdf`。
- 开发依赖：`pytest`、`ruff`。
- 官方验证码浏览器流程的可选依赖：`playwright`。

系统通知不增加 Python 依赖：macOS 使用 `osascript`，Windows 使用 PowerShell，Linux 优先使用 `notify-send`；通知能力不可用时，仍保留本地事件文件和 Dashboard 备用提示。

## 3. 目录结构

```text
zhao-biao-pa-chong/
├── config/sources.toml
├── data/                            # 数据库、报告、快照、证据
├── docs/                            # 侦察记录和接口契约
├── scripts/
│   ├── run-auto-refresh.sh
│   ├── run-dashboard.sh
│   ├── install-launchagent.sh
│   └── *.plist
├── src/tender_monitor/
├── tests/
├── pyproject.toml
├── README.md
└── MANUAL.md
```

### macOS 运行副本

macOS 后台访问 Desktop 目录可能被系统 TCC 拒绝。建议把后台运行副本放在：

```text
~/Library/Application Support/zhao-biao-pa-chong/
```

运行副本保存数据库、报告、快照和调度脚本；Dashboard 启动脚本使用当前源码，但读取运行副本数据库。

## 4. 安装

### macOS/Linux

```bash
git clone <你的 GitHub 仓库地址> zhao-biao-pa-chong
cd zhao-biao-pa-chong
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

如需官方验证码浏览器流程：

```bash
.venv/bin/python -m pip install -e ".[dev,browser]"
.venv/bin/python -m playwright install chromium
```

### Windows PowerShell

```powershell
git clone <你的 GitHub 仓库地址> zhao-biao-pa-chong
Set-Location zhao-biao-pa-chong
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

官方验证码浏览器依赖：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.venv\Scripts\python.exe -m playwright install chromium
```

安装检查：

```bash
.venv/bin/python -m tender_monitor.cli --help
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
```

## 5. 首次初始化

```bash
.venv/bin/python -m tender_monitor.cli setup --db data/tenders.sqlite3
```

该命令创建 SQLite、`data/raw`、`data/attachments` 和 `data/evidence`，不会覆盖已有数据库。

## 6. 自动采集

### 手动执行一次

```bash
.venv/bin/python -m tender_monitor.cli auto-refresh \
  --db data/tenders.sqlite3 \
  --scope jiangsu \
  --pages 2 \
  --page-size 50 \
  --time-type 1 \
  --snapshot-dir data/raw/okcis-auto \
  --report-output data/report.csv \
  --digest-output data/digest.txt \
  --review-output data/review.csv
```

- `--scope jiangsu`：江苏省级 OKCIS 聚合入口。
- `--scope taixing`：泰兴单点备用入口。
- `--pages 2`：单次最多两页。
- `--page-size 50`：每页最多 50 条。
- `--time-type 1`：站点的“最新”范围。

自动任务每次最多 2 页/100 条；请求之间至少 15 秒并加 0–5 秒抖动。401、429、`overLimitIP` 或“访问过于频繁”会熔断 1 小时，不自动重试。

输出文件：

- `data/tenders.sqlite3`：主数据库。
- `data/report.csv`：完整报告。
- `data/digest.txt`：短摘要。
- `data/review.csv`：人工复核队列。
- `data/raw/okcis-auto/`：列表快照。
- `data/attention.json`：人工处理事件。

## 7. 定时任务

### macOS LaunchAgent

后台副本默认位于：

```text
~/Library/Application Support/zhao-biao-pa-chong
```

首次准备运行副本时，在项目目录运行：

```bash
RUNTIME_DIR="$HOME/Library/Application Support/zhao-biao-pa-chong"
mkdir -p "$RUNTIME_DIR"
rsync -a --exclude '.git' --exclude '.venv' --exclude 'data' \
  ./ "$RUNTIME_DIR/"
python3 -m venv "$RUNTIME_DIR/.venv"
"$RUNTIME_DIR/.venv/bin/python" -m pip install -e "$RUNTIME_DIR"
"$RUNTIME_DIR/.venv/bin/python" -m tender_monitor.cli setup \
  --db "$RUNTIME_DIR/data/tenders.sqlite3"
```

以后只修改源码时，同步源码和脚本即可，不覆盖运行副本的 `data/` 和 `.venv/`：

```bash
RUNTIME_DIR="$HOME/Library/Application Support/zhao-biao-pa-chong"
rsync -a --exclude '.git' --exclude '.venv' --exclude 'data' \
  ./ "$RUNTIME_DIR/"
```

然后在项目目录运行：

```bash
scripts/install-launchagent.sh
```

安装脚本会根据当前用户名生成 LaunchAgent，安装到 `~/Library/LaunchAgents/` 并加载任务。计划时间为每天：

```text
08:00、10:00、12:00、14:00、16:00、18:00、20:00
```

检查：

```bash
launchctl print "gui/$(id -u)/com.jiangsu.tender-monitor.auto-refresh"
```

重点看 `state`、`runs`、`last exit code`、`ProgramArguments` 和日志路径。

查看日志：

```bash
RUNTIME_DIR="$HOME/Library/Application Support/zhao-biao-pa-chong"
tail -50 "$RUNTIME_DIR/data/auto-refresh.stdout.log"
tail -50 "$RUNTIME_DIR/data/auto-refresh.stderr.log"
```

只在需要验证时手动触发一次：

```bash
launchctl kickstart -k "gui/$(id -u)/com.jiangsu.tender-monitor.auto-refresh"
```

### Windows 任务计划程序

- 创建每日任务，开始时间 08:00。
- 高级设置为每 2 小时重复，持续 12 小时。
- 程序：`.venv\Scripts\python.exe`。
- 参数：`-m tender_monitor.cli auto-refresh --scope jiangsu --db data\tenders.sqlite3`。
- 起始位置：项目目录。
- 勾选“唤醒计算机运行”。

### Linux cron

使用绝对路径调用 `.venv/bin/python -m tender_monitor.cli auto-refresh`，并将工作目录设置为项目目录。不要使用常驻死循环。

## 8. 数据来源和验证码

### 自动来源：江苏招标网 OKCIS

```text
https://jiangsu.okcis.cn/sww/bn/
```

这是聚合发现源，不是政府官方发布平台。自动任务只访问静态列表，不请求详情页。

泰兴备用入口：

```text
https://taixingshi.okcis.cn/sww/bn/
```

### 官方来源：江苏政府采购网

```text
http://www.ccgp-jiangsu.gov.cn/jiangsu/cggg_search.html
```

官方列表接口需要验证码。运行：

```bash
.venv/bin/python -m tender_monitor.cli collect-ccgp-manual \
  --db data/tenders.sqlite3 \
  --start-date 2026-09-04 \
  --end-date 2026-09-04 \
  --pages 1 \
  --snapshot-dir data/raw/ccgp-manual
```

流程：

1. 浏览器打开官方查询页。
2. 用户手工输入验证码。
3. 回终端按回车继续。
4. 同一次查询会话可低频翻页。
5. 结果写入 SQLite 和快照。

不能使用 OCR、第三方打码、代理轮换、撞库或其他方式绕过验证码。自动任务不会调用官方验证码接口。

## 9. Dashboard

启动：

```bash
scripts/run-dashboard.sh
```

默认监听 `127.0.0.1:8765`、允许本机写入、自动打开浏览器，并读取运行副本数据库。

Dashboard 功能：

- 每 60 秒同步本地数据库。
- 漏斗卡片：公告总数、今日新增、待核验、已核验、已完成。
- 快速视图只保留“命中目标”。
- 详情抽屉直接编辑项目编号、预算、预算原文、截止时间、正文摘要和核验备注。
- 首次打开未核验公告会提示补充详情，保存后提示消失。
- 跟进状态：待跟进、跟进中、已完成、暂不跟进。
- “暂不跟进”的已核验项目显示红色“未命中目标”。
- 保存“已完成”后进入已完成视图。
- 详情可打开外部原文，不会自动提交验证码。

局域网模式：

```bash
.venv/bin/python -m tender_monitor.cli dashboard \
  --db data/tenders.sqlite3 \
  --host 0.0.0.0 \
  --port 8765
```

局域网模式默认只读。不要直接暴露公网；如需写入必须明确使用 `--allow-write`，并配合防火墙和可信网络。

## 10. 漏斗和状态

| 卡片 | 定义 |
|---|---|
| 公告总数 | 数据库中全部公告 |
| 今日新增 | 按中国时区统计发布日期为今天的公告 |
| 待核验 | 命中目标规则但尚未人工核验 |
| 已核验 | 人工核验完成，包括暂不跟进 |
| 已完成 | 已核验且跟进状态为已完成 |

列表状态：

- 疑似命中：机器命中目标、尚未人工核验。
- 待核验：预算或关键字段仍需人工确认。
- 命中目标：机器筛选为目标且未被人工排除。
- 未命中目标：人工核验后选择暂不跟进。
- 未命中：没有目标关键词或命中排除规则。

## 11. 人工核验和编辑

详情抽屉中可以填写：

- 项目编号
- 预算金额（人民币元）
- 预算原文
- 截止时间
- 正文摘要
- 核验备注

保存后会：

1. 写入本地 SQLite。
2. 标记为 `VERIFIED`。
3. 重新计算预算和筛选状态。
4. 保留已有跟进状态，不会把暂不跟进重置为待跟进。

预算只能从预算金额、采购预算、项目预算、最高限价等锚定字段确认；无法确认时保持 `UNKNOWN`。

## 12. 报告和摘要

```bash
.venv/bin/python -m tender_monitor.cli refresh --db data/tenders.sqlite3
```

导出 CSV：

```bash
.venv/bin/python -m tender_monitor.cli report \
  --db data/tenders.sqlite3 --format csv --output data/report.csv
```

导出 JSON：

```bash
.venv/bin/python -m tender_monitor.cli report \
  --db data/tenders.sqlite3 --format json --output data/report.json
```

导出复核队列：

```bash
.venv/bin/python -m tender_monitor.cli review \
  --db data/tenders.sqlite3 --output data/review.csv
```

生成摘要：

```bash
.venv/bin/python -m tender_monitor.cli digest \
  --db data/tenders.sqlite3 --output data/digest.txt
```

## 13. 通知

### 本地人工处理事件

遇到验证码、限流、DNS/网络错误或其他自动采集失败时写入 `data/attention.json`。Dashboard 会轮询事件并显示备用提示；程序同时尝试系统原生通知。同一未处理事件只通知一次，标记已处理后才会清除。

### Server酱 Turbo

```bash
export SERVERCHAN_SENDKEY='你的 SendKey'
.venv/bin/python -m tender_monitor.cli notify-serverchan \
  --db data/tenders.sqlite3
```

### PushPlus

```bash
export PUSHPLUS_TOKEN='你的 Token'
export PUSHPLUS_CHANNEL='wechat'
.venv/bin/python -m tender_monitor.cli notify-pushplus \
  --db data/tenders.sqlite3
```

凭据只从命令参数或环境变量读取，不写入 SQLite、报告或快照。不要把真实凭据提交到 GitHub。

## 14. 附件

只允许下载公开 URL，并限制单文件大小。文字型 PDF 使用 `pypdf`；扫描 PDF 可能为空文本并标记 `EMPTY`；不可读文件标记 `ERROR`。

不自动处理：

- CA 登录后的采购文件。
- `.kedt` 等私有文件。
- 需要 OCR 的扫描件。
- 需要验证码或登录的详情附件。

自动循环默认只采集列表元数据，不批量下载详情附件。

## 15. 去重、筛选和限流

去重顺序：

1. 归一化项目编号。
2. 归一化标题 + 发布日期。
3. 同日无编号时使用高阈值标题相似度兜底。

目标关键词包括摄影、拍摄、视频、影像、文旅、宣传、文化、博物馆、图书馆等；“宣传”单独出现不算有效命中，需要二级信号。

排除关键词包括工程、施工、建筑、监理、检测、实验室、维保、流标、废标、终止等。摄影、拍摄、图片、影像标题豁免宽泛的“设备采购”排除。

OKCIS 默认至少 15 秒间隔 + 0–5 秒抖动，单次最多 2 页/100 条；401、429、限流页进入 1 小时冷却，不自动重试；锁文件防止并行循环。

## 16. 常见问题

### Dashboard 打不开

```bash
curl -I http://127.0.0.1:8765/
scripts/run-dashboard.sh
```

确认浏览器读取的是运行副本数据库，而不是旧的 Desktop 数据库。

### Dashboard 不能保存

- 使用 `scripts/run-dashboard.sh` 启动。
- 确认地址是 `127.0.0.1` 或 `localhost`。
- 确认命令包含 `--allow-write`。
- 刷新页面后重新打开详情。
- 查看终端是否提示“当前 Dashboard 为只读模式”。

### LaunchAgent Operation not permitted

后台任务不应直接访问 Desktop。确认 LaunchAgent 指向非 Desktop 运行副本，再运行：

```bash
scripts/install-launchagent.sh
```

### 自动采集新增为 0

查看 stdout 中的 `stopped_reason`：

- `列表页为空`：源站返回空列表。
- `达到本次页数上限`：达到单次 2 页预算。
- 限流或验证码：等待冷却并按提示人工处理。

### 预算未公开

聚合站预算只是无标签提示。打开详情后填写锚定预算；不能确认时保持未知。

### 重新应用关键词规则

```bash
.venv/bin/python -m tender_monitor.cli reclassify --db data/tenders.sqlite3
.venv/bin/python -m tender_monitor.cli refresh --db data/tenders.sqlite3
```

## 17. 备份、升级和运行副本同步

升级前备份：

```bash
cp data/tenders.sqlite3 data/tenders.sqlite3.backup
```

macOS 源码修改后同步到运行副本，但不要覆盖运行副本的 `data/` 和 `.venv/`：

```bash
RUNTIME_DIR="$HOME/Library/Application Support/zhao-biao-pa-chong"
rsync -a --exclude '.git' --exclude '.venv' --exclude 'data' \
  ./ "$RUNTIME_DIR/"
```

修改依赖后重新安装并处理虚拟环境；只改源码或 Dashboard 时同步源码和脚本即可。

## 18. GitHub 发布清单

发布前检查：

```bash
rg -n "/Users/|SCT-[A-Za-z0-9]|PUSHPLUS_TOKEN|SERVERCHAN_SENDKEY" . \
  -g '!data/**' -g '!*.pyc'
```

确认：

- 没有个人绝对路径。
- 没有真实 Token、SendKey 或密码。
- 数据库、报告、快照和证据文件不提交。
- LaunchAgent 使用 `__RUNTIME_DIR__` 模板占位符。
- 客户通过 `scripts/install-launchagent.sh` 生成自己的路径。
- 示例密钥全部是占位值。
- 测试和静态检查通过。

发布前运行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
/bin/sh -n scripts/*.sh
plutil -lint scripts/com.jiangsu.tender-monitor.auto-refresh.plist
```

## 19. 当前交付基线

已验证能力：

- OKCIS 江苏省级列表自动采集。
- 每 2 小时 LaunchAgent 调度。
- 低频、去重、熔断和锁文件。
- 官方源可见浏览器人工验证码流程。
- SQLite、报告、摘要和复核队列。
- Dashboard 自动同步、漏斗统计、详情编辑和跟进。
- 验证码、限流、失败事件记录。
- macOS、Windows、Linux 原生通知适配。

官方源继续保留人工验证码边界；无人值守运行只使用无需验证码的公开辅助列表源。
