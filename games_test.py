import asyncio

from nsecpy import regions


async def doThing():
    print(await regions['en_US'].getStatus())


asyncio.run(doThing)
