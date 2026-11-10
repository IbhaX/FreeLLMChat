"""/v1/person — person card lookup for the chat UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_gateway_key
from app.services.person import lookup_person

router = APIRouter(prefix="/v1/person", dependencies=[Depends(require_gateway_key)])


@router.get("/lookup")
async def person_lookup(name: str = Query(..., min_length=3, max_length=80)):
    person = await lookup_person(name)
    return {"found": person is not None, "person": person}
