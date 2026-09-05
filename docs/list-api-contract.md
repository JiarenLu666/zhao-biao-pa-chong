# 省网列表接口离线契约

本契约来自 2026-09-02 保存的 `data/raw/cggg_search.js`，用于先在本地完成解析和测试。它不是对官网接口的绕过，也不代表可以脱离验证码无人值守调用。

## 成功响应

页面脚本只读取以下字段：

```json
{
  "code": 200,
  "result": {
    "count": 1,
    "pageNo": 1,
    "list": [
      {
        "id": "公告 ID",
        "type": 1,
        "ggCode": "cggg",
        "title": "公告标题",
        "summary": "摘要",
        "pZoneName": "江苏省",
        "zoneName": "南京市",
        "publishDate": "2026-09-02 09:00:00"
      }
    ]
  }
}
```

当 `type == 1` 且同时有 `ggCode`、`id` 时，详情链接按以下规则构造：

`/jiangsu/js_cggg/details.html?gglb=<ggCode>&ggid=<id>`

## 失败响应与限流

- `code != 200`（例如“验证码不能为空”）必须保留为失败，不能转换为空列表。
- HTTP 401/429，或正文包含 `overLimitIP`、“访问过于频繁”，视为限流。
- 限流后由 `RequestGuard` 打开冷却闸门；调用方不得自动重试。

解析器和上述失败路径均有离线回归测试，使用虚拟响应，不访问官网。

浏览器人工模式位于 `tender_monitor.sources.browser_manual`：它只启动可见浏览器，
提示用户手工输入验证码，然后在同一会话中按节流策略翻页；不支持 headless，
也不调用 OCR、验证码平台或代理轮换。命令行入口为
`tender-monitor collect-ccgp-manual`，默认只采集当天 1 页并保存渲染快照；
列表记录会先以 `public_detail_pending` 状态入库，详情核验通过 `verify` 命令完成。
日常运行应使用一次不带标题关键词的宽查询，再在本地筛选目标词；同一查询会话
可以低频翻页复用验证码，不应为每个关键词连续提交新查询。
