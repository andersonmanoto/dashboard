-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Colunas novas em slicktext_accounts (criadas direto no banco):
--   allow_input     liga/desliga manual do envio pra SlickText. false = não
--                   envia nada mesmo com status 'active'.
--   deactivated_at  hora em que o worker inativou a conta por queda
--                   (gravada junto com status='inactive', ver
--                   deactivation_fields() em app/services/slicktext_accounts.py).
--
-- get_slicktext_credentials() é a fonte das contas que recebem envio
-- (carrinho abandonado e compra aprovada), então o filtro de allow_input
-- entra aqui. allow_input null conta como false (não envia).

create or replace function public.get_slicktext_credentials()
returns table (
  id       uuid,
  name     text,
  brand_id text,
  api_key  text
)
language sql security definer set search_path = ''
as $$
  select a.id, a.name, a.brand_id, d.decrypted_secret
  from public.slicktext_accounts a
  join vault.decrypted_secrets d on d.id = a.secret_id
  where a.status = 'active'
    and a.allow_input is true
  order by a.created_at;
$$;

revoke execute on function public.get_slicktext_credentials() from public, anon, authenticated;
grant  execute on function public.get_slicktext_credentials() to service_role;

comment on column public.slicktext_accounts.allow_input is 'false = não envia pra SlickText mesmo com status active.';
comment on column public.slicktext_accounts.deactivated_at is 'Hora em que o worker inativou a conta por queda (401/404 confirmado no GET /brands).';
