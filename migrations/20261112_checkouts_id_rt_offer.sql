-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- checkouts.id_rt_offer: id da offer criada no RedTrack pelo POST
-- /offers-rt-ap (o "id" que o POST /offers do RedTrack devolve). Só as
-- linhas de front (funnel_stage Purchase) recebem valor; o resto fica null.
-- Mesmo valor que o AutoPages guarda em checkout_links.redtrack_id.
--
-- Backfill das offers já criadas: app/scripts/backfill_checkouts_id_rt_offer.py

alter table public.checkouts
  add column if not exists id_rt_offer text;

-- "add column if not exists" pula em silêncio se a coluna já existir com
-- outro tipo; confira depois de rodar:
--   select data_type from information_schema.columns
--   where table_name = 'checkouts' and column_name = 'id_rt_offer';
