import json
from io import StringIO
from typing import Any

import httpx
import pytest

import scripts.chat_client as chat_client
from scripts.chat_client import (
    DisplayState,
    SseEvent,
    SseProtocolError,
    parse_sse,
    render_event,
    send_message,
)


def test_parse_sse_supports_multiple_events_and_final_block() -> None:
    lines = [
        "event: message_start",
        'data: {"request_id":"r1",',
        'data: "conversation_id":"c1"}',
        "",
        ": keep-alive",
        "event: text_delta",
        'data: {"delta":"你好"}',
    ]

    events = list(parse_sse(lines))

    assert events == [
        SseEvent("message_start", {"request_id": "r1", "conversation_id": "c1"}),
        SseEvent("text_delta", {"delta": "你好"}),
    ]


def test_parse_sse_rejects_invalid_json() -> None:
    with pytest.raises(SseProtocolError, match="invalid JSON for product"):
        list(parse_sse(["event: product", "data: not-json", ""]))


def test_render_event_displays_product_text_and_end() -> None:
    stdout = StringIO()
    stderr = StringIO()
    state = DisplayState()
    events = [
        SseEvent(
            "message_start",
            {"request_id": "r1", "conversation_id": "c1"},
        ),
        SseEvent(
            "product",
            {
                "rank": 1,
                "product_id": "p1",
                "title": "测试耳机",
                "brand": "测试品牌",
                "display_price": 399.0,
                "matched_skus": [
                    {
                        "sku_id": "sku1",
                        "properties": {"color": "黑色"},
                        "price": 399.0,
                    }
                ],
                "image_url": "http://test/image",
            },
        ),
        SseEvent("text_delta", {"delta": "推荐"}),
        SseEvent("text_delta", {"delta": "结果"}),
        SseEvent(
            "message_end",
            {"request_id": "r1", "status": "completed"},
        ),
    ]

    for event in events:
        render_event(event, state, stdout=stdout, stderr=stderr)

    output = stdout.getvalue()
    assert "[开始] request_id=r1 conversation_id=c1" in output
    assert "[商品 1] 测试耳机 | 测试品牌 | ¥399.00" in output
    assert "SKU sku1: color=黑色 | ¥399.00" in output
    assert "http://test/image" in output
    assert "助手> 推荐结果" in output
    assert "[结束] request_id=r1 status=completed" in output
    assert stderr.getvalue() == ""


def test_render_event_reports_errors_and_unknown_events() -> None:
    stdout = StringIO()
    stderr = StringIO()
    state = DisplayState()

    render_event(
        SseEvent(
            "error",
            {"code": "GENERATION_FAILED", "message": "失败", "retryable": True},
        ),
        state,
        stdout=stdout,
        stderr=stderr,
    )
    render_event(
        SseEvent("diagnostic", {"value": "测试"}),
        state,
        stdout=stdout,
        stderr=stderr,
    )

    assert "[错误] GENERATION_FAILED: 失败 (retryable=True)" in stderr.getvalue()
    assert '[事件 diagnostic] {"value": "测试"}' in stdout.getvalue()


def test_render_event_strips_terminal_control_characters() -> None:
    stdout = StringIO()

    render_event(
        SseEvent("text_delta", {"delta": "安全\x1b]52;c;secret\x07文本"}),
        DisplayState(),
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "安全]52;c;secret文本" in output


def test_send_message_posts_payload_and_streams_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://test/api/v1/chat/stream"
        assert request.headers["accept"] == "text/event-stream"
        assert json.loads(request.content) == {
            "conversation_id": "c1",
            "message": "推荐耳机",
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            text=(
                "event: text_delta\n"
                'data: {"delta":"推荐结果"}\n\n'
                "event: message_end\n"
                'data: {"request_id":"r1","status":"completed"}\n\n'
            ),
        )

    clock_values = iter((10.0, 12.5))
    monkeypatch.setattr(
        chat_client,
        "monotonic",
        lambda: next(clock_values),
        raising=False,
    )
    stdout = StringIO()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        completed = send_message(
            client,
            base_url="http://test/",
            conversation_id="c1",
            message="推荐耳机",
            stdout=stdout,
            stderr=StringIO(),
        )

    assert completed
    assert "助手> 推荐结果" in stdout.getvalue()
    assert "[结束] request_id=r1 status=completed elapsed=2.500s" in stdout.getvalue()


def test_send_message_reports_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    clock_values = iter((20.0, 20.125))
    monkeypatch.setattr(
        chat_client,
        "monotonic",
        lambda: next(clock_values),
        raising=False,
    )
    stderr = StringIO()

    with httpx.Client(transport=transport) as client:
        completed = send_message(
            client,
            base_url="http://test",
            conversation_id="c1",
            message="推荐耳机",
            stdout=StringIO(),
            stderr=stderr,
        )

    assert not completed
    assert "[客户端错误] HTTP 503 elapsed=0.125s" in stderr.getvalue()


def test_send_message_rejects_stream_without_end_event() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='event: text_delta\ndata: {"delta":"未完成"}\n\n',
        )
    )
    stderr = StringIO()

    with httpx.Client(transport=transport) as client:
        completed = send_message(
            client,
            base_url="http://test",
            conversation_id="c1",
            message="推荐耳机",
            stdout=StringIO(),
            stderr=stderr,
        )

    assert not completed
    assert "stream ended before message_end" in stderr.getvalue()


@pytest.mark.parametrize(("completed", "expected_status"), [(True, 0), (False, 1)])
def test_main_one_shot_maps_completion_to_exit_status(
    monkeypatch: pytest.MonkeyPatch,
    completed: bool,
    expected_status: int,
) -> None:
    captured: dict[str, Any] = {}

    def fake_send_message(
        client: httpx.Client,
        *,
        base_url: str,
        conversation_id: str,
        message: str,
        stdout: Any,
        stderr: Any,
    ) -> bool:
        captured.update(
            base_url=base_url,
            conversation_id=conversation_id,
            message=message,
        )
        return completed

    monkeypatch.setattr(chat_client, "send_message", fake_send_message)

    result = chat_client.main(
        [
            "--base-url",
            "http://example",
            "--conversation-id",
            "c1",
            "--message",
            " 推荐耳机 ",
        ]
    )

    assert result == expected_status
    assert captured == {
        "base_url": "http://example",
        "conversation_id": "c1",
        "message": "推荐耳机",
    }
