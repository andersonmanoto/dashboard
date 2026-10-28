-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Bug: update_daily_quantity_snapshot estava excluindo vendas com
-- aff_id = '0' (Tiger Offers / tráfego orgânico), igual se faz nos
-- snapshots de *Afiliado* (update_daily_affiliate_*). Mas essa função
-- não é um ranking de afiliado, é o breakdown geral de potes/etapa do
-- funil (snapshot_daily_quantity), e deveria incluir vendas orgânicas —
-- mesmo padrão que update_daily_funnel_snapshot já usa (só exclui
-- HelpGrid, mantém aff_id='0'). Ver docs/FINANCE_CALCULATIONS.md §3.2.
--
-- Efeito do bug: linhas inteiras de (quantity, funnel_stage) sumiam do
-- snapshot quando 100% das vendas daquela combinação eram orgânicas, e
-- total_sales/total_front/gross_revenue ficavam subcontados nas demais.
--
-- Depois de rodar isso, é preciso reprocessar as datas passadas:
--   SELECT public.update_daily_quantity_snapshot('2026-09-13'::date);
--   SELECT public.update_daily_quantity_snapshot('2026-09-14'::date);
--   ... (cron não revisita datas antigas sozinho)

CREATE OR REPLACE FUNCTION public.update_daily_quantity_snapshot(target_date DATE)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO public.snapshot_daily_quantity (
        snapshot_date,
        snapshot_time,
        product_id,
        funnel_number,
        funnel_stage,
        quantity,
        network_id,
        is_upsell,
        gross_revenue,
        total_sales,
        total_front,
        conversion_rate,
        aov,
        updated_at
    )
    WITH sales AS (
        SELECT
            e.product_id,
            COALESCE(c.funnel_number, 1) AS funnel_number,
            COALESCE(c.funnel_stage, 'Purchase') AS funnel_stage,
            COALESCE(c.quantity, 1) AS quantity,
            e.network_id,
            COALESCE(bool_or(e.is_upsell), FALSE) AS is_upsell,
            SUM(e.sale_total) AS gross_revenue,
            COUNT(*) AS total_sales
        FROM public.events e
        LEFT JOIN public.checkouts c ON e.checkout_id = c.id
        WHERE e.event_date = target_date
          -- AQUI: Removido o 'rebill' para focar apenas em novas entradas no funil
          AND e.action_type IN ('SALE', 'neworder', 'payment')
          AND e.is_test IS FALSE
          AND e.product_id IS NOT NULL
          AND e.network_id IS NOT NULL
          AND e.affiliate_id NOT IN (SELECT id FROM public.affiliates WHERE aff_name ILIKE '%helpgrid%')
        GROUP BY 1, 2, 3, 4, 5
    ),
    fronts AS (
        SELECT
            e.product_id,
            COALESCE(c.funnel_number, 1) AS funnel_number,
            e.network_id,
            COUNT(CASE WHEN COALESCE(c.funnel_stage, 'Purchase') = 'Purchase' THEN 1 END) AS total_front
        FROM public.events e
        LEFT JOIN public.checkouts c ON e.checkout_id = c.id
        WHERE e.event_date = target_date
          AND e.action_type IN ('SALE', 'neworder', 'payment')
          AND e.is_test IS FALSE
          AND e.product_id IS NOT NULL
          AND e.network_id IS NOT NULL
          AND e.affiliate_id NOT IN (SELECT id FROM public.affiliates WHERE aff_name ILIKE '%helpgrid%')
        GROUP BY 1, 2, 3
    )
    SELECT
        target_date,
        CURRENT_TIME(2),
        s.product_id,
        s.funnel_number,
        s.funnel_stage,
        s.quantity,
        s.network_id,
        s.is_upsell,
        s.gross_revenue,
        s.total_sales,
        COALESCE(f.total_front, 0) AS total_front,

        -- CONVERSION RATE = Vendas da Etapa / Total de Fronts
        CASE WHEN COALESCE(f.total_front, 0) > 0 THEN ROUND(s.total_sales::numeric / f.total_front, 4) ELSE 0 END AS conversion_rate,

        -- AOV = Faturamento da Etapa / Total de Fronts
        CASE WHEN COALESCE(f.total_front, 0) > 0 THEN ROUND(s.gross_revenue / f.total_front::numeric, 2) ELSE 0 END AS aov,

        NOW() AS updated_at

    FROM sales s
    LEFT JOIN fronts f
        ON s.product_id = f.product_id
       AND s.funnel_number = f.funnel_number
       AND s.network_id = f.network_id

    ON CONFLICT (snapshot_date, product_id, funnel_number, funnel_stage, quantity, network_id)
    DO UPDATE SET
        snapshot_time = EXCLUDED.snapshot_time,
        is_upsell = EXCLUDED.is_upsell,
        gross_revenue = EXCLUDED.gross_revenue,
        total_sales = EXCLUDED.total_sales,
        total_front = EXCLUDED.total_front,
        conversion_rate = EXCLUDED.conversion_rate,
        aov = EXCLUDED.aov,
        updated_at = NOW();
END;
$$;
