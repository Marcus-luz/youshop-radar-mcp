"""Repositório estritamente somente leitura para consultas no SQLite."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from youshop_radar.config import get_db_path
from youshop_radar.data.database import open_readonly_connection


class RadarRepository:
    """Encapsula todas as consultas SQL de leitura da plataforma."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_db_path()

    def _connect(self):
        return open_readonly_connection(self.db_path)

    def list_producer_ids_by_manager(self, manager_id: str) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id FROM producers WHERE manager_id = ? ORDER BY id",
                (manager_id,),
            ).fetchall()
            return [row["id"] for row in rows]

    def get_producer(self, producer_ref: str) -> dict[str, Any] | None:
        cleaned = producer_ref.strip()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT id, manager_id, name, category, active_creators_count, seasonal_pattern
                FROM producers
                WHERE id = ? OR LOWER(name) = LOWER(?)
                ORDER BY id
                LIMIT 1
                """,
                (cleaned, cleaned),
            ).fetchone()
            return dict(row) if row else None

    def get_producer_for_manager(
        self, manager_id: str, producer_ref: str
    ) -> dict[str, Any] | None:
        """Busca produtor já restrito à carteira da sessão (suporta nomes homônimos)."""
        cleaned = producer_ref.strip()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT id, manager_id, name, category, active_creators_count, seasonal_pattern
                FROM producers
                WHERE manager_id = ? AND (id = ? OR LOWER(name) = LOWER(?))
                ORDER BY id
                LIMIT 1
                """,
                (manager_id, cleaned, cleaned),
            ).fetchone()
            return dict(row) if row else None

    def get_sales_summary(
        self, producer_id: str, start_iso: str, end_exclusive_iso: str
    ) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS orders_count, COALESCE(SUM(amount_brl), 0.0) AS revenue_brl
                FROM orders
                WHERE producer_id = ?
                  AND status = 'paid'
                  AND created_at >= ?
                  AND created_at < ?
                """,
                (producer_id, start_iso, end_exclusive_iso),
            ).fetchone()
            return {
                "orders_count": int(row["orders_count"]),
                "revenue_brl": float(row["revenue_brl"]),
            }

    def get_webhook_summary(
        self, producer_id: str, start_iso: str, end_exclusive_iso: str
    ) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            stats = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS failed
                FROM webhook_events
                WHERE producer_id = ?
                  AND created_at >= ?
                  AND created_at < ?
                """,
                (producer_id, start_iso, end_exclusive_iso),
            ).fetchone()

            err_rows = conn.execute(
                """
                SELECT error_category, COUNT(*) AS qty
                FROM webhook_events
                WHERE producer_id = ?
                  AND status_code >= 400
                  AND created_at >= ?
                  AND created_at < ?
                GROUP BY error_category
                ORDER BY qty DESC, error_category ASC
                """,
                (producer_id, start_iso, end_exclusive_iso),
            ).fetchall()

            errors_by_category = {row["error_category"]: int(row["qty"]) for row in err_rows}
            top_error = err_rows[0]["error_category"] if err_rows else "nenhum"

            return {
                "total": int(stats["total"] or 0),
                "failed": int(stats["failed"] or 0),
                "top_error": top_error,
                "errors_by_category": errors_by_category,
            }

    def get_open_tickets_by_category(
        self, producer_id: str, up_to_exclusive_iso: str
    ) -> dict[str, int]:
        """Retorna todos os tickets ainda abertos (de qualquer idade) até a data de corte."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS qty
                FROM tickets
                WHERE producer_id = ?
                  AND status = 'open'
                  AND created_at < ?
                GROUP BY category
                ORDER BY category ASC
                """,
                (producer_id, up_to_exclusive_iso),
            ).fetchall()
            return {row["category"]: int(row["qty"]) for row in rows}

    def count_tickets_opened(
        self, producer_id: str, start_iso: str, end_exclusive_iso: str
    ) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS qty
                FROM tickets
                WHERE producer_id = ?
                  AND created_at >= ?
                  AND created_at < ?
                """,
                (producer_id, start_iso, end_exclusive_iso),
            ).fetchone()
            return int(row["qty"] or 0)