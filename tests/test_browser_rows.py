from tender_monitor.sources.browser_manual import (
    parse_rendered_rows,
    parse_rendered_total,
)


def test_parse_rendered_rows_uses_same_detail_route_as_script():
    html = """
    <table><tbody class="conn_list_items">
      <tr href="/jiangsu/js_cggg/details.html?gglb=cggg&amp;ggid=abc123">
        <td>01</td><td><a>摄影服务采购公告</a></td>
        <td>江苏省 - 南京市</td><td>2026-09-02</td>
      </tr>
    </tbody></table>
    """
    items = parse_rendered_rows(html)
    assert len(items) == 1
    assert items[0].title == "摄影服务采购公告"
    assert items[0].detail_url().endswith("gglb=cggg&ggid=abc123")


def test_rendered_item_can_be_ingested_as_unverified_official_record():
    html = """
    <table><tbody class="conn_list_items">
      <tr href="/jiangsu/js_cggg/details.html?gglb=cggg&amp;ggid=abc123">
        <td>01</td><td><a>视频录制服务比选公告</a></td>
        <td>江苏省 - 泰兴市</td><td>2026-09-02 12:22:00</td>
      </tr>
    </tbody></table>
    """
    record = parse_rendered_rows(html)[0].to_tender_record()
    assert record.source == "ccgp_jiangsu"
    assert record.url.endswith("gglb=cggg&ggid=abc123")
    assert record.budget_status == "UNKNOWN"
    assert record.raw_payload["detail_access"] == "public_detail_pending"


def test_rendered_total_is_optional_for_current_official_page_shape():
    assert parse_rendered_total("<span>共 12 条</span>") == 12
    assert parse_rendered_total('<tbody class="conn_list_items"><tr><td>1</td></tr></tbody>') is None
