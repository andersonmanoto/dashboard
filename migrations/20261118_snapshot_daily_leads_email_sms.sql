-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Duas snapshots de envio de leads, uma linha por dia + plataforma + rede +
-- produto (colunas em /tmp/tabelas/tabelas.ods):
--
--   snapshot_daily_leads_email  <- aweber_lead_queue     (platform = 'aweber')
--   snapshot_daily_leads_sms    <- slicktext_sync_queue  (platform = 'slicktext')
--
-- Regras:
--   - Conta LEAD, não linha da fila. As duas filas podem ter mais de uma linha
--     por lead (uma por lista): o lead é event_id (venda) ou abandoned_cart_id
--     (abandono).
--   - success: pelo menos uma linha do lead entrou na plataforma
--       aweber: status 'sent' | slicktext: status 'synced'
--   - error: todas as linhas do lead terminaram e nenhuma entrou
--       aweber: 'failed' | slicktext: 'failed' ou 'skipped' (telefone
--       inválido, Abstract, país fora do escopo, lista não mapeada...)
--   - Lead com linha ainda 'pending'/'retry' e nenhuma de sucesso não conta
--     ainda; entra numa rodada seguinte do cron.
--   - snapshot_date = dia em que o lead entrou na fila (primeiro created_at do
--     lead), no fuso America/Sao_Paulo.
--   - product_id / network_id: events (venda) ou abandoned_carts (abandono);
--     na slicktext, network_id da própria fila como último recurso. Lead sem
--     produto/rede fica com null na coluna.
--   - slicktext_input_queue (input manual de leads em lote) NÃO entra.
--
-- As filas são limpas (slicktext venda: 3 dias; aweber sent: 7 dias), então a
-- snapshot tem que ser gravada enquanto as linhas existem: o pg_cron abaixo
-- recalcula hoje e ontem a cada 15 min. Por isso a função faz UPSERT e nunca
-- apaga: recalcular um dia já limpo da fila não zera o que foi gravado.
-- Consequência: se o produto/rede de um lead mudar depois, a combinação
-- antiga fica na tabela (mesmo comportamento das outras snapshots com
-- ON CONFLICT).
--
-- Carga inicial (o que ainda está nas filas, ~desde 2026-11-05):
--   select public.update_daily_leads_snapshot(d::date)
--   from generate_series('2026-11-05'::date, current_date, '1 day') d;

begin;

-- 1. Tabelas ---------------------------------------------------------------------

