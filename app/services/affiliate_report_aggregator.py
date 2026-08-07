"""
Agrega os snapshots diários (Afiliado / Funil / Pote) vindos do Supabase em
DataFrames prontos para virar planilha. Toda a matemática replica a lógica
documentada em docs/FINANCE_CALCULATIONS.md e validada contra o dashboard:

    Receita Real   = Faturamento - Tax - Shipping
    COGS           = Faturamento * cogs.percentage
    Lucro Líquido  = Faturamento - Comissão - $Taxa Plataforma - COGS - $Refund - $Chargeback
    AOV Bruto      = Faturamento / Volume Front
    AOV Líquido    = Receita Real / Volume Front
    CPA            = Comissão / Volume Front
    ROAS           = Receita Real / Comissão
    Margem         = Lucro Líquido / Faturamento
    % Conv (Pote)  = Vendas do Pote / Vendas totais do Funil
"""

import pandas as pd

VISAO_GERAL_COLUMNS = [
    "Afiliado",
    "ID Afiliado",
    "Volume Front",
    "Volume Funil",
    "Volume Rebills",
    "Receita Rebills",
    "Faturamento",
    "Receita Real",
    "Comissão",
    "$ Taxa Plataforma",
    "% Taxa Plataforma",
    "COGS",
    "% Refund",
    "$ Refund",
    "% Chargeback",
    "$ Chargeback",
    "AOV Bruto",
    "AOV Líquido",
    "CPA",
    "ROAS",
    "Margem",
    "Lucro Líquido",
]

DETALHADO_FUNIL_COLUMNS = [
    "Afiliado",
    "Plataforma",
    "Produto",
    "Funil",
    "Vendas",
    "Faturamento",
    "Ticket Médio",
]

DETALHADO_POTE_COLUMNS = [
    "Afiliado",
    "Plataforma",
    "Produto",
    "Funil",
    "Pote",
    "Vendas",
    "Faturamento",
    "Ticket Médio",
    "% Conv",
]

_NUMERIC_LEVEL1_COLS = [
    "gross_revenue",
    "refund_amount",
    "chargeback_amount",
    "aff_commission_amount",
    "merchant_commission_amount",
    "tax_amount",
    "shipping_cost_amount",
    "total_front",
    "total_sales",
    "total_upsell",
    "total_rebills",
    "rebill_amount",
]

_NUMERIC_LEVEL23_COLS = ["gross_revenue", "total_sales", "total_front"]


def _to_numeric_df(rows: list[dict], numeric_cols: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=numeric_cols)
    for col in numeric_cols:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return (numerator / denominator.replace(0, pd.NA)).fillna(0.0)


def build_visao_geral(
    level1_rows: list[dict],
    affiliates_lookup: dict[str, dict],
    cogs_pct: float,
) -> pd.DataFrame:
    """Nível 1: uma linha por afiliado, somando todos os produtos/plataformas."""
    df = _to_numeric_df(level1_rows, _NUMERIC_LEVEL1_COLS)
    if df.empty:
        return pd.DataFrame(columns=VISAO_GERAL_COLUMNS)

    grouped = df.groupby("affiliate_id", as_index=False)[_NUMERIC_LEVEL1_COLS].sum()

    faturamento = grouped["gross_revenue"]
    volume_front = grouped["total_front"]
    comissao = grouped["aff_commission_amount"]
    taxa_plataforma = grouped["merchant_commission_amount"]
    refund = grouped["refund_amount"]
    chargeback = grouped["chargeback_amount"]
    cogs = faturamento * cogs_pct
    receita_real = faturamento - grouped["tax_amount"] - grouped["shipping_cost_amount"]
    lucro_liquido = faturamento - comissao - taxa_plataforma - cogs - refund - chargeback

    out = pd.DataFrame(
        {
            "Afiliado": grouped["affiliate_id"].map(
                lambda i: affiliates_lookup.get(i, {}).get("aff_name", "Desconhecido")
            ),
            "ID Afiliado": grouped["affiliate_id"].map(
                lambda i: affiliates_lookup.get(i, {}).get("aff_id", i)
            ),
            "Volume Front": volume_front,
            "Volume Funil": grouped["total_sales"],
            "Volume Rebills": grouped["total_rebills"],
            "Receita Rebills": grouped["rebill_amount"],
            "Faturamento": faturamento,
            "Receita Real": receita_real,
            "Comissão": comissao,
            "$ Taxa Plataforma": taxa_plataforma,
            "% Taxa Plataforma": _safe_div(taxa_plataforma, faturamento),
            "COGS": cogs,
            "% Refund": _safe_div(refund, faturamento),
            "$ Refund": refund,
            "% Chargeback": _safe_div(chargeback, faturamento),
            "$ Chargeback": chargeback,
            "AOV Bruto": _safe_div(faturamento, volume_front),
            "AOV Líquido": _safe_div(receita_real, volume_front),
            "CPA": _safe_div(comissao, volume_front),
            "ROAS": _safe_div(receita_real, comissao),
            "Margem": _safe_div(lucro_liquido, faturamento),
            "Lucro Líquido": lucro_liquido,
        }
    )

    return out.sort_values("Faturamento", ascending=False).reset_index(drop=True)


