from sqlalchemy import select
from db import AsyncSessionLocal
from models import Product, UserHistory

async def get_recommendations(user_id: str, limit: int = 5):

    async with AsyncSessionLocal() as session:

        # lấy lịch sử click
        hist = await session.execute(
            select(UserHistory).where(UserHistory.user_id == str(user_id))
        )

        history = hist.scalars().all()
        history_ids = [h.product_id for h in history]

        # USER MỚI
        if not history_ids:
            res = await session.execute(
                select(Product).order_by(Product.score.desc())
            )
            return res.scalars().all()[:limit]

        # lấy category user thích
        products = await session.execute(
            select(Product).where(Product.id.in_(history_ids))
        )

        clicked_products = products.scalars().all()
        categories = list(set([p.category for p in clicked_products]))

        # recommend theo category
        res = await session.execute(
            select(Product).where(Product.category.in_(categories))
        )

        recs = [p for p in res.scalars().all() if p.id not in history_ids]

        # fallback nếu thiếu
        if len(recs) < limit:
            allp = await session.execute(select(Product))
            for p in allp.scalars().all():
                if p.id not in history_ids and p not in recs:
                    recs.append(p)
                if len(recs) >= limit:
                    break

        return recs[:limit]
