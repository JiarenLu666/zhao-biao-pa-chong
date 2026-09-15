import json
from urllib.parse import parse_qs

import httpx

from tender_monitor.notifications import (
    NotificationError,
    _serverchan_endpoint,
    actionable_rows,
    format_digest,
    send_pushplus_message,
    send_serverchan_message,
)


def test_digest_includes_reviewable_items_and_omits_filtered_noise():
    rows = [
        {
            "title": "摄影服务采购项目",
            "city": "南京市",
            "budget_raw": "18万元",
            "filter_status": "MATCH",
            "url": "https://example.invalid/1",
        },
        {
            "title": "建筑施工项目",
            "city": "苏州市",
            "budget_raw": "10万元",
            "filter_status": "FILTERED",
            "url": "https://example.invalid/2",
        },
    ]
    digest = format_digest(rows)
    assert "摄影服务采购项目" in digest
    assert "建筑施工项目" not in digest
    assert "https://example.invalid/1" in digest


def test_digest_labels_verified_items_as_follow_up():
    digest = format_digest(
        [
            {
                "title": "视频录制比选公告",
                "city": "泰兴市",
                "budget_raw": "¥9,000",
                "filter_status": "MATCH",
                "verification_status": "VERIFIED",
                "url": "https://example.invalid/verified",
            }
        ]
    )
    assert "已核验待跟进 1 条" in digest


def test_digest_omits_completed_verified_follow_up_items():
    digest = format_digest(
        [
            {
                "title": "已完成跟进的视频项目",
                "city": "泰兴市",
                "budget_raw": "¥9,000",
                "filter_status": "MATCH",
                "verification_status": "VERIFIED",
                "follow_up_status": "DONE",
                "url": "https://example.invalid/done",
            },
            {
                "title": "仍待跟进的摄影项目",
                "city": "南京市",
                "budget_raw": "18万元",
                "filter_status": "MATCH",
                "verification_status": "VERIFIED",
                "follow_up_status": "PENDING",
                "url": "https://example.invalid/pending",
            },
        ]
    )
    assert "已完成跟进的视频项目" not in digest
    assert "仍待跟进的摄影项目" in digest
    assert "已核验待跟进 1 条" in digest


def test_actionable_rows_excludes_completed_items():
    rows = [
        {"filter_status": "MATCH", "verification_status": "UNVERIFIED"},
        {
            "filter_status": "MATCH",
            "verification_status": "VERIFIED",
            "follow_up_status": "DONE",
        },
        {
            "filter_status": "REVIEW",
            "verification_status": "UNVERIFIED",
            "follow_up_status": "NOT_REQUIRED",
        },
    ]

    assert actionable_rows(rows) == [rows[0]]


def test_send_pushplus_message_posts_wechat_payload():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 200, "msg": "请求成功"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_pushplus_message(
            "江苏文化采购公告\n1. 视频拍摄服务",
            "test-token",
            title="命中提醒",
            client=client,
            endpoint="https://example.invalid/send",
        )

    assert len(requests) == 1
    assert requests[0].url.path == "/send"
    assert json.loads(requests[0].read()) == {
        "token": "test-token",
        "title": "命中提醒",
        "content": "江苏文化采购公告\n1. 视频拍摄服务",
        "template": "txt",
        "channel": "wechat",
    }


def test_send_pushplus_message_supports_qq_channel_configuration():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 200})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_pushplus_message(
            "hello",
            "test-token",
            channel="qq",
            option="qqgroup",
            client=client,
            endpoint="https://example.invalid/send",
        )

    assert json.loads(requests[0].read())["channel"] == "qq"
    assert json.loads(requests[0].read())["option"] == "qqgroup"


def test_send_pushplus_message_raises_on_channel_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 400, "msg": "invalid token"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            send_pushplus_message(
                "hello",
                "test-token",
                client=client,
                endpoint="https://example.invalid/send",
            )
        except NotificationError as exc:
            assert "400" in str(exc)
        else:
            raise AssertionError("expected NotificationError")


def test_send_serverchan_message_posts_turbo_form_payload():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "message": "发送成功"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_serverchan_message(
            "江苏文化采购公告\n1. 视频拍摄服务",
            "SCT-test-key",
            title="命中提醒",
            client=client,
            endpoint="https://example.invalid/SCT-test-key.send",
        )

    assert len(requests) == 1
    assert requests[0].url.path.endswith(".send")
    assert parse_qs(requests[0].read().decode()) == {
        "title": ["命中提醒"],
        "desp": ["江苏文化采购公告\n1. 视频拍摄服务"],
    }


def test_send_serverchan_message_raises_on_channel_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 400, "message": "无效 SendKey"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            send_serverchan_message(
                "hello",
                "SCT-test-key",
                client=client,
                endpoint="https://example.invalid/send",
            )
        except NotificationError as exc:
            assert "400" in str(exc)
            assert "SCT-test-key" not in str(exc)
        else:
            raise AssertionError("expected NotificationError")


def test_serverchan_endpoint_supports_turbo_and_sc3_sendkeys():
    assert _serverchan_endpoint("SCT1234567890abcdef") == (
        "https://sctapi.ftqq.com/SCT1234567890abcdef.send"
    )
    assert _serverchan_endpoint("sctp1379tSC3SAMPLEKEY") == (
        "https://1379.push.ft07.com/send/sctp1379tSC3SAMPLEKEY.send"
    )
    try:
        _serverchan_endpoint("not-a-sendkey")
    except ValueError as exc:
        assert "sctp" in str(exc)
    else:
        raise AssertionError("未知前缀的 SendKey 必须被拒绝")


def test_send_serverchan_message_posts_to_sc3_endpoint_for_sctp_key():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "message": "发送成功"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_serverchan_message(
            "江苏文化采购公告\n1. 视频拍摄服务",
            "sctp1379tSC3SAMPLEKEY",
            title="命中提醒",
            client=client,
        )

    assert len(requests) == 1
    assert requests[0].url.host == "1379.push.ft07.com"
    assert requests[0].url.path == "/send/sctp1379tSC3SAMPLEKEY.send"
    assert parse_qs(requests[0].read().decode()) == {
        "title": ["命中提醒"],
        "desp": ["江苏文化采购公告\n1. 视频拍摄服务"],
    }