def build_detalhado_funil(
    level2_rows: list[dict],
    affiliates_lookup: dict[str, dict],
    products_lookup: dict[str, str],
    networks_lookup: dict[str, str],
) -> pd.DataFrame:
    """Nível 2: uma linha por Afiliado + Produto + Plataforma + Funil."""
    df = _to_numeric_df(level2_rows, _NUMERIC_LEVEL23_COLS)
    if df.empty:
        return pd.DataFrame(columns=DETALHADO_FUNIL_COLUMNS)

    group_keys = ["affiliate_id", "product_id", "network_id", "funnel_number"]
    grouped = df.groupby(group_keys, as_index=False)[_NUMERIC_LEVEL23_COLS].sum()

    out = pd.DataFrame(
        {
            "Afiliado": grouped["affiliate_id"].map(
                lambda i: affiliates_lookup.get(i, {}).get("aff_name", "Desconhecido")
            ),
            "Plataforma": grouped["network_id"].map(
                lambda i: networks_lookup.get(i, "Desconhecida")
            ),
            "Produto": grouped["product_id"].map(
                lambda i: products_lookup.get(i, "Desconhecido")
            ),
            "Funil": grouped["funnel_number"].map(lambda n: f"Funil {int(n)}"),
            "Vendas": grouped["total_sales"],
            "Faturamento": grouped["gross_revenue"],
            "Ticket Médio": _safe_div(grouped["gross_revenue"], grouped["total_sales"]),
        }
    )

    return out.sort_values(["Afiliado", "Produto", "Funil"]).reset_index(drop=True)


def build_detalhado_pote(
    level3_rows: list[dict],
    affiliates_lookup: dict[str, dict],
    products_lookup: dict[str, str],
    networks_lookup: dict[str, str],
) -> pd.DataFrame:
    """Nível 3: uma linha por Afiliado + Produto + Funil + Pote (quantidade)."""
    df = _to_numeric_df(level3_rows, _NUMERIC_LEVEL23_COLS)
    if df.empty:
        return pd.DataFrame(columns=DETALHADO_POTE_COLUMNS)

    funnel_keys = ["affiliate_id", "product_id", "network_id", "funnel_number"]
    pote_keys = funnel_keys + ["quantity"]

    pote_grouped = df.groupby(pote_keys, as_index=False)[_NUMERIC_LEVEL23_COLS].sum()
    funnel_totals = df.groupby(funnel_keys, as_index=False)["total_sales"].sum().rename(
        columns={"total_sales": "funnel_total_sales"}
    )
    pote_grouped = pote_grouped.merge(funnel_totals, on=funnel_keys, how="left")

    out = pd.DataFrame(
        {
            "Afiliado": pote_grouped["affiliate_id"].map(
                lambda i: affiliates_lookup.get(i, {}).get("aff_name", "Desconhecido")
            ),
            "Plataforma": pote_grouped["network_id"].map(
                lambda i: networks_lookup.get(i, "Desconhecida")
            ),
            "Produto": pote_grouped["product_id"].map(
                lambda i: products_lookup.get(i, "Desconhecido")
            ),
            "Funil": pote_grouped["funnel_number"].map(lambda n: f"Funil {int(n)}"),
            "Pote": pote_grouped["quantity"].astype(int),
            "Vendas": pote_grouped["total_sales"],
            "Faturamento": pote_grouped["gross_revenue"],
            "Ticket Médio": _safe_div(
                pote_grouped["gross_revenue"], pote_grouped["total_sales"]
            ),
            "% Conv": _safe_div(
                pote_grouped["total_sales"], pote_grouped["funnel_total_sales"]
            ),
        }
    )

    return out.sort_values(["Afiliado", "Produto", "Funil", "Pote"]).reset_index(
        drop=True
    )


def build_kpis(visao_geral_df: pd.DataFrame) -> dict:
    """Totais consolidados do período, usados na aba Resumo/KPIs."""
    if visao_geral_df.empty:
        return {
            "total_afiliados": 0,
            "volume_front_total": 0,
            "volume_funil_total": 0,
            "faturamento_total": 0.0,
            "receita_real_total": 0.0,
            "comissao_total": 0.0,
            "taxa_plataforma_total": 0.0,
            "cogs_total": 0.0,
            "refund_total": 0.0,
            "chargeback_total": 0.0,
            "lucro_liquido_total": 0.0,
            "margem_media": 0.0,
            "roas_medio": 0.0,
        }

    faturamento_total = float(visao_geral_df["Faturamento"].sum())
    receita_real_total = float(visao_geral_df["Receita Real"].sum())
    comissao_total = float(visao_geral_df["Comissão"].sum())
    lucro_liquido_total = float(visao_geral_df["Lucro Líquido"].sum())

    return {
        "total_afiliados": int(visao_geral_df.shape[0]),
        "volume_front_total": int(visao_geral_df["Volume Front"].sum()),
        "volume_funil_total": int(visao_geral_df["Volume Funil"].sum()),
        "faturamento_total": faturamento_total,
        "receita_real_total": receita_real_total,
        "comissao_total": comissao_total,
        "taxa_plataforma_total": float(visao_geral_df["$ Taxa Plataforma"].sum()),
        "cogs_total": float(visao_geral_df["COGS"].sum()),
        "refund_total": float(visao_geral_df["$ Refund"].sum()),
        "chargeback_total": float(visao_geral_df["$ Chargeback"].sum()),
        "lucro_liquido_total": lucro_liquido_total,
        "margem_media": (lucro_liquido_total / faturamento_total)
        if faturamento_total
        else 0.0,
        "roas_medio": (receita_real_total / comissao_total) if comissao_total else 0.0,
    }
