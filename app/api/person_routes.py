"""/v1/person — person card lookup for the chat UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_user
from app.services.person import lookup_people, lookup_person

router = APIRouter(
    prefix="/v1/person",
    dependencies=[Depends(require_user)])


@router.get("/lookup")
async def person_lookup(name: str = Query(..., min_length=3, max_length=80)):
    person = await lookup_person(name)
    return {"found": person is not None, "person": person}


@router.get("/search")
async def people_search(name: str = Query(..., min_length=3, max_length=80)):
    people = await lookup_people(name)
    return {"found": bool(people), "people": people}
