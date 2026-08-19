-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Cria a tabela widgets: um script embed (script_widget) associado a um
-- product_id + network_id. Unique (network_id, product_id) porque
-- WidgetSyncService faz upsert nesse par (um widget por produto+rede).

create table if not exists public.widgets (
    id uuid primary key default gen_random_uuid(),
    network_id uuid not null references public.networks(id) on delete cascade,
    product_id uuid not null references public.products(id) on delete cascade,
    script_widget text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (network_id, product_id)
);

create index if not exists idx_widgets_product_id on public.widgets (product_id);

create or replace function public.set_widgets_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_widgets_updated_at on public.widgets;
create trigger trg_widgets_updated_at
    before update on public.widgets
    for each row
    execute function public.set_widgets_updated_at();
