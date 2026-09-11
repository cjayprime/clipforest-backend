"""In-process concurrency gates.

`heavy_cpu` serializes CPU-heavy work (FFmpeg encodes, face analysis, local
Whisper) inside one worker process so a local transcription never overlaps a
render on the shared 8 GB host (PRD §17.2). Cross-host isolation comes from
running render-only workers (WORKER_QUEUES=render) on separate machines.
"""

from __future__ import annotations

import asyncio

heavy_cpu = asyncio.Semaphore(1)
