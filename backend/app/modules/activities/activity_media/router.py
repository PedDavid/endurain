"""API routes for activity media uploads and management.

Thin HTTP adapter: authorization, storage naming and file cleanup live in
:mod:`modules.activities.activity_media.service`.
"""

from collections.abc import Callable
from typing import Annotated

import jasil.providers as platform_providers
from fastapi import APIRouter, Depends, Request, Security, UploadFile, status
from sqlalchemy.orm import Session

import core.database as core_database
import core.dependencies as core_dependencies
import core.file_uploads as core_file_uploads
import core.rate_limit as core_rate_limit
import modules.activities.activity_media.dependencies as activities_media_dependencies
import modules.activities.activity_media.schema as activity_media_schema
import modules.activities.activity_media.service as activity_media_service
import modules.auth.dependencies as auth_dependencies

# Define the API router
router = APIRouter()


@router.get(
    "/media",
    response_model=list[activity_media_schema.ActivityMedia],
)
def read_activities_media_user(
    activity_id: int,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["activities:read"])],
    token_user_id: Annotated[
        int,
        Depends(auth_dependencies.get_sub_from_access_token),
    ],
    db: Annotated[
        Session,
        Depends(core_database.get_db),
    ],
) -> list[activity_media_schema.ActivityMedia]:
    """
    Retrieve activity media records for an activity owned by the user.

    Returns an empty list when the activity has no media or is not accessible to
    the caller — previously ``200 null``, which made a collection endpoint answer
    with a scalar and forced every client to null-check it.
    """
    return activity_media_service.list_activity_media(activity_id, token_user_id, db)


@router.post(
    "/media",
    response_model=activity_media_schema.ActivityMedia,
    status_code=status.HTTP_201_CREATED,
)
@core_rate_limit.limiter.limit(core_rate_limit.UPLOAD)
def upload_media(
    request: Request,
    file: UploadFile,
    activity_id: int,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["activities:write"]),
    ],
    token_user_id: Annotated[
        int,
        Depends(auth_dependencies.get_sub_from_access_token),
    ],
    db: Annotated[
        Session,
        Depends(core_database.get_db),
    ],
    storage: Annotated[
        platform_providers.StorageProvider,
        Depends(core_dependencies.get_storage),
    ],
) -> activity_media_schema.ActivityMedia:
    """
    Upload an image file to associate with an activity.

    The file is validated by magic number (not extension) and size limits via
    the centralized image upload helper before the ownership check, stored through the platform
    ``StorageProvider`` under a server-generated key, and registered in the
    database. The response carries a signed URL; the blob itself has no public
    path.

    Rate limited on the ``UPLOAD`` tier: it is an authenticated endpoint that
    writes caller-supplied bytes to storage, so without a cap a single account
    can fill the media volume.

    Not found when the activity is not the caller's, rejected when the extension
    is unsupported, and a conflict when a media with the same path already exists.
    """
    # The route is synchronous and runs on a worker thread, so the blocking read
    # never touches the event loop.
    data = core_file_uploads.read_validated_upload_sync(file, kind=core_file_uploads.UploadKind.IMAGE)
    return activity_media_service.store_activity_media(activity_id, token_user_id, file.filename, data, db, storage)


@router.delete(
    "/media/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def delete_activity_media(
    request: Request,
    activity_id: int,
    media_id: int,
    _validate_id: Annotated[Callable, Depends(activities_media_dependencies.validate_media_id)],
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["activities:write"])],
    token_user_id: Annotated[
        int,
        Depends(auth_dependencies.get_sub_from_access_token),
    ],
    db: Annotated[
        Session,
        Depends(core_database.get_db),
    ],
    storage: Annotated[
        platform_providers.StorageProvider,
        Depends(core_dependencies.get_storage),
    ],
) -> None:
    """
    Delete an activity media record and remove its stored blob.

    Not found when the media is missing, does not belong to this activity, or its
    activity is not the caller's.
    """
    activity_media_service.delete_activity_media(activity_id, media_id, token_user_id, db, storage)
