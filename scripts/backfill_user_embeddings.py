from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import distinct, select

from core.database import AsyncSessionLocal
from models.party import Party
from models.quick_match.embedding import PartyMatchEmbedding
from models.user import User
from services.quick_match.embedding_service import EmbeddingService
from services.quick_match.quick_match_service import QuickMatchService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UserEmbeddingBackfill:
    """
    빠른매칭용 사용자 임베딩을 service_id 단위로 백필한다.

    QuickMatchService.create_request()에서 생성하는 PartyMatchEmbedding과 동일한
    프로필 구성/직렬화 로직을 사용한다.
    """

    def __init__(self) -> None:
        self.quick_match_service = QuickMatchService()

    async def sync_user_embedding(
        self,
        db,
        user_id: uuid.UUID,
        service_id: uuid.UUID,
        *,
        overwrite: bool = False,
    ) -> PartyMatchEmbedding | None:
        user = await db.get(User, user_id)
        if not user:
            logger.warning(
                "[UserEmbeddingBackfill] user not found user_id=%s service_id=%s",
                user_id,
                service_id,
            )
            return None

        existing_result = await db.execute(
            select(PartyMatchEmbedding).where(
                PartyMatchEmbedding.user_id == user_id,
                PartyMatchEmbedding.service_id == service_id,
            )
        )
        embedding = existing_result.scalar_one_or_none()

        ai_profile = await self.quick_match_service._build_user_ai_profile(
            db=db,
            user=user,
            service_id=service_id,
            preferred_conditions={},
        )

        if embedding and embedding.embedding_vector and not overwrite:
            if hasattr(embedding, "source_snapshot"):
                embedding.source_snapshot = ai_profile
            logger.info(
                "[UserEmbeddingBackfill] skipped existing user_id=%s service_id=%s",
                user_id,
                service_id,
            )
            await db.flush()
            return embedding

        embedding_text = EmbeddingService.serialize_user_profile_text(ai_profile)
        embedding_vector = await EmbeddingService.generate_embedding({"text": embedding_text})
        if not embedding_vector:
            logger.warning(
                "[UserEmbeddingBackfill] empty embedding generated user_id=%s service_id=%s",
                user_id,
                service_id,
            )
            return None

        if embedding:
            embedding.embedding_vector = embedding_vector
            if hasattr(embedding, "source_snapshot"):
                embedding.source_snapshot = ai_profile
            if hasattr(embedding, "last_generated_at"):
                embedding.last_generated_at = datetime.now(timezone.utc)
        else:
            embedding = PartyMatchEmbedding(
                user_id=user_id,
                service_id=service_id,
                embedding_vector=embedding_vector,
                source_snapshot=ai_profile,
                last_generated_at=datetime.now(timezone.utc),
            )
            db.add(embedding)

        await db.flush()
        logger.info(
            "[UserEmbeddingBackfill] sync done user_id=%s service_id=%s",
            user_id,
            service_id,
        )
        return embedding


async def backfill_user_embeddings(
    *,
    service_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    batch_size: int = 100,
    overwrite: bool = False,
    include_inactive_users: bool = False,
) -> None:
    sync_service = UserEmbeddingBackfill()

    async with AsyncSessionLocal() as db:
        if service_id:
            service_ids = [service_id]
        else:
            service_result = await db.execute(select(distinct(Party.service_id)))
            service_ids = [sid for sid in service_result.scalars().all() if sid]

        user_stmt = select(User.id)
        if user_id:
            user_stmt = user_stmt.where(User.id == user_id)
        elif not include_inactive_users and hasattr(User, "is_active"):
            user_stmt = user_stmt.where(User.is_active.is_(True))

        user_result = await db.execute(user_stmt)
        user_ids = user_result.scalars().all()

        total_targets = len(user_ids) * len(service_ids)
        logger.info(
            "[UserEmbeddingBackfill] total_users=%s total_services=%s total_targets=%s overwrite=%s",
            len(user_ids),
            len(service_ids),
            total_targets,
            overwrite,
        )

        processed = 0
        synced = 0
        for current_user_id in user_ids:
            for current_service_id in service_ids:
                try:
                    embedding = await sync_service.sync_user_embedding(
                        db=db,
                        user_id=current_user_id,
                        service_id=current_service_id,
                        overwrite=overwrite,
                    )
                    processed += 1
                    if embedding is not None:
                        synced += 1

                    if processed % batch_size == 0:
                        await db.commit()
                        logger.info(
                            "[UserEmbeddingBackfill] committed processed=%s synced=%s total_targets=%s",
                            processed,
                            synced,
                            total_targets,
                        )
                except Exception as exc:
                    await db.rollback()
                    logger.exception(
                        "[UserEmbeddingBackfill] failed user_id=%s service_id=%s error=%s",
                        current_user_id,
                        current_service_id,
                        exc,
                    )

        await db.commit()
        logger.info(
            "[UserEmbeddingBackfill] done processed=%s synced=%s total_targets=%s",
            processed,
            synced,
            total_targets,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill quick-match user embeddings.")
    parser.add_argument("--service-id", type=uuid.UUID, default=None, help="특정 service_id만 백필")
    parser.add_argument("--user-id", type=uuid.UUID, default=None, help="특정 user_id만 백필")
    parser.add_argument("--batch-size", type=int, default=100, help="커밋 배치 크기")
    parser.add_argument("--overwrite", action="store_true", help="기존 임베딩도 재생성")
    parser.add_argument(
        "--include-inactive-users",
        action="store_true",
        help="비활성 사용자까지 포함",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        backfill_user_embeddings(
            service_id=args.service_id,
            user_id=args.user_id,
            batch_size=args.batch_size,
            overwrite=args.overwrite,
            include_inactive_users=args.include_inactive_users,
        )
    )
