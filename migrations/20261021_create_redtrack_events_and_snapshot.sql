-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Ranking de criativos (rt_ad) do RedTrack: quanto cada criativo gastou,
-- quantos ICs/vendas trouxe e quanto faturou, dia a dia.
--
-- Duas tabelas, alimentadas pelo cron cron_sync_redtrack_creatives (worker.py):
--
--   redtrack_events          1 linha por conversão (GET /conversions). Detalhe
--                            venda a venda: criativo, lead, offers, produto.
--                            Upsert pelo id da conversão no RedTrack.
--
--   snapshot_daily_redtrack  1 linha por dia + rt_ad + campaign + landing
--                            (GET /report?group=rt_ad,landing,campaign). Valores
--                            ABSOLUTOS do dia, como o RedTrack mostra: cada
--                            coleta substitui o dia inteiro
--                            (replace_snapshot_daily_redtrack), então rodar de
--                            novo nunca soma em dobro.
--
-- Por que a snapshot não é por offer: o custo do Facebook entra no clique,
-- antes do visitante chegar na offer. Agrupando por offer, o custo cai todo
-- em linhas "sem offer" e a receita em outras — não dá ROAS. Por isso o
-- produto/network da snapshot vem das offers da campaign (campaign -> streams
-- -> offers -> título "Buygoods | AlphaErec | 6 Potes | Funil 3").
--
-- original_offer / current_offer / lead saem do título da landing, quando ele
-- segue o padrão "G1 | FB | OT248 - OT283 - SPY | L1 - C01 | ...". Título fora
-- do padrão fica com essas colunas nulas.

create table if not exists public.redtrack_events (
  id uuid primary key default gen_random_uuid(),
  rt_conversion_id text not null unique,  -- "id" da conversão no RedTrack
  click_id text,
  conversion_type text not null,          -- Purchase, InitiateCheckout, ...
  conversion_time timestamptz,
  conversion_date date not null,          -- data no fuso da conta (America/Sao_Paulo)
  rt_ad text not null default '',         -- criativo
  campaign_id text,
  campaign text,
  landing_id text,
  landing text,
  offer_id text,
  offer text,
  original_offer text,
  current_offer text,
  lead text,
  product_id uuid references public.products (id),
  network_id uuid references public.networks (id),
  payout numeric not null default 0,
  status text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_redtrack_events_date_ad
  on public.redtrack_events (conversion_date, rt_ad);

create table if not exists public.snapshot_daily_redtrack (
  id uuid primary key default gen_random_uuid(),
  snapshot_date date not null,
  rt_ad text not null default '',
  campaign_id text not null default '',
  landing_id text not null default '',
  campaign text,
  landing text,
  original_offer text,
  current_offer text,
  lead text,
  product_id uuid references public.products (id),
  network_id uuid references public.networks (id),
  clicks integer not null default 0,
  lp_clicks integer not null default 0,
  initiate_checkouts integer not null default 0,  -- convtype3 no /report
  purchases integer not null default 0,           -- convtype1 no /report
  total_revenue numeric not null default 0,
  cost numeric not null default 0,
  -- Custo por IC da linha. Pra um período/agrupamento maior, recalcule com
  -- sum(cost) / sum(initiate_checkouts) — nunca faça média desta coluna.
  cost_ic numeric generated always as (
    case when initiate_checkouts > 0 then round(cost / initiate_checkouts, 2) end
  ) stored,
  updated_at timestamptz not null default now(),
  constraint snapshot_daily_redtrack_key
    unique (snapshot_date, rt_ad, campaign_id, landing_id)
);

create index if not exists idx_snapshot_daily_redtrack_ad
  on public.snapshot_daily_redtrack (rt_ad, snapshot_date);

-- Substitui o dia inteiro numa transação só: apaga o que havia e insere o que
-- o RedTrack devolveu agora. Linha que sumiu no RedTrack some daqui também.
create or replace function public.replace_snapshot_daily_redtrack(
  target_date date,
  rows jsonb
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  delete from public.snapshot_daily_redtrack where snapshot_date = target_date;

  insert into public.snapshot_daily_redtrack (
    snapshot_date, rt_ad, campaign_id, landing_id, campaign, landing,
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
