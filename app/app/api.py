import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from opentelemetry import trace
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import Cache, get_cache
from app.database import Database, get_database, get_session
from app.models import Item
from app.schemas import ItemPage, ItemRead, ItemWrite

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
DatabaseDep = Annotated[Database, Depends(get_database)]
CacheDep = Annotated[Cache, Depends(get_cache)]


def tracer(request: Request) -> trace.Tracer:
    return request.app.state.tracer


@router.post("/items", response_model=ItemRead, status_code=201, tags=["items"])
async def create_item(
    payload: ItemWrite,
    request: Request,
    response: Response,
    session: SessionDep,
    database: DatabaseDep,
    cache: CacheDep,
) -> ItemRead:
    with tracer(request).start_as_current_span("items.create", record_exception=False):
        async with database.operation("create"), session.begin():
            item = Item(**payload.model_dump())
            session.add(item)
            await session.flush()
            await session.refresh(item)
            result = ItemRead.model_validate(item)
        await cache.invalidate(result.id)
        response.headers["Location"] = f"/api/v1/items/{result.id}"
        logger.info("item_created")
        return result


@router.get("/items", response_model=ItemPage, tags=["items"])
async def list_items(
    session: SessionDep,
    database: DatabaseDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> ItemPage:
    async with database.operation("list"):
        total = await session.scalar(select(func.count()).select_from(Item))
        items = (
            await session.scalars(select(Item).order_by(Item.created_at, Item.id).limit(limit).offset(offset))
        ).all()
        return ItemPage(items=[ItemRead.model_validate(i) for i in items], total=total or 0, limit=limit, offset=offset)


@router.get("/items/{item_id}", response_model=ItemRead, tags=["items"])
async def get_item(
    item_id: UUID, request: Request, session: SessionDep, database: DatabaseDep, cache: CacheDep
) -> ItemRead:
    with tracer(request).start_as_current_span("items.get", record_exception=False):
        cached = await cache.get_item(item_id)
        if cached is not None:
            return cached
        async with database.operation("get"):
            item = await session.get(Item, item_id)
            if item is None:
                raise HTTPException(404, "Item not found")
            result = ItemRead.model_validate(item)
        await cache.set_item(result)
        return result


@router.put("/items/{item_id}", response_model=ItemRead, tags=["items"])
async def update_item(
    item_id: UUID, payload: ItemWrite, request: Request, session: SessionDep, database: DatabaseDep, cache: CacheDep
) -> ItemRead:
    with tracer(request).start_as_current_span("items.update", record_exception=False):
        async with database.operation("update"), session.begin():
            item = await session.scalar(select(Item).where(Item.id == item_id).with_for_update())
            if item is None:
                raise HTTPException(404, "Item not found")
            for field, value in payload.model_dump().items():
                setattr(item, field, value)
            await session.flush()
            await session.refresh(item)
            result = ItemRead.model_validate(item)
        await cache.invalidate(item_id)
        logger.info("item_updated")
        return result


@router.delete("/items/{item_id}", status_code=204, tags=["items"])
async def delete_item(
    item_id: UUID, request: Request, session: SessionDep, database: DatabaseDep, cache: CacheDep
) -> Response:
    with tracer(request).start_as_current_span("items.delete", record_exception=False):
        async with database.operation("delete"), session.begin():
            item = await session.scalar(select(Item).where(Item.id == item_id).with_for_update())
            if item is None:
                raise HTTPException(404, "Item not found")
            await session.delete(item)
        await cache.invalidate(item_id)
        logger.info("item_deleted")
        return Response(status_code=204)


def cpu_work(iterations: int) -> int:
    result = 0
    for value in range(iterations):
        result = (result + value * value) % 1000000007
    return result


@router.get("/demo/work", tags=["diagnostics"])
async def demo_work(
    request: Request,
    iterations: Annotated[int, Query(ge=1000, le=1000000)] = 100000,
    delay_ms: Annotated[int, Query(ge=0, le=500)] = 0,
) -> dict[str, int]:
    if not request.app.state.settings.demo_enabled:
        raise HTTPException(404, "Not found")
    if request.app.state.demo_lock.locked():
        raise HTTPException(429, "Demo workload already running")
    async with request.app.state.demo_lock:
        with tracer(request).start_as_current_span("demo.cpu_work", record_exception=False):
            await asyncio.sleep(delay_ms / 1000)
            checksum = await asyncio.to_thread(cpu_work, iterations)
    return {"iterations": iterations, "checksum": checksum}
