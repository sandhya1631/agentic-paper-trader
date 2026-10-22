from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "agentic_paper_trader",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
)

celery_app.autodiscover_tasks(["app.agent"])
