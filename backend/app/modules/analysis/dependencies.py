import uuid
from typing import Annotated

from fastapi import Depends

from app.core.exceptions.error import NotFoundError
from app.db.session import SessionDep
from app.modules.accounts.models import ConnectedAccount
from app.modules.analysis import repository
from app.modules.analysis.models import ActionItem
from app.modules.auth.dependencies import CurrentUser
from app.modules.threads.models import Thread


async def get_owned_thread(
    thread_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> Thread:
    thread = await repository.get_owned_thread(
        session,
        user.id,
        thread_id,
    )

    if thread is None:
        raise NotFoundError("thread", str(thread_id))

    return thread


async def get_owned_account(
    account_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> ConnectedAccount:
    account = await repository.get_owned_account(
        session,
        user.id,
        account_id,
    )

    if account is None:
        raise NotFoundError("account", str(account_id))

    return account


async def get_owned_action_item(
    item_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> ActionItem:
    item = await repository.get_owned_action_item(
        session,
        user.id,
        item_id,
    )

    if item is None:
        raise NotFoundError("action_item", str(item_id))

    return item


OwnedThread = Annotated[Thread, Depends(get_owned_thread)]
OwnedAccount = Annotated[ConnectedAccount, Depends(get_owned_account)]
OwnedActionItem = Annotated[ActionItem, Depends(get_owned_action_item)]