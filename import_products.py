import asyncio
import csv
from db import AsyncSessionLocal, engine
from models import Base, Product

async def import_data():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        with open("products.csv", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                p = Product(
                    name=row["name"],
                    description=row["description"],
                    price=row["price"],
                    category=row["category"],
                    tags=row["tags"],
                    afl_link=row["afl_link"]
                )
                session.add(p)

        await session.commit()
        print("Imported OK")

if __name__ == "__main__":
    asyncio.run(import_data())
