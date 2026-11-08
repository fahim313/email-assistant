import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.exceptions.error import NotFoundError
from app.db.session import SessionDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.sending import repository
from app.modules.sending.models import EmailSend


async def get_owned_send(
    send_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> EmailSend:
    send = await repository.get_owned_send(
        session,
        user.id,
        send_id,
    )

    if send is None:
        raise NotFoundError("send", str(send_id))

    return send


OwnedSend = Annotated[EmailSend, Depends(get_owned_send)]


def get_idempotency_key(
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
        ),
    ],
) -> str:
    key = idempotency_key.strip()

    if not key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key cannot be empty.",
        )

    return key


IdempotencyKey = Annotated[str, Depends(get_idempotency_key)]