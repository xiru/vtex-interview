"""Near-duplicate suggestions using PostgreSQL ``pg_trgm`` on ``catalog.name_fold(name)``.

Exact duplicates cannot exist (unique key indexes). This finds names that merely *look* alike
(``Hugo`` / ``Hugo Boss``, typos, extra words) so an operator can decide whether to merge.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text

ENTITIES = ("product", "brand", "category")


@dataclass(frozen=True)
class Candidate:
    score: float
    keep_id: int
    keep_name: str
    duplicate_id: int
    duplicate_name: str
    keep_brand: str | None = None
    duplicate_brand: str | None = None
    keep_offers: int = 0
    duplicate_offers: int = 0


def find_candidates(
    conn: Connection, entity: str, threshold: float = 0.5, limit: int = 50
) -> list[Candidate]:
    if entity not in ENTITIES:
        raise ValueError(f"entity must be one of {ENTITIES}")
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    conn.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"), {"t": str(threshold)}
    )
    conn.execute(
        text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
        {"t": str(threshold)},
    )
    if entity == "product":
        same_group = "(a.brand_id = b.brand_id OR a.brand_id IS NULL OR b.brand_id IS NULL)"
        extra_cols = (
            ", ba.name AS a_brand, bb.name AS b_brand,"
            " (SELECT count(*) FROM catalog.seller_product o WHERE o.product_id = a.id"
            "  AND o.active) AS a_offers,"
            " (SELECT count(*) FROM catalog.seller_product o WHERE o.product_id = b.id"
            "  AND o.active) AS b_offers"
        )
        joins = (
            " LEFT JOIN catalog.brand ba ON ba.id = a.brand_id"
            " LEFT JOIN catalog.brand bb ON bb.id = b.brand_id"
        )
    else:
        same_group = "true"
        extra_cols = ", NULL AS a_brand, NULL AS b_brand, 0 AS a_offers, 0 AS b_offers"
        joins = ""
    sql = f"""
        WITH pairs AS (
            SELECT a.id AS a_id, a.name AS a_name, b.id AS b_id, b.name AS b_name,
                   greatest(
                       public.similarity(catalog.name_fold(a.name),
                                         catalog.name_fold(b.name)),
                       public.word_similarity(catalog.name_fold(a.name),
                                              catalog.name_fold(b.name)),
                       public.word_similarity(catalog.name_fold(b.name),
                                              catalog.name_fold(a.name))
                   ) AS score
                   {extra_cols}
            FROM catalog.{entity} a
            JOIN catalog.{entity} b
              ON a.id < b.id
             AND (catalog.name_fold(a.name) OPERATOR(public.%) catalog.name_fold(b.name)
                  OR catalog.name_fold(a.name) OPERATOR(public.<%) catalog.name_fold(b.name)
                  OR catalog.name_fold(b.name) OPERATOR(public.<%) catalog.name_fold(a.name))
            {joins}
            WHERE a.active AND b.active AND {same_group}
        )
        SELECT * FROM pairs WHERE score >= :threshold
        ORDER BY score DESC, a_id, b_id
        LIMIT :limit
    """
    rows = conn.execute(text(sql), {"threshold": threshold, "limit": limit}).all()
    return [
        Candidate(
            score=round(float(r.score), 3),
            keep_id=r.a_id,
            keep_name=r.a_name,
            duplicate_id=r.b_id,
            duplicate_name=r.b_name,
            keep_brand=r.a_brand,
            duplicate_brand=r.b_brand,
            keep_offers=int(r.a_offers),
            duplicate_offers=int(r.b_offers),
        )
        for r in rows
    ]


def merge_command(entity: str, candidate: Candidate) -> str:
    return (
        f"catalog merge {entity} --keep {candidate.keep_id} --duplicate {candidate.duplicate_id}"
    )
