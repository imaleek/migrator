import asyncio
import httpx

async def test():
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        try:
            r = await c.get('https://zoneswitch.azurecr.io/v2/')
            print(f'Status: {r.status_code}')
            print(f'Headers: {dict(r.headers)}')
        except Exception as e:
            print(f'Error: {type(e).__name__}: {e}')

asyncio.run(test())
