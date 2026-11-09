-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Input de leads (docs/sms-input-leads-contrato-backend.md), passo 4: o
-- backend fala com o Supabase pelo PostgREST, que não segura lock de linha
-- entre chamadas. Então o "FOR UPDATE SKIP LOCKED" do contrato vira uma
-- reserva com prazo (lease): a RPC trava as linhas só durante o próprio
-- SELECT, grava claimed_at = now() e devolve o lote. Outro worker só pega a
-- mesma linha depois que o prazo vence (worker morto no meio do lote).
--
-- Só reserva linha de input em 'processing' com enqueued_qty preenchido:
-- input 'error' não é reprocessado (contrato, passo 6) e input ainda
-- reunindo leads não tem a fila completa.

alter table public.slicktext_input_queue
  add column if not exists claimed_at timestamptz;

create or replace function public.sms_input_claim_batch(
  p_limit int default 25,
  p_max_attempts int default 5,
  p_lease_minutes int default 15
)
returns table (
  input_id uuid,
  event_id uuid,
  input_list_id uuid,
  customer_phone text,
  status text,
  attempts int
)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with picked as (
    select q.input_id, q.event_id
    from public.slicktext_input_queue q
    join public.slicktext_inputs i on i.id = q.input_id
    where q.status in ('pending', 'retry')
      and q.attempts < p_max_attempts
      and i.status = 'processing'
      and i.enqueued_qty is not null
      and (q.claimed_at is null
           or q.claimed_at < now() - make_interval(mins => p_lease_minutes))
    order by q.created_at
    limit p_limit
    for update of q skip locked
  )
  update public.slicktext_input_queue q
  set claimed_at = now()
  from picked p
  where q.input_id = p.input_id
    and q.event_id = p.event_id
  returning q.input_id, q.event_id, q.input_list_id, q.customer_phone,
            q.status, q.attempts;
end;
$$;

revoke all on function public.sms_input_claim_batch(int, int, int) from public, anon, authenticated;
grant execute on function public.sms_input_claim_batch(int, int, int) to service_role;
