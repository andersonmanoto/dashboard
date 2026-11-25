-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- redtrack_events.click_sub_12: o "clickSub12" da tela Conversions do
-- RedTrack (campo sub12 do GET /conversions). Na maioria das vendas é o nome
-- do criativo de verdade ("BK36", "AD03_meta"), enquanto o rt_ad vem herdado
-- ("S410EDC19FBYTR2AD19 v1"). Em tráfego Google/YouTube vem o gclid.
-- Conferido em 25/11 (07 a 22/11): 319 de 464 conversões com sub12.
--
-- Vai só como coluna informativa no relatório Ativos Validados (.xlsx), ao
-- lado do criativo. A snapshot_daily_redtrack não muda: o custo continua por
-- rt_ad.
--
-- Depois de rodar, pra preencher os dias antigos (o cron só recoleta hoje e
-- ontem):
--   PYTHONPATH=.:app python3 app/scripts/sync_redtrack_creatives.py <from> <to>

alter table public.redtrack_events
  add column if not exists click_sub_12 text;
