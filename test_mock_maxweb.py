import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from loguru import logger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from config import get_settings
from repositories.database import DatabaseRepository
from services.report_service import ReportService


# Dados mockados — ajusta à vontade pra testar cenários diferentes
MOCK_RESULTADOS = [
    {
        "aff_id": "69138",
        "aff_name": "MaxWeb 2",
        "produto": "BreathEaseX",
        "valor_total_vendas": 982.34,
        "valor_total_refunds": 335.87,
        "taxa_refund_percentual": 34.2,
    },
    {
        "aff_id": "70211",
        "aff_name": "Carlos Afiliados LTDA",
        "produto": "GlucoShield Pro",
        "valor_total_vendas": 4520.00,
        "valor_total_refunds": 2712.00,
        "taxa_refund_percentual": 60.0,
    },
    {
        "aff_id": "58890",
        "aff_name": "Marketing Direto",
        "produto": "NightSlim",
        "valor_total_vendas": 1200.50,
        "valor_total_refunds": 198.08,
        "taxa_refund_percentual": 16.5,
    },
]


async def main():
    logger.info("A iniciar teste local (mockado) do alerta da MaxWeb...")

    settings = get_settings()
    db_repo = DatabaseRepository(settings)
    report_service = ReportService(settings, db_repo)

    # Monta a resposta mockada da RPC
    mock_response = MagicMock()
    mock_response.data = MOCK_RESULTADOS

    # .rpc(...) é síncrono no client real, só o .execute() é async
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute = AsyncMock(return_value=mock_response)

    with patch("services.report_service.create_async_client", return_value=mock_client):
        await report_service.generate_and_send_maxweb_refund_warning()

    logger.info("Teste local (mockado) finalizado!")


if __name__ == "__main__":
    asyncio.run(main())