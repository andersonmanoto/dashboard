import asyncio
from loguru import logger

# Importa o cliente assíncrono do Supabase
from supabase import create_async_client

# Importa o gerenciador de pool do ARQ e as configurações do Redis
from arq import create_pool
from arq.connections import RedisSettings

from app.config import get_settings


async def reprocess_pending_webhooks():
    settings = get_settings()

    logger.info("A inicializar conexões (Supabase e Redis)...")

    # 1. Conecta ao Supabase
    db_async = await create_async_client(settings.supabase_url, settings.supabase_key)

    # 2. Conecta ao Redis
    redis_pool = await create_pool(
        RedisSettings(host=settings.redis_host, port=settings.redis_port)
    )

    # 3. Busca todos os registros pendentes na webhook_inbox
    logger.info("A procurar webhooks antigos com status 'pending'...")
    response = (
        await db_async.table("webhook_inbox")
        .select("id, network, payload")
        .eq("status", "pending")
        .execute()
    )

    records = response.data

    if not records:
        logger.info("Maravilha! Não há nenhum webhook pendente na tabela.")
        return

    logger.info(
        f"Encontrados {len(records)} webhooks pendentes! A enviar para o Worker..."
    )

    count = 0
    # 4. Enfileira cada registro no Redis
    for row in records:
        inbox_id = row["id"]
        network = row["network"]
        payload = row["payload"]

        try:
            # Chama a task exata que o seu worker já escuta
            await redis_pool.enqueue_job(
                "task_process_webhook",
                network_str=network,
                payload=payload,
                inbox_id=inbox_id,
            )
            count += 1
            logger.debug(f"Enfileirado: {inbox_id} ({network})")
        except Exception as e:
            logger.error(f"Falha ao enfileirar {inbox_id}: {e}")

    logger.info(
        f"Reprocessamento concluído! {count} webhooks atirados para o Redis com sucesso."
    )


if __name__ == "__main__":
    # Roda a função assíncrona
    asyncio.run(reprocess_pending_webhooks())
