import uuid
from typing import Annotated

from fastapi import Depends

from app.core.exceptions.error import NotFoundError
from app.db.session import SessionDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.drafts import repository
from app.modules.drafts.models import ReplyDraft


async def get_owned_draft(
    draft_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> ReplyDraft:
    draft = await repository.get_owned_draft(
        session,
        user.id,
        draft_id,
    )

    if draft is None:
        raise NotFoundError("draft", str(draft_id))

    return draft


OwnedDraft = Annotated[
    ReplyDraft,
    Depends(get_owned_draft),
]