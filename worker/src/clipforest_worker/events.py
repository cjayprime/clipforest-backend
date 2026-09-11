"""Realtime progress events. Advisory only: PostgreSQL remains authoritative (PRD §15)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis

from .log import get_logger

log = get_logger(__name__)


class EventPublisher:
    def __init__(self, redis_url: str, channel: str):
        self.channel = channel
        self.redis = aioredis.from_url(redis_url)

    async def publish(self, event: dict) -> None:
        payload = {**event, "at": datetime.now(timezone.utc).isoformat()}
        try:
            await self.redis.publish(self.channel, json.dumps(payload, default=str))
        except Exception as exc:  # noqa: BLE001 - progress events are best effort
            log.warning("Failed to publish progress event", extra={"error": str(exc)})

    async def video(self, video: dict) -> None:
        await self.publish(
            {
                "type": "video.updated",
                "userId": str(video["user_id"]),
                "videoId": str(video["id"]),
                "status": video["status"],
                "progress": video.get("progress"),
                "stage": video.get("stage"),
                "substage": video.get("substage"),
                "errorCode": video.get("error_code"),
            }
        )

    async def render(self, render: dict) -> None:
        await self.publish(
            {
                "type": "render.updated",
                "userId": str(render["user_id"]),
                "videoId": str(render["video_id"]),
                "renderId": str(render["id"]),
                "status": render["status"],
                "progress": render.get("progress"),
                "stage": render.get("stage"),
                "substage": render.get("substage"),
                "errorCode": render.get("error_code"),
            }
        )

    async def close(self) -> None:
        await self.redis.aclose()
