"""Gerador determinístico de dados simulados (Faker pt_BR com seed fixa)."""

from __future__ import annotations

import random
import sqlite3
from datetime import timedelta
from pathlib import Path

from faker import Faker

from youshop_radar.config import DEFAULT_DB_PATH, REFERENCE_DATE, SEED
from youshop_radar.data.database import SCHEMA_SQL, open_write_connection


def _insert_orders(
    conn: sqlite3.Connection,
    faker: Faker,
    producer_id: str,
    baseline_daily: int,
    recent_daily: int,
    ticket_value: float,
) -> None:
    rows: list[tuple[str, str, float, str, str]] = []
    for days_ago in range(27, -1, -1):
        day_iso = (REFERENCE_DATE - timedelta(days=days_ago)).isoformat()
        count = recent_daily if days_ago < 7 else baseline_daily
        for _ in range(count):
            amount = round(ticket_value * random.uniform(0.92, 1.08), 2)
            rows.append((producer_id, day_iso, amount, "paid", faker.email()))
    conn.executemany(
        "INSERT INTO orders (producer_id, created_at, amount_brl, status, buyer_email) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def _insert_webhooks(
    conn: sqlite3.Connection,
    producer_id: str,
    daily_total: int,
    daily_fail: int,
    error_category: str,
    fail_status: int = 401,
    fail_days: int = 5,
) -> None:
    rows: list[tuple[str, str, int, str]] = []
    for days_ago in range(27, -1, -1):
        day_iso = (REFERENCE_DATE - timedelta(days=days_ago)).isoformat()
        fails = daily_fail if days_ago < fail_days else 0
        oks = max(0, daily_total - fails)
        rows.extend((producer_id, day_iso, 200, "ok") for _ in range(oks))
        rows.extend((producer_id, day_iso, fail_status, error_category) for _ in range(fails))
    conn.executemany(
        "INSERT INTO webhook_events (producer_id, created_at, status_code, error_category) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def _insert_tickets(
    conn: sqlite3.Connection,
    producer_id: str,
    items: list[tuple[int, str, str]],
) -> None:
    rows = [
        (producer_id, (REFERENCE_DATE - timedelta(days=days_ago)).isoformat(), category, status)
        for days_ago, category, status in items
    ]
    conn.executemany(
        "INSERT INTO tickets (producer_id, created_at, category, status) VALUES (?, ?, ?, ?)",
        rows,
    )


