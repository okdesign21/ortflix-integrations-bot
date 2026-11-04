"""Pydantic models for Ortflix Telegram Bot."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class WebhookModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class MediaInfo(WebhookModel):
    media_type: Optional[str] = Field(None, alias="media_type")
    tmdb_id: Optional[int] = Field(None, alias="tmdbId")
    tvdb_id: Optional[int] = Field(None, alias="tvdbId")
    status: Optional[str] = None
    status_4k: Optional[str] = Field(None, alias="status4k")


class RequestInfo(WebhookModel):
    request_id: Optional[str] = Field(None, alias="request_id")
    requested_by_username: Optional[str] = Field("Someone", alias="requestedBy_username")
    requested_by_email: Optional[str] = Field(None, alias="requestedBy_email")
    requested_by_avatar: Optional[str] = Field(None, alias="requestedBy_avatar")


class OverseerrWebhook(WebhookModel):
    notification_type: str
    event: Optional[str] = None
    subject: Optional[str] = "Unknown title"
    message: Optional[str] = None
    image: Optional[str] = None
    media: Optional[MediaInfo] = None
    request: Optional[RequestInfo] = None


class CorruptedFileInfo(WebhookModel):
    path: str
    size: str
    error: str


class MediaIntegrityWebhook(WebhookModel):
    notification_type: str
    summary_message: Optional[str] = "Media integrity check alert"
    count: int = 0
    files: list[CorruptedFileInfo] = Field(default_factory=list)


class RadarrWebhook(WebhookModel):
    event_type: Optional[str] = Field(None, alias="eventType")
    event: Optional[str] = None
    movie: dict[str, Any] = Field(default_factory=dict)
    movie_file: dict[str, Any] = Field(default_factory=dict, alias="movieFile")


class SonarrWebhook(WebhookModel):
    event_type: Optional[str] = Field(None, alias="eventType")
    event: Optional[str] = None
    series: dict[str, Any] = Field(default_factory=dict)
    episode_file: dict[str, Any] = Field(default_factory=dict, alias="episodeFile")


class TautulliWebhook(WebhookModel):
    event: Optional[str] = None
    trigger: Optional[str] = None
    notification_type: Optional[str] = None
