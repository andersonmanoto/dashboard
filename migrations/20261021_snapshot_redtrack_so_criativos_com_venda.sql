-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- snapshot_daily_redtrack passa a ter só criativos com venda no dia
-- (purchases > 0). Antes entrava todo criativo das landers que venderam,
-- inclusive os que só gastaram ou só tiveram IC — o time não quer essas linhas.
-- redtrack_events e redtrack_cost_daily não mudam (continuam por lander com
-- receita); o cost/total_ic de cada criativo que vendeu continua completo.
--
-- Só recria a função. Pra recalcular os dias já gravados:
--   select public.update_snapshot_daily_redtrack(d::date)
--   from generate_series('2026-10-01'::date, current_date, '1 day') d;

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
