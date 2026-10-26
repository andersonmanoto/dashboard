-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Adiciona microlead (ML do título da campaign) na redtrack_events,
-- redtrack_cost_daily e snapshot_daily_redtrack.
--
-- De onde vem: token "ML<número>" em qualquer bloco do título da campaign
-- ("G1 | OT248 | FB | ... | SdHrsPk | ML8 | ...", "G3 | OT248/OT257/ML8 | YT",
-- "G3 | S218-ML2 | YT", "G1 | OT248 | ML5-6-7 | FB"). "ML Sarah" / "ML 2018"
-- de campaigns antigas não contam. Sem ML no título fica null.
-- O custo (redtrack_cost_daily) também precisa da coluna: a snapshot agrupa
-- custo e conversões pela mesma chave.
--
-- Ordem: rode esta migration ANTES do deploy (o código novo grava a coluna;
-- o antigo só deixa null nela). Depois do deploy, recalcule a snapshot:
--   select public.update_snapshot_daily_redtrack(d::date)
--   from generate_series('2026-09-01'::date, current_date, '1 day') d;

-- 1. Colunas + backfill a partir do título da campaign já gravado ------------

alter table public.redtrack_events add column if not exists microlead text;
alter table public.redtrack_cost_daily add column if not exists microlead text;
alter table public.snapshot_daily_redtrack add column if not exists microlead text;

-- mesma regra do parse_microlead (redtrack_creative_service.py)
update public.redtrack_events
   set microlead = upper((regexp_match(campaign, '(?<![a-z])ml\d+(?:-\d+)*', 'i'))[1])
 where microlead is null and campaign is not null;

update public.redtrack_cost_daily
   set microlead = upper((regexp_match(campaign, '(?<![a-z])ml\d+(?:-\d+)*', 'i'))[1])
 where microlead is null and campaign is not null;

-- 2. Chave da snapshot passa a incluir o microlead -----------------------------

alter table public.snapshot_daily_redtrack drop constraint if exists snapshot_daily_redtrack_key;
alter table public.snapshot_daily_redtrack
  add constraint snapshot_daily_redtrack_key unique nulls not distinct (
    snapshot_date, network_id, product_id, original_offer, current_offer, lead,
    vturb_video_id, microlead, rt_ad
  );

-- 3. Custo: grava microlead -----------------------------------------------

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
    original_offer, current_offer, lead, vturb_video_id, microlead, product_id, network_id,
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
    original_offer text, current_offer text, lead text, vturb_video_id text, microlead text,
    product_id uuid, network_id uuid,
    clicks integer, lp_clicks integer, initiate_checkouts integer,
    purchases integer, total_revenue numeric, cost numeric
  );
end;
$$;

-- 4. Snapshot: agrupa também por microlead ----------------------------------

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
    vturb_video_id, microlead, rt_ad, purchases, total_revenue, cost, total_ic, updated_at
  )
  select
    target_date,
    network_id, product_id, original_offer, current_offer, lead, vturb_video_id, microlead, rt_ad,
    sum(purchases), sum(total_revenue), sum(cost), sum(total_ic), now()
  from (
    select
      e.network_id, e.product_id, e.original_offer, e.current_offer, e.lead,
      e.vturb_video_id, e.microlead, e.rt_ad,
      count(*) filter (where e.conversion_type = 'Purchase') as purchases,
      coalesce(sum(e.payout) filter (where e.conversion_type = 'Purchase'), 0) as total_revenue,
      0::numeric as cost,
      count(*) filter (where e.conversion_type = 'InitiateCheckout') as total_ic
    from public.redtrack_events e
    where e.conversion_date = target_date
      and coalesce(e.campaign, '') not ilike 'backredirect%'
    group by 1, 2, 3, 4, 5, 6, 7, 8

    union all

    select
      c.network_id, c.product_id, c.original_offer, c.current_offer, c.lead,
      c.vturb_video_id, c.microlead, c.rt_ad,
      0, 0, sum(c.cost), 0
    from public.redtrack_cost_daily c
    where c.cost_date = target_date
      and coalesce(c.campaign, '') not ilike 'backredirect%'
    group by 1, 2, 3, 4, 5, 6, 7, 8
  ) t
  group by network_id, product_id, original_offer, current_offer, lead, vturb_video_id, microlead, rt_ad
  having sum(purchases) > 0;
end;
$$;
