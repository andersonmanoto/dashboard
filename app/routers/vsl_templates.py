import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field, HttpUrl

from app.config import Settings, get_settings
from app.dependencies import verify_redtrack_offers_key
from app.services.vsl_template_service import (
    DEFAULT_PITCH_SECONDS,
    DEFAULT_VTURB_EMBED,
    VslTemplateConfigError,
    VslTemplateConflict,
    VslTemplateError,
    VslTemplateService,
    VslTemplateUploadError,
)

router = APIRouter(tags=["VSL Templates"])


class VslTemplateRequest(BaseModel):
    # Pasta do produto em PVs/ (ex: "TtnHny", "bllsdrs")
    codigo: str = Field(pattern=r"^[A-Za-z0-9_-]{2,40}$")
    # Página DTC de origem (ex: https://titanhoney.com/dtc/)
    dtc_url: HttpUrl
    # Só afetam o preview (o (ap) usa vídeo/pitch do AutoPages). Padrão: o
    # mesmo vídeo e pitch do preview do VapoMend.
    vturb_embed: str = Field(default=DEFAULT_VTURB_EMBED, min_length=20)
    pitch_seconds: int = Field(default=DEFAULT_PITCH_SECONDS, ge=0)
    vturb_speed_script: str = ""
    pagina: str = Field(default="pv1", pattern=r"^pv\d{1,2}$")
    # Sobrescrevem o que vem do <title> da DTC. slogan="" = sem slogan.
    nome: str | None = None
    slogan: str | None = None
    overwrite: bool = False
    # Só extrai/renderiza e devolve o resumo, sem subir nada
    dry_run: bool = False


@router.post(
    "/vsl-templates",
    # Mesma chave do /offers-rt-ap (é ele que gera o kit; isto é pra regerar)
    dependencies=[Depends(verify_redtrack_offers_key)],
)
async def create_vsl_templates(
    payload: VslTemplateRequest,
    settings: Settings = Depends(get_settings),
):
    """
    Gera o kit de VSL 2.9 de um produto a partir da página DTC e sobe no
    psstock via SFTP: `PVs/{codigo}/{pagina}/` (preview, com área de edição
    rápida) e `PVs/{codigo}/{pagina}(ap)/` (template Jinja2 do AutoPages).

    Da DTC vêm nome/slogan, cores, preços dos potes 2b/3b/6b, imagens
    (convertidas pra webp) e os scripts de tracking da BuyGoods. Responde
    409 se as pastas já existirem (a menos que `overwrite=true`) e 422 se a
    DTC fugir do padrão esperado.
    """
    service = VslTemplateService(settings)
    try:
        return await asyncio.to_thread(
            service.generate,
            codigo=payload.codigo,
            pagina=payload.pagina,
            dtc_url=str(payload.dtc_url),
            nome=payload.nome,
            slogan=payload.slogan,
            vturb_embed=payload.vturb_embed,
            vturb_speed_script=payload.vturb_speed_script,
            pitch_seconds=payload.pitch_seconds,
            overwrite=payload.overwrite,
            dry_run=payload.dry_run,
        )
    except VslTemplateConfigError as e:
        logger.error(f"[vsl-templates] {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))
    except VslTemplateUploadError as e:
        logger.error(f"[vsl-templates] {payload.codigo}: {e}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    except VslTemplateConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except VslTemplateError as e:
        logger.warning(f"[vsl-templates] {payload.codigo}: {e}")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