create table if not exists public.snapshot_daily_leads_email (
  id uuid primary key default gen_random_uuid(),
  snapshot_date date not null,
  platform text not null default 'aweber',
  qty_neworder_success integer not null default 0,
  qty_neworder_error integer not null default 0,
  qty_abandon_success integer not null default 0,
  qty_abandon_error integer not null default 0,
  product_id uuid references public.products (id) on delete set null,
  network_id uuid references public.networks (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint snapshot_daily_leads_email_key
    unique nulls not distinct (snapshot_date, platform, network_id, product_id)
);

create index if not exists idx_snapshot_daily_leads_email_product
  on public.snapshot_daily_leads_email (product_id, snapshot_date);

create table if not exists public.snapshot_daily_leads_sms (
  id uuid primary key default gen_random_uuid(),
  snapshot_date date not null,
  platform text not null default 'slicktext',
  qty_neworder_success integer not null default 0,
  qty_neworder_error integer not null default 0,
  qty_abandon_success integer not null default 0,
  qty_abandon_error integer not null default 0,
  product_id uuid references public.products (id) on delete set null,
  network_id uuid references public.networks (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint snapshot_daily_leads_sms_key
    unique nulls not distinct (snapshot_date, platform, network_id, product_id)
);

create index if not exists idx_snapshot_daily_leads_sms_product
  on public.snapshot_daily_leads_sms (product_id, snapshot_date);

-- Mesmo acesso das outras snapshots: só service_role escreve; o front lê.
alter table public.snapshot_daily_leads_email enable row level security;
alter table public.snapshot_daily_leads_sms enable row level security;

drop policy if exists snapshot_daily_leads_email_read on public.snapshot_daily_leads_email;
create policy snapshot_daily_leads_email_read
  on public.snapshot_daily_leads_email for select to authenticated using (true);

drop policy if exists snapshot_daily_leads_sms_read on public.snapshot_daily_leads_sms;
create policy snapshot_daily_leads_sms_read
  on public.snapshot_daily_leads_sms for select to authenticated using (true);

-- 2. E-mail (AWeber) -------------------------------------------------------------

create or replace function public.update_daily_leads_email_snapshot(target_date date)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_start timestamptz := (target_date::timestamp) at time zone 'America/Sao_Paulo';
  v_end   timestamptz := ((target_date + 1)::timestamp) at time zone 'America/Sao_Paulo';
begin
  insert into public.snapshot_daily_leads_email as s (
    snapshot_date, platform, network_id, product_id,
    qty_neworder_success, qty_neworder_error,
    qty_abandon_success, qty_abandon_error,
    updated_at
  )
  select
    target_date, 'aweber', l.network_id, l.product_id,
    count(*) filter (where l.lead_type = 'neworder' and l.ok),
    count(*) filter (where l.lead_type = 'neworder' and not l.ok and l.done),
    count(*) filter (where l.lead_type = 'abandon' and l.ok),
    count(*) filter (where l.lead_type = 'abandon' and not l.ok and l.done),
    now()
  from (
    select
      q.lead_type,
      coalesce(e.network_id, ac.network_id) as network_id,
      coalesce(e.product_id, ac.product_id) as product_id,
      bool_or(q.status = 'sent') as ok,
      bool_and(q.status in ('sent', 'failed')) as done
    from public.aweber_lead_queue q
    left join public.events e on e.id = q.event_id
    left join public.abandoned_carts ac on ac.id = q.abandoned_cart_id
    -- folga de 1 dia: linha extra de lista pode ter sido criada depois
    where q.created_at >= v_start - interval '1 day'
      and q.created_at < v_end + interval '1 day'
    group by q.lead_type,
             coalesce(q.event_id::text, q.abandoned_cart_id::text, q.id::text),
             2, 3
    having min(q.created_at) >= v_start and min(q.created_at) < v_end
  ) l
  group by l.network_id, l.product_id
  on conflict (snapshot_date, platform, network_id, product_id) do update set
    qty_neworder_success = excluded.qty_neworder_success,
    qty_neworder_error   = excluded.qty_neworder_error,
    qty_abandon_success  = excluded.qty_abandon_success,
    qty_abandon_error    = excluded.qty_abandon_error,
    updated_at           = excluded.updated_at;
end;
$$;

-- 3. SMS (SlickText) -------------------------------------------------------------

create or replace function public.update_daily_leads_sms_snapshot(target_date date)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_start timestamptz := (target_date::timestamp) at time zone 'America/Sao_Paulo';
  v_end   timestamptz := ((target_date + 1)::timestamp) at time zone 'America/Sao_Paulo';
begin
  insert into public.snapshot_daily_leads_sms as s (
    snapshot_date, platform, network_id, product_id,
    qty_neworder_success, qty_neworder_error,
    qty_abandon_success, qty_abandon_error,
    updated_at
  )
  select
    target_date, 'slicktext', l.network_id, l.product_id,
    count(*) filter (where l.lead_type = 'neworder' and l.ok),
    count(*) filter (where l.lead_type = 'neworder' and not l.ok and l.done),
    count(*) filter (where l.lead_type = 'abandon' and l.ok),
    count(*) filter (where l.lead_type = 'abandon' and not l.ok and l.done),
    now()
  from (
    select
      q.lead_type,
      coalesce(e.network_id, ac.network_id, q.network_id) as network_id,
      coalesce(e.product_id, ac.product_id) as product_id,
      bool_or(q.status = 'synced') as ok,
      bool_and(q.status in ('synced', 'failed', 'skipped')) as done
    from public.slicktext_sync_queue q
    left join public.events e on e.id = q.event_id
    left join public.abandoned_carts ac on ac.id = q.abandoned_cart_id
    -- folga de 1 dia: o cron cria a linha de cada lista depois da original
    where q.created_at >= v_start - interval '1 day'
      and q.created_at < v_end + interval '1 day'
    group by q.lead_type,
             coalesce(q.event_id::text, q.abandoned_cart_id::text, q.id::text),
             2, 3
    having min(q.created_at) >= v_start and min(q.created_at) < v_end
  ) l
  group by l.network_id, l.product_id
  on conflict (snapshot_date, platform, network_id, product_id) do update set
    qty_neworder_success = excluded.qty_neworder_success,
    qty_neworder_error   = excluded.qty_neworder_error,
    qty_abandon_success  = excluded.qty_abandon_success,
    qty_abandon_error    = excluded.qty_abandon_error,
    updated_at           = excluded.updated_at;
end;
$$;

-- 4. As duas de uma vez + cron ----------------------------------------------------

create or replace function public.update_daily_leads_snapshot(target_date date)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform public.update_daily_leads_email_snapshot(target_date);
  perform public.update_daily_leads_sms_snapshot(target_date);
end;
$$;

revoke all on function public.update_daily_leads_email_snapshot(date) from public, anon, authenticated;
revoke all on function public.update_daily_leads_sms_snapshot(date) from public, anon, authenticated;
revoke all on function public.update_daily_leads_snapshot(date) from public, anon, authenticated;
grant execute on function public.update_daily_leads_email_snapshot(date) to service_role;
grant execute on function public.update_daily_leads_sms_snapshot(date) to service_role;
grant execute on function public.update_daily_leads_snapshot(date) to service_role;

do $$
begin
  if exists (select 1 from cron.job where jobname = 'update-snapshot-daily-leads') then
    perform cron.unschedule('update-snapshot-daily-leads');
  end if;
end $$;

-- Hoje e ontem (fuso de São Paulo): lead de ontem pode terminar o envio hoje.
select cron.schedule(
  'update-snapshot-daily-leads',
  '*/15 * * * *',
  $$
    select public.update_daily_leads_snapshot(
      (now() at time zone 'America/Sao_Paulo')::date - 1
    );
    select public.update_daily_leads_snapshot(
      (now() at time zone 'America/Sao_Paulo')::date
    );
  $$
);

commit;

notify pgrst, 'reload schema';

-- Conferência:
-- select snapshot_date, sum(qty_neworder_success) nw_ok, sum(qty_neworder_error) nw_err,
--        sum(qty_abandon_success) ab_ok, sum(qty_abandon_error) ab_err
-- from public.snapshot_daily_leads_sms group by 1 order by 1 desc;
