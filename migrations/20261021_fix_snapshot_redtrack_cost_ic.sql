-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Corrige cost_ic da snapshot_daily_redtrack: era cost / initiate_checkouts
-- (o "InitiateCheckout CPA" do RedTrack), mas a coluna que o time usa é a
-- "Ic Cost (Prod)" da tela de Landers, que é cost / lp_clicks — custo por
-- clique da lander pro checkout. Conferido em 21/10 contra a tela:
--   heartfulliving  64,28 / 3 lp_clicks = 21,43
--   onwardpath      82,55 / 3 lp_clicks = 27,52
--   goodtobehere   243,24 / 1 lp_click  = 243,24
--
-- Coluna gerada não tem a expressão alterada in-place: dropa e recria (é
-- calculada, não perde dado). Continua null quando lp_clicks = 0 (gastou sem
-- ninguém clicar pro checkout).

alter table public.snapshot_daily_redtrack drop column if exists cost_ic;

alter table public.snapshot_daily_redtrack
  add column cost_ic numeric generated always as (
    case when lp_clicks > 0 then round(cost / lp_clicks, 2) end
  ) stored;

comment on column public.snapshot_daily_redtrack.cost_ic is
  'Ic Cost (Prod) do RedTrack: cost / lp_clicks. Pra período/agrupamento maior, '
  'recalcule com sum(cost) / sum(lp_clicks) — nunca faça média desta coluna.';
