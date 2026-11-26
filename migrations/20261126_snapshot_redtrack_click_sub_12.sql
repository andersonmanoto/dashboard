-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Adiciona click_sub_12 (o "clickSub12" do RedTrack) na redtrack_cost_daily e
-- na snapshot_daily_redtrack: a snapshot passa a ter 1 linha por dia +
-- plataforma + produto + oferta original + oferta atual + lead + vturb +
-- microlead + rt_ad + click_sub_12.
--
-- Por que funciona sem rateio: o GET /report aceita group=...,sub12 e devolve
-- o custo por sub12 (é parâmetro do clique). Conferido em 20/11 e 24/11:
-- custo, cliques, lp_clicks, IC, vendas e receita batem com o agrupamento sem
-- sub12 (diferença de centavos, arredondamento), e o sub12 das vendas no
-- /report é o mesmo do /conversions.
--
-- gclid fica como vazio: em tráfego Google/YouTube o sub12 é o gclid (1 por
-- clique, 40+ caracteres; criativo real tem no máximo 38). Se entrasse na
-- chave, cada venda do YT viraria uma linha e o custo dos cliques sem venda
-- sumiria (a snapshot só guarda linha com venda). Mesma regra do
-- parse_click_sub_12 (redtrack_creative_service.py). Fora isso o valor fica
-- como vem do RedTrack (sem normalizar maiúscula/espaço): grafias diferentes
-- são criativos diferentes.
--
-- Ordem:
--   1. rode esta migration ANTES do deploy (o código novo manda várias linhas
--      de custo por rt_ad, uma por sub12, e a chave antiga da
--      redtrack_cost_daily rejeitaria). O código antigo continua funcionando
--      com ela: só deixa click_sub_12 nulo.
--   2. deploy (worker).
--   3. recolete os dias antigos (o custo antigo foi gravado sem sub12; sem
--      recoletar, o custo cai na linha sem sub12, que pode não ter venda e
--      sumir da snapshot):
--        PYTHONPATH=.:app python3 app/scripts/sync_redtrack_creatives.py 2026-09-01 <hoje>

-- 1. gclid já gravado na events vira vazio -----------------------------------

update public.redtrack_events
   set click_sub_12 = null
 where length(click_sub_12) >= 40;

-- 2. Colunas -----------------------------------------------------------------

alter table public.redtrack_cost_daily add column if not exists click_sub_12 text;
alter table public.snapshot_daily_redtrack add column if not exists click_sub_12 text;

-- 3. Chaves passam a incluir o sub12 ----------------------------------------

alter table public.redtrack_cost_daily drop constraint if exists redtrack_cost_daily_key;
alter table public.redtrack_cost_daily
  add constraint redtrack_cost_daily_key unique nulls not distinct (
    cost_date, rt_ad, campaign_id, landing_id, click_sub_12
  );

alter table public.snapshot_daily_redtrack drop constraint if exists snapshot_daily_redtrack_key;
alter table public.snapshot_daily_redtrack
  add constraint snapshot_daily_redtrack_key unique nulls not distinct (
    snapshot_date, network_id, product_id, original_offer, current_offer, lead,
    vturb_video_id, microlead, rt_ad, click_sub_12
  );

-- 4. Custo: grava click_sub_12 ------------------------------------------------

create or replace function public.replace_redtrack_cost_daily(
  target_date date,
  rows jsonb
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  delete from public.redtrack_cost_daily where cost_date = target_date;

  insert into public.redtrack_cost_daily (
    cost_date, rt_ad, campaign_id, landing_id, campaign, landing,
    original_offer, current_offer, lead, vturb_video_id, microlead, click_sub_12,
    product_id, network_id,
    clicks, lp_clicks, initiate_checkouts, purchases, total_revenue, cost,
    updated_at
  )
  select
    target_date,
    coalesce(r.rt_ad, ''),
    coalesce(r.campaign_id, ''),
    coalesce(r.landing_id, ''),
    r.campaign,
    r.landing,
    r.original_offer,
    r.current_offer,
    r.lead,
    r.vturb_video_id,
    r.microlead,
    r.click_sub_12,
    r.product_id,
    r.network_id,
    coalesce(r.clicks, 0),
    coalesce(r.lp_clicks, 0),
    coalesce(r.initiate_checkouts, 0),
    coalesce(r.purchases, 0),
    coalesce(r.total_revenue, 0),
    coalesce(r.cost, 0),
    now()
  from jsonb_to_recordset(rows) as r (
    rt_ad text, campaign_id text, landing_id text, campaign text, landing text,
    original_offer text, current_offer text, lead text, vturb_video_id text,
    microlead text, click_sub_12 text,
    product_id uuid, network_id uuid,
    clicks integer, lp_clicks integer, initiate_checkouts integer,
    purchases integer, total_revenue numeric, cost numeric
  );
end;
$$;

-- 5. Snapshot: agrupa também por click_sub_12 -------------------------------

create or replace function public.update_snapshot_daily_redtrack(target_date date)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  delete from public.snapshot_daily_redtrack where snapshot_date = target_date;

  insert into public.snapshot_daily_redtrack (
    snapshot_date, network_id, product_id, original_offer, current_offer, lead,
    vturb_video_id, microlead, rt_ad, click_sub_12,
    purchases, total_revenue, cost, total_ic, updated_at
  )
  select
    target_date,
    network_id, product_id, original_offer, current_offer, lead, vturb_video_id,
    microlead, rt_ad, click_sub_12,
    sum(purchases), sum(total_revenue), sum(cost), sum(total_ic), now()
  from (
    select
      e.network_id, e.product_id, e.original_offer, e.current_offer, e.lead,
      e.vturb_video_id, e.microlead, e.rt_ad, e.click_sub_12,
      count(*) filter (where e.conversion_type = 'Purchase') as purchases,
      coalesce(sum(e.payout) filter (where e.conversion_type = 'Purchase'), 0) as total_revenue,
      0::numeric as cost,
      count(*) filter (where e.conversion_type = 'InitiateCheckout') as total_ic
    from public.redtrack_events e
    where e.conversion_date = target_date
      and coalesce(e.campaign, '') not ilike 'backredirect%'
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9

    union all

    select
      c.network_id, c.product_id, c.original_offer, c.current_offer, c.lead,
      c.vturb_video_id, c.microlead, c.rt_ad, c.click_sub_12,
      0, 0, sum(c.cost), 0
    from public.redtrack_cost_daily c
    where c.cost_date = target_date
      and coalesce(c.campaign, '') not ilike 'backredirect%'
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9
  ) t
  group by network_id, product_id, original_offer, current_offer, lead,
           vturb_video_id, microlead, rt_ad, click_sub_12
  having sum(purchases) > 0;
end;
$$;
