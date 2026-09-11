# 原需求方案（立项留档）

> **来源**：项目立项时收到的最初需求方案（微信文件《爬虫(1).txt》，Windows 换行）。
> **性质**：需求原稿，仅作溯源与需求对照。**正文一字未改**，仅统一换行符为 LF、并在顶部加本说明块。
> **提示**：文中技术选型（Selenium、Windows 部署、PDF 全自动下载等）与数据源清单为当时设想；
> 实际实现范围与边界以 [README](../README.md)、[MANUAL](../MANUAL.md) 及 `docs/` 其他文档为准。

---

江苏地区摄影文旅宣传与文化类招标信息采集系统方案
一、项目概述
本项目旨在构建一套自动化招标信息采集系统，专用于抓取江苏省范围内与摄影、文旅宣传、文化相关的招标公告，预算以20万元以内为主，可浮动至30万元，支持每日定时汇总并自动下载解析PDF附件。

1.1 核心需求清单
需求项	具体要求
地域范围	江苏省（含省级、13个地市及区县级平台）
内容类目	摄影、文旅宣传、文艺演出、非遗保护、公共文化服务、文化活动策划等
预算区间	20万以内为主，20-30万标注[超预算]供人工筛选
部署方式	本地电脑运行（Windows）
开发方式	自行开发（Python）
推送方式	每日定时汇总（建议上午9:00）
附件处理	自动抓取并解析PDF附件内容
1.2 技术选型
组件	选型	理由
编程语言	Python 3.9+	生态丰富，招标爬虫案例成熟
动态渲染	Selenium / Playwright	苏采云系统为动态加载页面，需浏览器渲染
数据存储	SQLite	零配置，完全适合本地运行
PDF解析	pypdf / pdfplumber	提取PDF正文作为采购需求字段入库
定时调度	Windows 任务计划程序	本地电脑部署，设置每日上午9:00执行
前端看板	可选（FastAPI + 简易HTML）	或仅推送邮件/微信汇总
二、数据源清单
江苏省招投标平台站点分散，需重点覆盖以下渠道：

2.1 省级核心平台
平台名称	网址	说明
江苏政府采购网	www.ccgp-jiangsu.gov.cn	省级核心发布渠道
江苏省公共资源交易平台	http://jszfcg.jsczt.cn/	苏采云系统入口
2.2 地市级平台（13市）
南京、苏州、无锡、常州、镇江、扬州、泰州、南通、盐城、淮安、连云港、徐州、宿迁的公共资源交易中心或市级政府采购网。

⚠️ 徐州等地市的采购公告通过“苏采云”系统发布，需通过省级入口访问。

2.3 区县级补充（苏南强区）
昆山、江阴、常熟、张家港、武进、吴江等经济强区的政府采购分站，常有独立的文化宣传类采购项目。

提示：已有开源项目（如 bid_spider）明确规划支持江苏政府采购网，可作为参考。

三、技术实现方案
3.1 系统架构
text
┌─────────────────────────────────────────────────────────────┐
│                    定时调度（每日9:00）                      │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              多源采集层（Selenium/Playwright）              │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐      │
│  │省级站 │  │地市站1│  │地市站2│  │...  │  │区县站 │      │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘      │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              数据清洗与解析层                                │
│  关键词过滤 → 预算筛选 → 去重 → PDF下载 → PDF内容提取      │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              SQLite数据库存储                               │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              每日汇总报告（邮件/微信推送）                   │
└─────────────────────────────────────────────────────────────┘
3.2 核心模块代码示例
3.2.1 Selenium浏览器渲染配置
针对苏采云系统动态加载的特性，需使用Selenium驱动浏览器：

python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def init_driver():
    """初始化Chrome驱动，配置无头模式"""
    chrome_options = Options()
    chrome_options.add_argument('--headless')  # 无界面运行
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver

def fetch_tender_list(driver, url):
    """获取招标列表页"""
    driver.get(url)
    # 等待列表加载完成，苏采云系统需等待5-15秒
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.CLASS_NAME, "tender-item")))
    # 或根据实际页面元素调整选择器
    return driver.page_source
3.2.2 预算解析与浮动处理
python
import re

def parse_budget(budget_text):
    """
    解析预算金额，支持格式：
    - "20万元" / "20万"
    - "200000元"
    - "预算金额：20.00万元"
    """
    if not budget_text:
        return None
    
    # 提取数字
    numbers = re.findall(r'(\d+\.?\d*)', budget_text)
    if not numbers:
        return None
    
    budget = float(numbers[0])
    
    # 判断单位
    if '万' in budget_text or '万元' in budget_text:
        pass  # 已是万元单位
    elif '元' in budget_text and not '万' in budget_text:
        budget = budget / 10000  # 转换为万元
    
    return budget

