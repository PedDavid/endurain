"""Public, token-gated route that serves an activity's map thumbnail.

This route replaces the public static mount that used to serve thumbnails at a
guessable path. It is intentionally **unauthenticated** (mounted without the
activities auth dependency) so it can be used in an ``<img src>`` tag; the
capability is the signed ``t`` token minted by :mod:`signing` and handed only to
viewers permitted to see the map (visibility masking). A non-owner of a
``hide_map`` activity never receives a token and cannot forge one, so they cannot
fetch the blob — while the activity owner keeps their map.
"""

from typing import Annotated

import jasil.providers as platform_providers
from fastapi import APIRouter, Depends, Query, Response

import core.dependencies as core_dependencies
import modules.activities.activity_thumbnail.service as activity_thumbnail_service

router = APIRouter()

# Browser cache for the (stable, signed) thumbnail URL, in seconds.
_CACHE_MAX_AGE = 3600


@router.get(
    "/{activity_id}/thumbnail",
    responses={200: {"content": {"image/webp": {}}}},
)
def read_activity_thumbnail(
    activity_id: int,
    token: Annotated[str, Query(alias="t", description="Signed thumbnail access token")],
    storage: Annotated[platform_providers.StorageProvider, Depends(core_dependencies.get_storage)],
) -> Response:
    """Serve an activity's map thumbnail when the signed token is valid.

    Args:
        activity_id: The activity whose thumbnail to serve.
        token: The signed access token (``?t=``) minted at serialization time.
        storage: Blob-storage provider.

    Returns:
        The WebP thumbnail bytes.

    Raises:
        NotFoundError: When the token is invalid/forged or no thumbnail exists
            (rendered as a 404).
    """
    data = activity_thumbnail_service.read_activity_thumbnail(activity_id, token, storage)

    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": f"private, max-age={_CACHE_MAX_AGE}"},
    )
