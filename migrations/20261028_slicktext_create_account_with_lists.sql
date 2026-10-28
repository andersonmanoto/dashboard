-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard,
-- depois da 20261027_slicktext_create_account_shipped_list.sql.
--
-- Conta SlickText nova deixa de ser cadastrada pelo front: agora é o backend
-- (POST /slicktext/accounts, ver app/services/slicktext_setup_service.py)
-- que cria as listas/custom fields na SlickText e chama esta RPC com a
-- service_role. A RPC passa a gravar tudo numa transação só: conta, api_key
-- no Vault, shipped_list_id e as linhas de slicktext_product_lists
--   p_product_lists = [{"product_id", "approved_list_id", "abandoned_list_id"}, ...]
--
-- O front não chama mais: execute sai de authenticated (só service_role).
-- update_slicktext_account_api_key e os updates diretos (status, nome,
-- listas) continuam como estavam.

-- Assinatura muda (4 -> 5 parâmetros): sem o drop ficariam as duas versões.
drop function if exists public.create_slicktext_account(text, text, text, text);

create or replace function public.create_slicktext_account(
  p_name            text,
  p_brand_id        text,
  p_api_key         text,
  p_shipped_list_id text default null,
  p_product_lists   jsonb default '[]'::jsonb
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  v_id uuid;
begin
  if coalesce(auth.jwt() ->> 'role', '') <> 'service_role' then
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

  insert into public.slicktext_product_lists (product_id, slick_account_id, approved_list_id, abandoned_list_id)
  select (l ->> 'product_id')::uuid,
         v_id,
         nullif(trim(l ->> 'approved_list_id'), ''),
         nullif(trim(l ->> 'abandoned_list_id'), '')
  from jsonb_array_elements(coalesce(p_product_lists, '[]'::jsonb)) l;

  return v_id;
end;
$$;

revoke execute on function public.create_slicktext_account(text, text, text, text, jsonb) from public, anon, authenticated;
grant  execute on function public.create_slicktext_account(text, text, text, text, jsonb) to service_role;
