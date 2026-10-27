-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard,
-- depois da 20261027_slicktext_accounts_vault_status.sql.
--
-- create_slicktext_account passa a receber shipped_list_id (lista do SMS de
-- rastreio, usada pelo sync_slicktext.py da shipoffers -- ver
-- sql/004_slicktext_shipped_list.sql de lá, que criou a coluna). Opcional:
-- null = conta não recebe rastreio. Vazio/espaços também vira null.
--
-- Front:
--   supabase.rpc('create_slicktext_account', {
--     p_name, p_brand_id, p_api_key, p_shipped_list_id  // último opcional
--   })
-- Editar shipped_list_id de conta existente continua sendo update direto em
-- slicktext_accounts (policy slicktext_accounts_admin_update).

alter table public.slicktext_accounts
  add column if not exists shipped_list_id text;

-- Assinatura muda (3 -> 4 parâmetros): sem o drop ficariam as duas versões
-- e o PostgREST não saberia qual chamar com 3 argumentos.
drop function if exists public.create_slicktext_account(text, text, text);

create or replace function public.create_slicktext_account(
  p_name            text,
  p_brand_id        text,
  p_api_key         text,
  p_shipped_list_id text default null
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  v_id uuid;
begin
  if coalesce(auth.jwt() ->> 'role', '') <> 'service_role' and not public.is_admin() then
    raise exception 'permission denied' using errcode = '42501';
  end if;

  insert into public.slicktext_accounts (name, brand_id, shipped_list_id)
  values (p_name, p_brand_id, nullif(trim(p_shipped_list_id), ''))
  returning id into v_id;

  -- nome do secret pelo id (único), não pelo brand_id: conta recadastrada
  -- com o mesmo brand_id não bate no unique de vault.secrets.name
  update public.slicktext_accounts
     set secret_id = vault.create_secret(p_api_key, 'slicktext_' || v_id, 'SlickText API key ' || p_name)
   where id = v_id;

  return v_id;
end;
$$;

revoke execute on function public.create_slicktext_account(text, text, text, text) from public, anon;
grant  execute on function public.create_slicktext_account(text, text, text, text) to authenticated, service_role;
