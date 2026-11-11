"""SSE 事件（/v1/events）：共享区变更跨设备即时感知（每订阅者独立广播）。"""
import asyncio
import json
import queue
import threading
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..auth import get_current_device
from ..db import now_iso

router = APIRouter()

_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()


def push_event(event_type: str, scope: str, scope_ref: str, memory_id: int,
               device_id: int | None, detail: str = "") -> None:
    """写路径调用：共享区（user/project）变更广播给所有已连接订阅者。

    每个订阅者有独立队列，事件复制到所有队列（广播而非竞争消费）。
    """
    if scope not in ("user", "project"):
        return
    event = {
        "type": event_type, "scope": scope, "scope_ref": scope_ref,
        "memory_id": memory_id, "device_id": device_id, "detail": detail,
        "time": now_iso(),
    }
    with _subscribers_lock:
        for q in _subscribers:
            q.put(event)


@router.get("/v1/events", tags=["events"])
async def events(device: dict = Depends(get_current_device)):
    """SSE 流：连接后持续推送共享区变更；15s 心跳保活。"""
    q: queue.Queue = queue.Queue()
    with _subscribers_lock:
        _subscribers.append(q)

    async def gen():
        try:
            while True:
                try:
                    evt = await asyncio.to_thread(q.get, timeout=15)
                    # 过滤：自己写的不推（客户端已经知道），推其他设备的变更
                    if evt.get("device_id") == device["id"]:
                        continue
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield f": ping {int(time.time())}\n\n"
        finally:
            with _subscribers_lock:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})