#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain
from time import monotonic
from typing import Any, TextIO

import httpx


CHAT_PATH = "/api/v1/chat/stream"


class SseProtocolError(ValueError):
    """Raised when the server returns malformed SSE data."""


@dataclass(frozen=True)
class SseEvent:
    name: str
    data: dict[str, Any]


@dataclass
class DisplayState:
    text_open: bool = False


def parse_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Parse SSE lines into JSON-object events."""
    event_name = "message"
    data_lines: list[str] = []
    for raw_line in chain(lines, ("",)):
        line = raw_line.rstrip("\r\n")
        if not line:
            if data_lines:
                raw_data = "\n".join(data_lines)
                try:
                    data = json.loads(raw_data)
                except json.JSONDecodeError as exc:
                    raise SseProtocolError(f"invalid JSON for {event_name}") from exc
                if not isinstance(data, dict):
                    raise SseProtocolError(f"non-object data for {event_name}")
                yield SseEvent(event_name, data)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)


def _format_price(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"¥{value:.2f}"
    return "-"


def _safe_text(value: object) -> str:
    return "".join(
        character
        for character in str(value)
        if character in {"\n", "\t"}
        or 32 <= ord(character) < 127
        or ord(character) > 159
    )


def _close_text(state: DisplayState, stdout: TextIO) -> None:
    if state.text_open:
        print(file=stdout, flush=True)
        state.text_open = False


def render_event(
    event: SseEvent,
    state: DisplayState,
    *,
    stdout: TextIO,
    stderr: TextIO,
    elapsed_seconds: float | None = None,
) -> None:
    """Render one protocol event without buffering text deltas."""
    data = event.data
    if event.name == "message_start":
        _close_text(state, stdout)
        print(
            f"[开始] request_id={_safe_text(data.get('request_id', '-'))} "
            f"conversation_id={_safe_text(data.get('conversation_id', '-'))}",
            file=stdout,
        )
    elif event.name == "product":
        _close_text(state, stdout)
        print(
            f"[商品 {_safe_text(data.get('rank', '-'))}] "
            f"{_safe_text(data.get('title', '-'))} | "
            f"{_safe_text(data.get('brand', '-'))} | "
            f"{_format_price(data.get('display_price'))}",
            file=stdout,
        )
        print(
            f"  product_id: {_safe_text(data.get('product_id', '-'))}",
            file=stdout,
        )
        matched_skus = data.get("matched_skus")
        if isinstance(matched_skus, list):
            for sku in matched_skus:
                if not isinstance(sku, Mapping):
                    continue
                properties = sku.get("properties")
                if isinstance(properties, Mapping):
                    property_text = ", ".join(
                        f"{_safe_text(key)}={_safe_text(value)}"
                        for key, value in sorted(
                            properties.items(), key=lambda item: str(item[0])
                        )
                    )
                else:
                    property_text = "-"
                print(
                    f"  SKU {_safe_text(sku.get('sku_id', '-'))}: {property_text} | "
                    f"{_format_price(sku.get('price'))}",
                    file=stdout,
                )
        if data.get("image_url"):
            print(f"  图片: {_safe_text(data['image_url'])}", file=stdout)
    elif event.name == "text_delta":
        if not state.text_open:
            print("助手> ", end="", file=stdout, flush=True)
            state.text_open = True
        print(
            _safe_text(data.get("delta", "")),
            end="",
            file=stdout,
            flush=True,
        )
    elif event.name == "error":
        _close_text(state, stdout)
        print(
            f"[错误] {_safe_text(data.get('code', '-'))}: "
            f"{_safe_text(data.get('message', '-'))} "
            f"(retryable={_safe_text(data.get('retryable', False))})",
            file=stderr,
        )
    elif event.name == "message_end":
        _close_text(state, stdout)
        elapsed = (
            f" elapsed={max(elapsed_seconds, 0.0):.3f}s"
            if elapsed_seconds is not None
            else ""
        )
        print(
            f"[结束] request_id={_safe_text(data.get('request_id', '-'))} "
            f"status={_safe_text(data.get('status', '-'))}{elapsed}",
            file=stdout,
        )
    else:
        _close_text(state, stdout)
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
        print(f"[事件 {_safe_text(event.name)}] {payload}", file=stdout)


def send_message(
    client: httpx.Client,
    *,
    base_url: str,
    conversation_id: str,
    message: str,
    stdout: TextIO,
    stderr: TextIO,
) -> bool:
    """Send one message and return whether the stream completed successfully."""
    endpoint = f"{base_url.rstrip('/')}{CHAT_PATH}"
    state = DisplayState()
    saw_end = False
    completed = False
    started_at = monotonic()

    def elapsed_text() -> str:
        return f"elapsed={max(monotonic() - started_at, 0.0):.3f}s"

    try:
        with client.stream(
            "POST",
            endpoint,
            json={"conversation_id": conversation_id, "message": message},
            headers={"Accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                raise SseProtocolError("expected a text/event-stream response")
            for event in parse_sse(response.iter_lines()):
                elapsed_seconds = (
                    monotonic() - started_at if event.name == "message_end" else None
                )
                render_event(
                    event,
                    state,
                    stdout=stdout,
                    stderr=stderr,
                    elapsed_seconds=elapsed_seconds,
                )
                if event.name == "message_end":
                    saw_end = True
                    completed = event.data.get("status") == "completed"
        if not saw_end:
            raise SseProtocolError("stream ended before message_end")
    except httpx.HTTPStatusError as exc:
        print(
            f"[客户端错误] HTTP {exc.response.status_code} {elapsed_text()}",
            file=stderr,
        )
        return False
    except httpx.RequestError as exc:
        print(f"[客户端错误] {exc} {elapsed_text()}", file=stderr)
        return False
    except SseProtocolError as exc:
        print(f"[客户端错误] {exc} {elapsed_text()}", file=stderr)
        return False
    return completed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="交互式调用 ShopAgent 文本购物 SSE 接口。"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--conversation-id")
    parser.add_argument("--message", help="发送单条消息后退出")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    conversation_id = args.conversation_id or str(uuid.uuid4())
    timeout = httpx.Timeout(120.0, connect=10.0)
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            if args.message is not None:
                message = args.message.strip()
                if not message:
                    print("[客户端错误] message 不能为空", file=sys.stderr)
                    return 2
                completed = send_message(
                    client,
                    base_url=args.base_url,
                    conversation_id=conversation_id,
                    message=message,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
                return 0 if completed else 1

            print(f"ShopAgent 测试客户端（conversation_id={conversation_id}）")
            print("当前服务为单轮对话；该 ID 只用于关联事件。输入 /quit 退出。")
            while True:
                try:
                    message = input("你> ").strip()
                except EOFError:
                    print()
                    return 0
                if message in {"/quit", "/exit"}:
                    return 0
                if not message:
                    continue
                send_message(
                    client,
                    base_url=args.base_url,
                    conversation_id=conversation_id,
                    message=message,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
    except KeyboardInterrupt:
        print("\n已退出", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
