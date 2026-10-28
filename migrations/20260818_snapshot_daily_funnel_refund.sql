-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Adiciona refund_amount/total_refunds em snapshot_daily_funnel, quebrado
-- por funil (product_id, affiliate_id, network_id, funnel_number) — hoje
-- essa tabela não tem NENHUMA coluna de refund (só existe em
-- snapshot_daily_affiliate / snapshot_daily_affiliate_product_network).
--
-- Fonte do refund: public.sales_status (status_type='refund', amount_affected),
-- mesmo padrão já usado em update_daily_affiliate_snapshot. Como sales_status
-- não guarda funnel_number, junta com events (pra pegar
-- order_details->>'external_checkout_code') e daí com checkouts (mesmo join
-- que a query de vendas já faz) pra descobrir o funil daquele pedido.
--
-- Atribuído pela data do refund (status_date), não pela data da venda
-- original — pode ser diferente e é assim que update_daily_affiliate_snapshot
-- já funciona.

alter table public.snapshot_daily_funnel
  add column if not exists refund_amount numeric not null default 0,
  add column if not exists total_refunds integer not null default 0;

CREATE OR REPLACE FUNCTION public.update_daily_funnel_snapshot(target_date DATE)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO public.snapshot_daily_funnel (
        snapshot_date,
        snapshot_time,
        product_id,
        affiliate_id,
        network_id,
        funnel_number,

        -- Valores Monetários (Amounts)
        gross_revenue,
        front_amount,
        up1_amount, dw1_amount,
        up2_amount, dw2_amount,
        up3_amount, dw3_amount,
        up4_amount, dw4_amount,

        -- Contadores (Totals)
        total_front,
        total_up1, total_dw1,
        total_up2, total_dw2,
        total_up3, total_dw3,
        total_up4, total_dw4,

        -- Total de Vendas
        total_sale,

        -- Refund
        refund_amount,
        total_refunds,

        updated_at
    )
    SELECT
        target_date,
        CURRENT_TIME(2),
        COALESCE(base.product_id, ref.product_id),
        COALESCE(base.affiliate_id, ref.affiliate_id),
        COALESCE(base.network_id, ref.network_id),
        CAST(COALESCE(base.funnel_number, ref.funnel_number) AS TEXT),

        -- Somas Monetárias
        COALESCE(base.gross_revenue, 0),
        COALESCE(base.front_amount, 0),
        COALESCE(base.up1_amount, 0), COALESCE(base.dw1_amount, 0),
        COALESCE(base.up2_amount, 0), COALESCE(base.dw2_amount, 0),
        COALESCE(base.up3_amount, 0), COALESCE(base.dw3_amount, 0),
        COALESCE(base.up4_amount, 0), COALESCE(base.dw4_amount, 0),

        -- Totais de Transações
        COALESCE(base.total_front, 0),
        COALESCE(base.total_up1, 0), COALESCE(base.total_dw1, 0),
        COALESCE(base.total_up2, 0), COALESCE(base.total_dw2, 0),
        COALESCE(base.total_up3, 0), COALESCE(base.total_dw3, 0),
        COALESCE(base.total_up4, 0), COALESCE(base.total_dw4, 0),

        -- CÁLCULO DO TOTAL SALE
        (
            COALESCE(base.total_front, 0) +
            COALESCE(base.total_up1, 0) + COALESCE(base.total_dw1, 0) +
            COALESCE(base.total_up2, 0) + COALESCE(base.total_dw2, 0) +
            COALESCE(base.total_up3, 0) + COALESCE(base.total_dw3, 0) +
            COALESCE(base.total_up4, 0) + COALESCE(base.total_dw4, 0)
        ) as total_sale,

        COALESCE(ref.refund_amount, 0),
        COALESCE(ref.total_refunds, 0),

        NOW()

    FROM (
        SELECT
            e.product_id,
            e.affiliate_id,
            MAX(e.network_id::text)::uuid as network_id,

            COALESCE(c.funnel_number, 0) as funnel_number,

            SUM(e.sale_total) as gross_revenue,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Purchase' THEN e.sale_total ELSE 0 END) as front_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Up1' THEN e.sale_total ELSE 0 END) as up1_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Dw1' THEN e.sale_total ELSE 0 END) as dw1_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Up2' THEN e.sale_total ELSE 0 END) as up2_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Dw2' THEN e.sale_total ELSE 0 END) as dw2_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Up3' THEN e.sale_total ELSE 0 END) as up3_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Dw3' THEN e.sale_total ELSE 0 END) as dw3_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Up4' THEN e.sale_total ELSE 0 END) as up4_amount,
            SUM(CASE WHEN c.funnel_stage ILIKE 'Dw4' THEN e.sale_total ELSE 0 END) as dw4_amount,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Purchase' THEN 1 END) as total_front,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Up1' THEN 1 END) as total_up1,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Dw1' THEN 1 END) as total_dw1,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Up2' THEN 1 END) as total_up2,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Dw2' THEN 1 END) as total_dw2,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Up3' THEN 1 END) as total_up3,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Dw3' THEN 1 END) as total_dw3,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Up4' THEN 1 END) as total_up4,
            COUNT(CASE WHEN c.funnel_stage ILIKE 'Dw4' THEN 1 END) as total_dw4

        FROM public.events e

        LEFT JOIN public.checkouts c
            ON (e.order_details->>'external_checkout_code') = c.checkout_code
            AND e.product_id = c.product_id

        LEFT JOIN public.affiliates a ON e.affiliate_id = a.id
        WHERE e.event_date = target_date
          AND e.action_type IN ('SALE', 'neworder', 'payment')
          AND e.is_test IS FALSE
          AND e.product_id IS NOT NULL
          AND (a.aff_name NOT ILIKE '%HelpGrid%' OR a.aff_name IS NULL)

        GROUP BY e.product_id, e.affiliate_id, 4
    ) base

    FULL OUTER JOIN (
        SELECT
            s.product_id,
            s.affiliate_id,
            MAX(s.network_id::text)::uuid as network_id,

            COALESCE(c.funnel_number, 0) as funnel_number,

            SUM(s.amount_affected) as refund_amount,
            COUNT(*) as total_refunds

        FROM public.sales_status s

        JOIN public.events e ON s.event_id = e.id

        LEFT JOIN public.checkouts c
            ON (e.order_details->>'external_checkout_code') = c.checkout_code
            AND s.product_id = c.product_id

        LEFT JOIN public.affiliates a ON s.affiliate_id = a.id

        WHERE s.status_date = target_date
          AND s.status_type = 'refund'
          AND (e.is_test IS FALSE OR e.is_test IS NULL)
          AND s.product_id IS NOT NULL
          AND (a.aff_name NOT ILIKE '%HelpGrid%' OR a.aff_name IS NULL)

        GROUP BY s.product_id, s.affiliate_id, 4
    ) ref
        ON base.product_id = ref.product_id
       AND base.affiliate_id = ref.affiliate_id
       AND base.funnel_number = ref.funnel_number

    WHERE (COALESCE(base.gross_revenue, 0) > 0 OR COALESCE(ref.refund_amount, 0) > 0)
      AND COALESCE(base.funnel_number, ref.funnel_number) != 0

    ON CONFLICT (snapshot_date, product_id, affiliate_id, network_id, funnel_number) DO UPDATE SET
        snapshot_time = EXCLUDED.snapshot_time,
        gross_revenue = EXCLUDED.gross_revenue,
        front_amount = EXCLUDED.front_amount,
        up1_amount = EXCLUDED.up1_amount, dw1_amount = EXCLUDED.dw1_amount,
        up2_amount = EXCLUDED.up2_amount, dw2_amount = EXCLUDED.dw2_amount,
        up3_amount = EXCLUDED.up3_amount, dw3_amount = EXCLUDED.dw3_amount,
        up4_amount = EXCLUDED.up4_amount, dw4_amount = EXCLUDED.dw4_amount,
        total_front = EXCLUDED.total_front,
        total_up1 = EXCLUDED.total_up1, total_dw1 = EXCLUDED.total_dw1,
        total_up2 = EXCLUDED.total_up2, total_dw2 = EXCLUDED.total_dw2,
        total_up3 = EXCLUDED.total_up3, total_dw3 = EXCLUDED.total_dw3,
        total_up4 = EXCLUDED.total_up4, total_dw4 = EXCLUDED.total_dw4,
        total_sale = EXCLUDED.total_sale,
        refund_amount = EXCLUDED.refund_amount,
        total_refunds = EXCLUDED.total_refunds,
        updated_at = NOW();
END;
$$;
