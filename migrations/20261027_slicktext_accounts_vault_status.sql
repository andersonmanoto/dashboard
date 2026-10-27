-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Contas SlickText dinâmicas: as contas caem com frequência, então em vez de
-- api_key no .env (SLICKTEXT_API_KEY_<BRAND_ID>) e deploy a cada conta nova,
-- tudo passa a ser cadastrado pelo front:
--
--   status     'active' | 'inactive' -- só conta active recebe envio. O worker
--              inativa sozinho a conta que caiu (ver slicktext_accounts.py).
--   secret_id  api_key no Vault, mesmo padrão da shipoffers_accounts (a key
--              decifrada nunca vai pro client; só o worker, via service_role,
--              lê pela get_slicktext_credentials()).
--
-- Escrita do front:
--   conta nova       rpc create_slicktext_account(name, brand_id, api_key)
--   trocar api_key   rpc update_slicktext_account_api_key(id, api_key)
--   status / nome    update direto em slicktext_accounts (admin)
--   listas           insert/update/delete direto em slicktext_product_lists (admin)
--
-- Ordem:
--   1. esta migration
--   2. app/scripts/migrate_slicktext_keys_to_vault.py (copia as keys do .env
--      pro Vault -- conta sem key no Vault não recebe envio no código novo)
--   3. deploy
--   4. tirar SLICKTEXT_API_KEY_* do .env
--
-- Usa public.is_admin(), criada pela migration 001 da shipoffers (mesmo projeto).

-- 1. Colunas ------------------------------------------------------------------

alter table public.slicktext_accounts
  add column if not exists status text not null default 'active',
  add column if not exists secret_id uuid;

alter table public.slicktext_accounts drop constraint if exists slicktext_accounts_status_check;
alter table public.slicktext_accounts
  add constraint slicktext_accounts_status_check check (status in ('active', 'inactive'));

comment on column public.slicktext_accounts.status is 'active = recebe envio; inactive = desligada no front ou caída (inativada pelo worker).';
comment on column public.slicktext_accounts.secret_id is 'FK lógica para vault.secrets.id (api_key) — nunca expor a key decifrada ao client.';

-- 2. RLS: front (admin) lê e edita; worker usa service_role --------------------

alter table public.slicktext_accounts enable row level security;
alter table public.slicktext_product_lists enable row level security;

drop policy if exists slicktext_accounts_admin_select on public.slicktext_accounts;
create policy slicktext_accounts_admin_select on public.slicktext_accounts
  for select to authenticated using (public.is_admin());

-- update pro switch de status / renomear; conta nova só pelo RPC (precisa da key)
drop policy if exists slicktext_accounts_admin_update on public.slicktext_accounts;
create policy slicktext_accounts_admin_update on public.slicktext_accounts
  for update to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists slicktext_product_lists_admin_all on public.slicktext_product_lists;
create policy slicktext_product_lists_admin_all on public.slicktext_product_lists
  for all to authenticated using (public.is_admin()) with check (public.is_admin());

-- 3. RPC: criar conta -----------------------------------------------------------

create or replace function public.create_slicktext_account(
  p_name     text,
  p_brand_id text,
  p_api_key  text
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

  insert into public.slicktext_accounts (name, brand_id)
  values (p_name, p_brand_id)
  returning id into v_id;

  -- nome do secret pelo id (único), não pelo brand_id: conta recadastrada
  -- com o mesmo brand_id não bate no unique de vault.secrets.name
  update public.slicktext_accounts
     set secret_id = vault.create_secret(p_api_key, 'slicktext_' || v_id, 'SlickText API key ' || p_name)
   where id = v_id;

  return v_id;
end;
$$;

-- 4. RPC: trocar api_key (também grava a primeira key de conta que não tinha) --

create or replace function public.update_slicktext_account_api_key(p_id uuid, p_api_key text)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_name   text;
  v_secret uuid;
begin
  if coalesce(auth.jwt() ->> 'role', '') <> 'service_role' and not public.is_admin() then
    raise exception 'permission denied' using errcode = '42501';
  end if;

  select name, secret_id into v_name, v_secret from public.slicktext_accounts where id = p_id;
  if not found then
    raise exception 'conta % não encontrada', p_id;
  end if;

  if v_secret is null then
    update public.slicktext_accounts
       set secret_id = vault.create_secret(p_api_key, 'slicktext_' || p_id, 'SlickText API key ' || v_name)
     where id = p_id;
  else
    perform vault.update_secret(v_secret, p_api_key);
  end if;
end;
$$;

-- 5. RPC: credenciais decifradas das contas active — SOMENTE service_role ------

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
  order by a.created_at;
$$;

revoke execute on function public.create_slicktext_account(text, text, text)      from public, anon;
revoke execute on function public.update_slicktext_account_api_key(uuid, text)    from public, anon;
revoke execute on function public.get_slicktext_credentials()                     from public, anon, authenticated;

grant execute on function public.create_slicktext_account(text, text, text)      to authenticated, service_role;
grant execute on function public.update_slicktext_account_api_key(uuid, text)    to authenticated, service_role;
grant execute on function public.get_slicktext_credentials()                     to service_role;
