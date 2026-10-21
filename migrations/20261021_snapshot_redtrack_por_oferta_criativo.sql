-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Reestrutura a snapshot do RedTrack no formato pedido pelo time (21/10):
-- 1 linha por dia + network + produto + original_offer + current_offer +
-- lead + criativo (rt_ad), populada a partir da redtrack_events.
--
--   redtrack_events          (sem mudança) GET /conversions, 1 linha por conversão
--   redtrack_cost_daily      era a snapshot_daily_redtrack: espelho do
--                            GET /report por rt_ad+landing+campaign. Agora só
--                            serve de fonte do CUSTO (a events não tem custo:
--                            o gasto do Facebook é por clique, não por conversão)
--   snapshot_daily_redtrack  NOVA. purchases / total_ic / total_revenue vêm da
--                            redtrack_events; cost vem da redtrack_cost_daily.
--                            Recalculada por update_snapshot_daily_redtrack(dia).
--
-- O custo encaixa nesse nível sem rateio: original_offer, current_offer e lead
-- saem da landing, e o RedTrack já entrega o custo por criativo + landing.
--
-- Depois de rodar: deploy imediato (o worker antigo chama
-- replace_snapshot_daily_redtrack, que deixa de existir — o cron só loga erro
-- até o deploy) e rodar o backfill pra popular a snapshot nova.

-- 1. A snapshot antiga vira a tabela de custo ------------------------------

alter table public.snapshot_daily_redtrack rename to redtrack_cost_daily;
alter table public.redtrack_cost_daily
  rename constraint snapshot_daily_redtrack_key to redtrack_cost_daily_key;
alter index public.idx_snapshot_daily_redtrack_ad rename to idx_redtrack_cost_daily_ad;
alter table public.redtrack_cost_daily rename column snapshot_date to cost_date;

-- custo por IC agora é coluna da snapshot nova
alter table public.redtrack_cost_daily drop column if exists cost_ic;

drop function if exists public.replace_snapshot_daily_redtrack(date, jsonb);

-- Substitui o dia inteiro numa transação só (mesma lógica da função antiga).
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
    original_offer, current_offer, lead, product_id, network_id,
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
    original_offer text, current_offer text, lead text,
    product_id uuid, network_id uuid,
    clicks integer, lp_clicks integer, initiate_checkouts integer,
    purchases integer, total_revenue numeric, cost numeric
  );
end;
$$;

-- 2. Snapshot nova ---------------------------------------------------------

create table if not exists public.snapshot_daily_redtrack (
  id uuid primary key default gen_random_uuid(),
  snapshot_date date not null,
  network_id uuid references public.networks (id),
  product_id uuid references public.products (id),
  original_offer text,
  current_offer text,
  lead text,
  rt_ad text not null default '',
  purchases integer not null default 0,
  total_revenue numeric not null default 0,  -- payout das vendas de front (Purchase)
  cost numeric not null default 0,
  total_ic integer not null default 0,       -- conversões InitiateCheckout
  -- Custo por IC: cost / total_ic. Pra período/agrupamento maior, recalcule
  -- com sum(cost) / sum(total_ic) — nunca faça média desta coluna.
  cost_ic numeric generated always as (
    case when total_ic > 0 then round(cost / total_ic, 2) end
  ) stored,
  updated_at timestamptz not null default now(),
  constraint snapshot_daily_redtrack_key unique nulls not distinct (
    snapshot_date, network_id, product_id, original_offer, current_offer, lead, rt_ad
  )
);

create index if not exists idx_snapshot_daily_redtrack_ad
  on public.snapshot_daily_redtrack (rt_ad, snapshot_date);

-- Recalcula o dia inteiro a partir da redtrack_events (conversões) e da
-- redtrack_cost_daily (custo). Junta os dois lados por UNION + GROUP BY em vez
-- de JOIN: a chave tem colunas que podem ser nulas (lead/offers de landing
-- fora do padrão) e o GROUP BY trata nulo como igual.
-- Só entra criativo com venda no dia (purchases > 0). BackRedirect fica de fora
-- dos dois lados (a events já nem recebe; no custo é sempre 0).
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
    rt_ad, purchases, total_revenue, cost, total_ic, updated_at
  )
  select
    target_date,
    network_id, product_id, original_offer, current_offer, lead, rt_ad,
    sum(purchases), sum(total_revenue), sum(cost), sum(total_ic), now()
  from (
    select
      e.network_id, e.product_id, e.original_offer, e.current_offer, e.lead, e.rt_ad,
      count(*) filter (where e.conversion_type = 'Purchase') as purchases,
      coalesce(sum(e.payout) filter (where e.conversion_type = 'Purchase'), 0) as total_revenue,
      0::numeric as cost,
      count(*) filter (where e.conversion_type = 'InitiateCheckout') as total_ic
    from public.redtrack_events e
    where e.conversion_date = target_date
      and coalesce(e.campaign, '') not ilike 'backredirect%'
    group by 1, 2, 3, 4, 5, 6

    union all

    select
      c.network_id, c.product_id, c.original_offer, c.current_offer, c.lead, c.rt_ad,
      0, 0, sum(c.cost), 0
    from public.redtrack_cost_daily c
    where c.cost_date = target_date
      and coalesce(c.campaign, '') not ilike 'backredirect%'
    group by 1, 2, 3, 4, 5, 6
  ) t
  group by network_id, product_id, original_offer, current_offer, lead, rt_ad
  having sum(purchases) > 0;
end;
$$;
