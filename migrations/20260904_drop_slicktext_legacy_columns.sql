-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Cutover concluído: slicktext_product_lists + slicktext_accounts (ver
-- migrations/20260904_slicktext_multi_account.sql) já são a única fonte
-- usada pelo código (app/services/slicktext_service.py e
-- app/scripts/sync_approved_orders.py) pros fluxos de abandono de carrinho
-- e compra aprovada -- validado em teste ponta a ponta contra as duas
-- contas SlickText. As colunas antigas em products não são mais lidas em
-- lugar nenhum, então dropa de vez.

alter table public.products
    drop column if exists slicktext_approved_list_id,
    drop column if exists slicktext_abandoned_list_id;
