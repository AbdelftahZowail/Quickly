import asyncio
from app.database import AsyncSessionLocal
from app.routers.office365_webhook import ensure_subscription
from fastapi import HTTPException

async def run():
    async with AsyncSessionLocal() as db:
        try:
            await ensure_subscription(db, 999999)
        except HTTPException as e:
            print('got HTTPException as expected', e.status_code, e.detail)

if __name__ == '__main__':
    asyncio.run(run())
