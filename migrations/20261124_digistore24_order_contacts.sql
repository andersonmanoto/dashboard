-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- ANTES de ativar na DigiStore o webhook "Orders by customers" que aponta pra
-- /digistore24/order-contact/{token} (sem a tabela o endpoint responde 500 e
-- a DigiStore fica retentando; a venda em si não é afetada).
--
-- Telefone das vendas DigiStore24. O S2S postback de venda (fonte da
-- events) não tem placeholder de telefone; o webhook "Orders by customers"
-- tem (phone_no), mas não tem cid/sid/transaction_type, então não substitui
-- o postback. Os dois chegam independentes, com o mesmo order_id:
--
--   webhook primeiro  -> contato fica aqui; o EventProcessor, ao gravar a
--                        venda, lê o telefone daqui.
--   postback primeiro -> a venda grava sem telefone; o webhook, ao chegar,
--                        grava aqui e faz UPDATE na events pelo order_id.
--   os dois juntos    -> o sync do SlickText (que espera o telefone por até
--                        30 min) procura aqui pelo order_id e corrige a events.

create table if not exists public.digistore24_order_contacts (
  order_id text primary key,
  email text,
  phone text,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.digistore24_order_contacts enable row level security;
