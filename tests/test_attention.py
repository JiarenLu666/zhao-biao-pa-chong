from unittest.mock import patch

from tender_monitor.attention import (
    clear_attention,
    read_attention,
    send_desktop_notification,
    write_attention,
)


def test_attention_lifecycle(tmp_path):
    path = tmp_path / "attention.json"
    write_attention(
        path,
        kind="CAPTCHA_REQUIRED",
        message="验证码不能为空",
        source="ccgp_jiangsu",
        url="http://example.invalid/search",
        notify=False,
    )

    event = read_attention(path)
    assert event is not None
    assert event["kind"] == "CAPTCHA_REQUIRED"
    assert event["message"] == "验证码不能为空"

    clear_attention(path)
    assert read_attention(path) is None


def test_attention_deduplicates_open_event_and_notifies_once(tmp_path):
    path = tmp_path / "attention.json"
    with patch("tender_monitor.attention.send_desktop_notification") as notify:
        write_attention(path, kind="RATE_LIMIT", message="请稍后再试", source="okcis", url="https://example.invalid", notify=True)
        write_attention(path, kind="RATE_LIMIT", message="请稍后再试", source="okcis", url="https://example.invalid", notify=True)

    notify.assert_called_once()


def test_linux_notification_uses_native_notify_send_without_extra_dependency():
    with (
        patch("tender_monitor.attention.sys.platform", "linux"),
        patch("tender_monitor.attention.os.name", "posix"),
        patch("tender_monitor.attention.shutil.which", return_value="/usr/bin/notify-send"),
        patch("tender_monitor.attention.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        assert send_desktop_notification(kind="RATE_LIMIT", message="请稍后再试", source="okcis")

    run.assert_called_once_with(
        ["/usr/bin/notify-send", "采集被限流保护", "okcis：请稍后再试"],
        check=False,
        capture_output=True,
        timeout=5,
    )
