"""Repositório somente leitura para consultas no SQLite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from youshop_radar.config import DEFAULT_DB_PATH
from youshop_radar.data.database import open_readonly_connection
from youshop_radar.data.seed import ensure_seeded


class RadarRepository:
    """Encapsula todas as consultas SQL de leitura da plataforma."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = ensure_seeded(db_path)

    def _connect(self):
        return open_readonly_connection(self.db_path)

    def list_producer_ids_by_manager(self, manager_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM producers WHERE manager_id = ? ORDER BY id",
                (manager_id,),
            ).fetchall()
            return [row["id"] for row in rows]

    def get_producer(self, producer_ref: str) -> dict[str, Any] | None:
        cleaned = producer_ref.strip()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, manager_id, name, category, active_creators_count, seasonal_pattern
                FROM producers
                WHERE id = ? OR LOWER(name) = LOWER(?)
                """,
                (cleaned, cleaned),
            ).fetchone()
            return dict(row) if row else None
        
    def get_sales_summary(self, producer_id: str, start_iso: str, end_iso: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS orders_count, COALESCE(SUM(amount_brl), 0.0) AS revenue_brl
                FROM orders
                WHERE producer_id = ? AND status = 'paid' AND created_at BETWEEN ? AND ?
                """,
                (producer_id, start_iso, end_iso),
            ).fetchone()
            return {
                "orders_count": int(row["orders_count"]),
                "revenue_brl": float(row["revenue_brl"]),
            }

    def get_webhook_summary(self, producer_id: str, start_iso: str, end_iso: str) -> dict[str, Any]:
        with self._connect() as conn:
            stats = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS failed
                FROM webhook_events
                WHERE producer_id = ? AND created_at BETWEEN ? AND ?
                """,
                (producer_id, start_iso, end_iso),
            ).fetchone()

            top_err = conn.execute(
                """
                SELECT error_category, COUNT(*) AS qty
                FROM webhook_events
                WHERE producer_id = ? AND status_code >= 400 AND created_at BETWEEN ? AND ?
                GROUP BY error_category
                ORDER BY qty DESC
                LIMIT 1
                """,
                (producer_id, start_iso, end_iso),
            ).fetchone()

            return {
                "total": int(stats["total"] or 0),
                "failed": int(stats["failed"] or 0),
                "top_error": top_err["error_category"] if top_err else "nenhum",
            }

    def get_open_tickets_by_category(
        self, producer_id: str, start_iso: str, end_iso: str
    ) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS qty
                FROM tickets
                WHERE producer_id = ? AND status = 'open' AND created_at BETWEEN ? AND ?
                GROUP BY category
                """,
                (producer_id, start_iso, end_iso),
            ).fetchall()
            return {row["category"]: int(row["qty"]) for row in rows}

    def count_tickets_opened(self, producer_id: str, start_iso: str, end_iso: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS qty
                FROM tickets
                WHERE producer_id = ? AND created_at BETWEEN ? AND ?
                """,
                (producer_id, start_iso, end_iso),
            ).fetchone()
            return int(row["qty"] or 0)