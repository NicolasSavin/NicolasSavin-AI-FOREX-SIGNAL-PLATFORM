from __future__ import annotations

import json
import logging
import shutil
from app.services.storage_paths import atomic_write_json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

RETRY_DELAYS_SECONDS = [5 * 60, 30 * 60, 2 * 60 * 60, 12 * 60 * 60]


class MediaAutomationService:
    """Autonomous FXPilot TV scheduler and import/analyze/publish pipeline."""

    def __init__(self, *, engine_factory: Callable[[], Any], catalog_loader: Callable[[], list[dict[str, Any]]], pipeline_runner: Callable[[dict[str, Any]], dict[str, Any]], state_path: Path, tv_catalog_path: Path, data_dir: Path) -> None:
        self.engine_factory = engine_factory
        self.catalog_loader = catalog_loader
        self.pipeline_runner = pipeline_runner
        self.state_path = state_path
        self.tv_catalog_path = tv_catalog_path
        self.data_dir = data_dir
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._running = False
        self._last_error: str | None = None

    def start(self) -> None:
        if self.scheduler.running:
            return
        self.scheduler.add_job(lambda: self.run_import_cycle("youtube"), IntervalTrigger(minutes=15), id="media_youtube_15m", replace_existing=True, max_instances=1, coalesce=True)
        self.scheduler.add_job(lambda: self.run_import_cycle("telegram"), IntervalTrigger(minutes=10), id="media_telegram_10m", replace_existing=True, max_instances=1, coalesce=True)
        self.scheduler.add_job(lambda: self.run_import_cycle("rss"), IntervalTrigger(minutes=30), id="media_rss_30m", replace_existing=True, max_instances=1, coalesce=True)
        self.scheduler.add_job(self.run_nightly_maintenance, CronTrigger(hour=2, minute=15), id="media_nightly_maintenance", replace_existing=True, max_instances=1, coalesce=True)
        self.scheduler.start()
        self._write_state_event("scheduler_started", {"status": "running"})
        logger.info("media_automation_scheduler_started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self._write_state_event("scheduler_stopped", {"status": "stopped"})

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        jobs = []
        for job in self.scheduler.get_jobs() if self.scheduler.running else []:
            jobs.append({"id": job.id, "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None})
        return {
            "enabled": self.scheduler.running,
            "status": "running" if self.scheduler.running else "ready_for_future_cron",
            "scheduler_status": "running" if self.scheduler.running else "stopped",
            "jobs": jobs,
            "last_error": self._last_error,
            "state": state,
            "notifications": state.get("notifications", [])[-20:],
            "retry_policy_minutes": [5, 30, 120, 720],
        }

    def run_import_cycle(self, source_type: str | None = None) -> dict[str, Any]:
        if self._running:
            return {"success": False, "skipped": True, "reason": "automation_already_running"}
        self._running = True
        started = time.monotonic()
        try:
            engine = self.engine_factory()
            result = engine.import_latest(source_types={source_type} if source_type else None)
            new_items = self._new_imported_items(result)
            pipeline = [self.pipeline_runner(item) for item in new_items]
            self._publish_to_tv()
            payload = {**result, "source_type": source_type or "all", "pipeline_processed": len(pipeline), "duration_seconds": round(time.monotonic() - started, 3)}
            self._write_state_event("import_cycle_finished", payload)
            return payload
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("media_automation_import_cycle_failed source_type=%s", source_type)
            self._write_state_event("import_cycle_failed", {"source_type": source_type, "error": str(exc)})
            return {"success": False, "source_type": source_type, "error": str(exc)}
        finally:
            self._running = False

    def run_nightly_maintenance(self) -> dict[str, Any]:
        items = self.catalog_loader()
        rebuilt = {"authors": len({str(i.get("author") or i.get("source_id") or "") for i in items}), "consensus_items": len(items), "performance_items": len(items)}
        removed = self._cleanup_cache()
        self._publish_to_tv()
        payload = {"success": True, "rebuilt": rebuilt, "cleanup_removed": removed}
        self._write_state_event("nightly_maintenance_finished", payload)
        return payload

    def _new_imported_items(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        new_count = int(result.get("new_items") or result.get("imported") or 0)
        if new_count <= 0:
            return []
        return self.catalog_loader()[:new_count]

    def _publish_to_tv(self) -> None:
        self.tv_catalog_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.tv_catalog_path, self.catalog_loader())

    def _cleanup_cache(self) -> int:
        removed = 0
        for name in ("transcript_cache", "download_cache", "thumbnail_cache", ".cache"):
            path = self.data_dir / name
            if path.exists() and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        return removed

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_state_event(self, event: str, payload: dict[str, Any]) -> None:
        state = self._read_state()
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        state["last_event"] = event
        state["last_payload"] = payload
        notifications = state.setdefault("notifications", [])
        if event.endswith("failed") or event in {"scheduler_stopped"} or "quota" in str(payload).lower():
            notifications.append({"event": event, "message_ru": self._notification_ru(event, payload), "created_at": state["updated_at"], "payload": payload})
        state["notifications"] = notifications[-50:]
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.state_path, state)

    @staticmethod
    def _notification_ru(event: str, payload: dict[str, Any]) -> str:
        text = str(payload.get("error") or payload.get("last_error") or payload)
        if "quota" in text.lower():
            return "Квота YouTube исчерпана — включён автоматический fallback yt-dlp."
        if "telegram" in text.lower():
            return "Telegram временно недоступен или канал не отвечает."
        if "rss" in text.lower():
            return "RSS источник сломан или недоступен."
        if event == "scheduler_stopped":
            return "Планировщик остановлен."
        return "Источник или автоматический импорт завершился с ошибкой."
