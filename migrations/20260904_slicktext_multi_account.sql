-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Introduz suporte a múltiplas contas SlickText. Hoje só existia 1 conta
-- global (via SLICKTEXT_API_KEY/SLICKTEXT_BRAND_ID no .env) e 2 colunas fixas
-- em products (slicktext_approved_list_id / slicktext_abandoned_list_id).
-- Isso substitui essas colunas por uma tabela de mapeamento produto -> conta,
-- pra escalar sem empilhar coluna nova a cada conta SlickText adicionada.
--
-- Segredos (api_key) continuam só no .env, com a convenção
-- SLICKTEXT_API_KEY_<NAME> (nome da conta sem espaços/maiúsculo, ex: conta
-- 'Velupet' -> SLICKTEXT_API_KEY_VELUPET). O brand_id não é sensível (é só
-- parte do path da API) e fica em slicktext_accounts.
--
-- As colunas antigas em products (slicktext_approved_list_id /
-- slicktext_abandoned_list_id) NÃO são dropadas aqui de propósito -- ficam
-- como deprecated até o cutover no código ser validado em produção. Dropar
-- em uma migration separada depois.
--
-- Dropa slicktext_accounts/slicktext_product_lists antes de recriar: uma
-- versão anterior deste script (schema com code/label/adspower_profile) já
-- rodou nesse banco, então "create table if not exists" sozinho não
-- adicionaria as colunas novas (id/name/account_id). Seguro dropar porque
-- são tabelas novas, sem nenhum outro código/dado dependendo delas ainda.
drop table if exists public.slicktext_product_lists cascade;
drop table if exists public.slicktext_accounts cascade;

create table if not exists public.slicktext_accounts (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    brand_id text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.slicktext_product_lists (
    product_id uuid primary key references public.products(id) on delete cascade,
    account_id uuid not null references public.slicktext_accounts(id),
    approved_list_id text,
    abandoned_list_id text,
    updated_at timestamptz not null default now()
);

create index if not exists idx_slicktext_product_lists_account_id
    on public.slicktext_product_lists (account_id);

create or replace function public.set_slicktext_product_lists_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_slicktext_product_lists_updated_at on public.slicktext_product_lists;
create trigger trg_slicktext_product_lists_updated_at
    before update on public.slicktext_product_lists
    for each row
    execute function public.set_slicktext_product_lists_updated_at();

-- Conta atual (única), a mesma já usada via SLICKTEXT_API_KEY/SLICKTEXT_BRAND_ID
-- no .env. O código continua lendo a api_key dessa conta a partir dessas
-- variáveis (sem sufixo) quando slicktext_accounts.name = 'default' -- só o
-- brand_id passa a vir do banco.
insert into public.slicktext_accounts (name, brand_id)
select 'default', '34641'
where not exists (select 1 from public.slicktext_accounts where name = 'default');

-- Backfill: todo produto que já tinha list_id mapeado migra para a conta 'default'.
insert into public.slicktext_product_lists (product_id, account_id, approved_list_id, abandoned_list_id)
select p.id, a.id, p.slicktext_approved_list_id::text, p.slicktext_abandoned_list_id::text
from public.products p
cross join (select id from public.slicktext_accounts where name = 'default') a
where p.slicktext_approved_list_id is not null
   or p.slicktext_abandoned_list_id is not null
on conflict (product_id) do nothing;

-- Nova conta: Adspower "SMS CONTA 08 - Velupet". A api_key vive na env var
-- SLICKTEXT_API_KEY_VELUPET (ver .env / .env.example).
insert into public.slicktext_accounts (name, brand_id)
select 'Velupet', 'b35016'
where not exists (select 1 from public.slicktext_accounts where name = 'Velupet');

-- Produtos que passam a usar a conta Velupet (sobrescreve o mapeamento
-- 'default' feito pelo backfill acima, para estes 5 produtos).
insert into public.slicktext_product_lists (product_id, account_id, approved_list_id, abandoned_list_id)
select p.id, a.id, v.approved_list_id, v.abandoned_list_id
from public.products p
join (values
    ('BreathEaseX',   '162334', '162335'),
    ('NervoLyn',      '162336', '162337'),
    ('NailsCleanPro', '162338', '162340'),
    ('AudiLeaf',      '162341', '162342'),
    ('Prostafense',   '162343', '162344')
) as v(name, approved_list_id, abandoned_list_id) on v.name = p.name
cross join (select id from public.slicktext_accounts where name = 'Velupet') a
on conflict (product_id) do update
    set account_id = excluded.account_id,
        approved_list_id = excluded.approved_list_id,
        abandoned_list_id = excluded.abandoned_list_id,
        updated_at = now();
