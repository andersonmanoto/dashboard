"""
Códigos de last_error da slicktext_sync_queue e da slicktext_input_queue.

Venda (sync_approved_orders), abandono (slicktext_service) e input manual
(slicktext_input_service) fazem as mesmas checagens e gravam o mesmo código,
pra o dashboard agrupar sem regex. O detalhe (produto, telefone cru...) vai
pro log, não pro código.
"""

ERR_EMPTY_PHONE = "telefone_vazio"  # lead chegou sem telefone
ERR_INVALID_PHONE = "telefone_invalido"  # tem telefone, mas não é número válido
ERR_ABSTRACT = "rejeitado_abstract"  # AbstractAPI reprovou o telefone
ERR_ABSTRACT_API = "abstract_api_error"  # AbstractAPI fora (retry)
ERR_LIST_NOT_MAPPED = "lista_nao_mapeada"  # produto sem lista em slicktext_product_lists
ERR_NO_ACTIVE_ACCOUNT = "sem_conta_active"  # tem lista mapeada, nenhuma conta active
ERR_PRODUCT_UNKNOWN = "produto_nao_identificado"  # venda chegou sem product_id


def is_empty_phone(raw_phone) -> bool:
    return not str(raw_phone or "").strip()