def seed_database(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Recria e popula o banco SQLite com os cenários planejados."""
    Faker.seed(SEED)
    random.seed(SEED)
    faker = Faker("pt_BR")

    path = Path(db_path)
    conn = open_write_connection(path)
    try:
        conn.executescript(SCHEMA_SQL)

        managers = [
            ("mgr_ana", "Ana Silva", "ana.silva@youshop.example"),
            ("mgr_carlos", "Carlos Mendes", "carlos.mendes@youshop.example"),
        ]
        conn.executemany("INSERT INTO managers (id, name, email) VALUES (?, ?, ?)", managers)

        producers = [
            # Carteira da Ana (6 produtores)
            ("prod_aurora", "mgr_ana", "Aurora Cosméticos", "cosméticos", 12, 0),
            ("prod_nutrimax", "mgr_ana", "NutriMax Suplementos", "suplementos", 7, 0),
            ("prod_codemaster", "mgr_ana", "CodeMaster Cursos", "cursos", 9, 0),
            ("prod_gestaopro", "mgr_ana", "GestãoPro Software", "software", 4, 1),
            ("prod_essencia", "mgr_ana", "Essência Natural", "cosméticos", 6, 0),
            ("prod_fitpulse", "mgr_ana", "FitPulse Suplementos", "suplementos", 5, 0),
            # Carteira do Carlos (2 produtores — isolamento de acesso)
            ("prod_bella", "mgr_carlos", "BellaSuplementos", "suplementos", 10, 0),
            ("prod_academy", "mgr_carlos", "AcademyFlow Cursos", "cursos", 8, 0),
        ]

        producer_rows = [
            (
                pid,
                mgr_id,
                name,
                category,
                creators,
                seasonal,
                faker.company_email(),
                faker.cnpj(),
                f"whsec_{faker.lexify(text='????????????????????????')}",
            )
            for pid, mgr_id, name, category, creators, seasonal in producers
        ]
        conn.executemany(
            """
            INSERT INTO producers (
                id, manager_id, name, category, active_creators_count,
                seasonal_pattern, owner_email, tax_id, webhook_secret
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            producer_rows,
        )

        # Cenário 1: Risco alto (lançamento + 12 creators + webhooks 401 + queda vendas)
        _insert_orders(conn, faker, "prod_aurora", 20, 12, 149.90)
        _insert_webhooks(conn, "prod_aurora", 20, 9, "assinatura_invalida", 401, fail_days=5)
        _insert_tickets(conn, "prod_aurora", [(4, "erro_integracao", "open"), (2, "erro_integracao", "open")])

        # Cenário 2: Risco alto (timeout Pix + queda vendas + 3 tickets de checkout)
        _insert_orders(conn, faker, "prod_nutrimax", 15, 10, 189.00)
        _insert_webhooks(conn, "prod_nutrimax", 15, 4, "timeout_checkout_pix", 504, fail_days=6)
        _insert_tickets(
            conn,
            "prod_nutrimax",
            [(5, "falha_checkout", "open"), (3, "falha_checkout", "open"), (1, "falha_checkout", "open")],
        )

        # Cenário 3: Falso alarme técnico (muitos erros de analytics, vendas estáveis)
        _insert_orders(conn, faker, "prod_codemaster", 14, 15, 297.00)
        _insert_webhooks(conn, "prod_codemaster", 15, 5, "endpoint_analytics_offline", 500, fail_days=6)
        _insert_tickets(conn, "prod_codemaster", [])

        # Cenário 4: Queda por sazonalidade (sem erro técnico e sem ticket)
        _insert_orders(conn, faker, "prod_gestaopro", 10, 8, 199.00)
        _insert_webhooks(conn, "prod_gestaopro", 10, 0, "ok", fail_days=0)
        _insert_tickets(conn, "prod_gestaopro", [])

        # Cenários 5 e 6: Saudáveis na carteira da Ana
        _insert_orders(conn, faker, "prod_essencia", 12, 13, 119.90)
        _insert_webhooks(conn, "prod_essencia", 15, 0, "ok", fail_days=0)
        _insert_tickets(conn, "prod_essencia", [])

        _insert_orders(conn, faker, "prod_fitpulse", 12, 12, 159.00)
        _insert_webhooks(conn, "prod_fitpulse", 12, 0, "ok", fail_days=0)
        _insert_tickets(conn, "prod_fitpulse", [(2, "duvida_relatorio", "open")])

        # Cenários 7 e 8: Carteira do Carlos (acesso bloqueado para Ana)
        _insert_orders(conn, faker, "prod_bella", 18, 10, 175.00)
        _insert_webhooks(conn, "prod_bella", 18, 8, "assinatura_invalida", 401, fail_days=6)
        _insert_tickets(conn, "prod_bella", [(3, "erro_integracao", "open"), (1, "falha_checkout", "open")])

        _insert_orders(conn, faker, "prod_academy", 10, 11, 349.00)
        _insert_webhooks(conn, "prod_academy", 10, 0, "ok", fail_days=0)
        _insert_tickets(conn, "prod_academy", [])

        conn.commit()
    finally:
        conn.close()

    return path


def ensure_seeded(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Garante que o banco existe antes de abrir conexão somente leitura."""
    path = Path(db_path)
    if not path.exists():
        seed_database(path)
    return path


if __name__ == "__main__":
    created = seed_database()
    print(f"Banco simulado criado com sucesso em: {created}")