def filter_by_budget(budget):
    """
    预算筛选逻辑：
    - <= 20万：正常收录
    - 20-30万：标注[超预算-需确认]
    - > 30万：过滤
    """
    if budget is None:
        return 'UNKNOWN'
    if budget <= 20:
        return 'NORMAL'
    elif budget <= 30:
        return 'OVER_BUDGET'
    else:
        return 'FILTERED'
3.2.3 PDF下载与内容提取
python
import requests
import pdfplumber
from pathlib import Path

def download_pdf(pdf_url, save_path):
    """下载PDF附件"""
    try:
        # 部分网站需携带Cookie或Session
        response = requests.get(pdf_url, timeout=30)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(response.content)
            return True
    except Exception as e:
        print(f"PDF下载失败: {e}")
    return False

def extract_pdf_text(pdf_path):
    """提取PDF正文内容"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ''
            for page in pdf.pages:
                text += page.extract_text() or ''
        return text
    except Exception as e:
        print(f"PDF解析失败: {e}")
        return ''
3.2.4 关键词配置词典
基于文旅宣传与文化类项目特征，配置以下关键词：

包含关键词（匹配任一即命中）：

类别	关键词
摄影	摄影、拍摄、图片、视觉、影像、采风
文旅宣传	文旅、文化旅游、宣传、推广、新媒体、短视频、达人、网红、自媒体
文化类	文化、文艺演出、剧目、非遗、文化遗产、博物馆、图书馆、文化节、文化服务
排除关键词（过滤无关）：
工程、施工、建筑、设备采购、医疗设备、信息化硬件、监理、检测、实验室

3.2.5 SQLite数据库设计
sql
-- 招标信息主表
CREATE TABLE tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,                -- 项目名称
    source TEXT,                        -- 来源平台
    province TEXT DEFAULT '江苏',      -- 省份
    pub_date TEXT,                      -- 发布时间
    deadline TEXT,                      -- 投标截止时间
    budget REAL,                        -- 预算金额（万元）
    budget_flag TEXT,                   -- NORMAL / OVER_BUDGET / FILTERED
    department TEXT,                    -- 采购单位
    content TEXT,                       -- 正文内容
    pdf_content TEXT,                   -- PDF解析内容
    url TEXT UNIQUE,                    -- 原文链接
    hash TEXT,                          -- 去重标识
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 附件表（存储下载的PDF路径）
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER,
    file_name TEXT,
    file_path TEXT,
    FOREIGN KEY (tender_id) REFERENCES tenders(id)
);

-- 索引
CREATE INDEX idx_pub_date ON tenders(pub_date);
CREATE INDEX idx_budget ON tenders(budget);
CREATE INDEX idx_hash ON tenders(hash);
3.3 定时任务配置
Windows环境下使用任务计划程序：

打开"任务计划程序" → 创建基本任务

触发器：每天 09:00

操作：启动程序 python.exe，参数为脚本路径 D:\bid_scraper\main.py

或编写批处理脚本 daily_run.bat：

batch
@echo off
cd D:\bid_scraper
python main.py
四、参考开源项目
项目	说明
BidMonitor-AI	集成Selenium绕过反爬，支持40+招标网站，含AI过滤和多渠道通知，MIT协议
bid_spider	支持江苏政府采购网，基于关键词爬取招标信息
TrendRadar	支持江苏政府采购网，可生成TXT/HTML报告，支持飞书/钉钉推送
CPWD eTender Scraper	使用Selenium抓取招标数据，导出CSV，可参考其翻页处理逻辑
建议：可基于 BidMonitor-AI 二次开发，在其框架基础上增加江苏地区定制化数据源和PDF解析模块，减少从零开发的工作量。

五、实施步骤
阶段	任务	预计耗时
第一阶段	环境搭建（Python + Selenium + 依赖库）	1天
第二阶段	编写省级平台（江苏政府采购网）采集模块	2-3天
第三阶段	扩展至3-5个地市平台	2-3天
第四阶段	实现PDF下载与内容提取	1-2天
第五阶段	实现关键词过滤 + 预算筛选逻辑	1天
第六阶段	配置定时任务 + 邮件/微信推送	1天
第七阶段	全量测试与优化	2-3天
六、注意事项
苏采云系统特殊性：江苏省多地政府采购通过"苏采云"系统发布公告，需使用谷歌浏览器访问，且下载采购文件需CA证书登录。爬虫仅能抓取公开可见的公告列表和摘要，附件中的详细采购需求文件（后缀为.kedt）通常需要登录才能下载，爬虫无法绕过该限制。

反爬策略：设置合理的请求间隔（建议5-10秒），必要时使用代理IP池轮换。

去重机制：采用 URL hash + 内容MD5 双重去重，避免重复推送。

合规声明：遵守各网站的 robots.txt，仅采集公开可访问的招标公告信息，不得用于商业牟利

数据备份：SQLite数据库定期备份至本地其他目录，防止数据丢失。

本方案基于2026年公开的招投标采集技术资料编制，具体实现需根据目标网站结构调整。
