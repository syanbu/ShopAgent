# Text Shopping Chat Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-file interactive Python client that exercises the real text-shopping HTTP/SSE endpoint and presents its streamed events readably.

**Architecture:** `scripts/chat_client.py` owns a small SSE parser, terminal renderer, HTTP request function, and CLI loop. Pure parsing/rendering functions remain independently testable, while the HTTP path uses `httpx.Client.stream` so text deltas appear as they arrive.

**Tech Stack:** Python 3.11+, standard library, httpx 0.28+, pytest 8.3+, Ruff, mypy

## Global Constraints

- Use `http://127.0.0.1:8000/api/v1/chat/stream` by default.
- Reuse one `conversation_id` per process without implying server-side memory.
- Never read or print `.env` or API keys.
- Keep the executable client in one Python script.
- Do not run any Git command without operation-specific user authorization.

---

### Task 1: Interactive HTTP/SSE client

**Files:**
- Create: `scripts/chat_client.py`
- Test: `tests/unit/test_chat_client.py`
- Modify: `docs/features/text-shopping-workflow.md`

**Interfaces:**
- Produces: `SseEvent(name: str, data: dict[str, Any])`
- Produces: `parse_sse(lines: Iterable[str]) -> Iterator[SseEvent]`
- Produces: `render_event(event: SseEvent, state: DisplayState, *, stdout: TextIO, stderr: TextIO) -> None`
- Produces: `send_message(client: httpx.Client, *, base_url: str, conversation_id: str, message: str, stdout: TextIO, stderr: TextIO) -> bool`
- Produces: `main(argv: Sequence[str] | None = None) -> int`

- [x] **Step 1: Write failing parser, renderer, and HTTP tests**

Create `tests/unit/test_chat_client.py` with deterministic SSE input and `httpx.MockTransport`. The required cases are: multi-line `data`, an incomplete final block, invalid JSON raising `SseProtocolError`, product/SKU/text/end rendering, the exact request URL and JSON body, HTTP 503 returning `False` with a concise error, and `--message` mapping a completed response to exit status 0. Use these concrete test shapes:

```python
def test_parse_sse_supports_multiple_events_and_final_block() -> None:
    lines = [
        "event: message_start",
        'data: {"request_id":"r1",',
        'data: "conversation_id":"c1"}',
        "",
        "event: text_delta",
        'data: {"delta":"你好"}',
    ]
    events = list(parse_sse(lines))
    assert events == [
        SseEvent("message_start", {"request_id": "r1", "conversation_id": "c1"}),
        SseEvent("text_delta", {"delta": "你好"}),
    ]


def test_send_message_posts_payload_and_streams_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://test/api/v1/chat/stream"
        assert json.loads(request.content) == {
            "conversation_id": "c1",
            "message": "推荐耳机",
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                "event: text_delta\n"
                'data: {"delta":"推荐结果"}\n\n'
                "event: message_end\n"
                'data: {"request_id":"r1","status":"completed"}\n\n'
            ),
        )

    stdout = StringIO()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert send_message(
            client,
            base_url="http://test/",
            conversation_id="c1",
            message="推荐耳机",
            stdout=stdout,
            stderr=StringIO(),
        )
    assert "助手> 推荐结果" in stdout.getvalue()
```

- [x] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest tests/unit/test_chat_client.py -q`

Expected: collection fails because `scripts.chat_client` does not exist.

- [x] **Step 3: Implement the SSE parser and terminal renderer**

In `scripts/chat_client.py`, add immutable event/state types, parse SSE fields until blank lines or EOF, combine repeated `data` fields with newlines, decode JSON objects, and raise `SseProtocolError` for malformed payloads. Render known events as follows:

```python
@dataclass(frozen=True)
class SseEvent:
    name: str
    data: dict[str, Any]


@dataclass
class DisplayState:
    text_started: bool = False


def parse_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    event_name = "message"
    data_lines: list[str] = []
    for line in chain(lines, [""]):
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
```

`render_event` must print message identifiers, ranked product title/brand/display price/SKUs/image URL, stream `text_delta` with flushing, print structured errors to stderr, print the final status, and display unknown events as `[事件 <name>] <JSON>` without failing. Prices use `¥{value:.2f}` and missing values use `-`.

- [x] **Step 4: Implement streaming HTTP and the CLI loop**

Build the endpoint with `base_url.rstrip("/") + "/api/v1/chat/stream"`. Send `conversation_id` and `message` as JSON with `Accept: text/event-stream`, use a 120-second request timeout, and consume `response.iter_lines()` without buffering the entire response.

`send_message` returns `True` only when a `message_end` event reports `completed`. It catches `httpx.HTTPError` and `SseProtocolError`, writes a concise `[客户端错误]` line to stderr, and returns `False`.

`main` uses these arguments:

```python
parser.add_argument("--base-url", default="http://127.0.0.1:8000")
parser.add_argument("--conversation-id", default=None)
parser.add_argument("--message")
```

Generate `str(uuid.uuid4())` when no conversation ID is supplied. In one-shot mode return `0` for a completed response and `1` otherwise. In interactive mode print the conversation ID, ignore blank input, exit on `/quit`, `/exit`, EOF, or Ctrl-C, and continue after an individual request failure.

- [x] **Step 5: Run focused tests and fix until GREEN**

Run: `uv run pytest tests/unit/test_chat_client.py -q`

Expected: all client tests pass.

- [x] **Step 6: Document the developer command**

Add this client usage to the local-running section of `docs/features/text-shopping-workflow.md`:

```markdown
服务启动后，可在另一个终端运行交互式测试客户端：

```bash
uv run python scripts/chat_client.py
```

也可发送单条消息：

```bash
uv run python scripts/chat_client.py --message "推荐一款降噪耳机"
```
```

State explicitly that the process reuses a conversation ID for correlation only and the current server remains single-turn.

- [x] **Step 7: Run static and regression verification**

Run:

```bash
uv run python scripts/chat_client.py --help
uv run ruff check scripts/chat_client.py tests/unit/test_chat_client.py
uv run ruff format --check scripts/chat_client.py tests/unit/test_chat_client.py
uv run mypy scripts/chat_client.py
uv run pytest -q -m "not live"
```

Expected: help exits 0, Ruff and mypy report success, and the non-live test suite passes.

- [x] **Step 8: Run a local smoke request when the API is available**

Run with proxy variables removed so loopback traffic stays local:

```bash
env -u ALL_PROXY -u all_proxy uv run python scripts/chat_client.py --message "推荐一款降噪耳机"
```

Expected: output contains `message_start`, zero or more product cards, streamed assistant text, and `message_end status=completed`. If the API process is not running, report that environment limitation separately from deterministic test results.

No Git commands are included because repository instructions require explicit authorization for each Git operation.
