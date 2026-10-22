import uuid

from fastapi import BackgroundTasks

from app.modules.sync.service import run_sync


def enqueue_sync(
    background_tasks: BackgroundTasks,
    account_id: uuid.UUID,
    *,
    full: bool = False,
) -> None:
    background_tasks.add_task(run_sync, account_id, full=full)
