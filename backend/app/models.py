import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import EmailStr
from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(ItemBase):
    title: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReviewStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    FINAL = "FINAL"


class DocumentBase(SQLModel):
    original_filename: str = Field(min_length=1, max_length=512)
    content_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = None


class Document(DocumentBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    storage_path: str = Field(min_length=1, max_length=1024)

    jobs: list["ProcessingJob"] = Relationship(
        back_populates="document", cascade_delete=True
    )
    result: Optional["ExtractionResult"] = Relationship(
        back_populates="document", cascade_delete=True
    )


class ProcessingJobBase(SQLModel):
    status: JobStatus = Field(default=JobStatus.QUEUED)
    progress: int = Field(default=0, ge=0, le=100)
    current_stage: str | None = Field(default=None, max_length=255)
    error_message: str | None = Field(default=None, max_length=2000)


class ProcessingJob(ProcessingJobBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="document.id", nullable=False, ondelete="CASCADE"
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    started_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    document: Document | None = Relationship(back_populates="jobs")
    extraction_result: Optional["ExtractionResult"] = Relationship(
        back_populates="job", cascade_delete=True
    )


class ExtractionResultBase(SQLModel):
    extracted_json: dict[str, Any] = Field(sa_column=Column(JSONB), default_factory=dict)
    review_status: ReviewStatus = Field(default=ReviewStatus.DRAFT)


class ExtractionResult(ExtractionResultBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="document.id", nullable=False, ondelete="CASCADE", unique=True
    )
    job_id: uuid.UUID = Field(
        foreign_key="processingjob.id", nullable=False, ondelete="CASCADE"
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    finalized_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    document: Document | None = Relationship(back_populates="result")
    job: ProcessingJob | None = Relationship(back_populates="extraction_result")


class ProcessingJobPublic(ProcessingJobBase):
    id: uuid.UUID
    document_id: uuid.UUID
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None


class ExtractionResultPublic(ExtractionResultBase):
    id: uuid.UUID
    document_id: uuid.UUID
    job_id: uuid.UUID
    finalized_at: datetime | None = None


class ExtractionResultReviewPublic(SQLModel):
    review_status: ReviewStatus


class DocumentListItemPublic(SQLModel):
    id: uuid.UUID
    original_filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    created_at: datetime | None = None
    latest_job: ProcessingJobPublic | None = None
    result: ExtractionResultReviewPublic | None = None


class DocumentDetailPublic(SQLModel):
    id: uuid.UUID
    original_filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    created_at: datetime | None = None
    latest_job: ProcessingJobPublic | None = None
    result: ExtractionResultPublic | None = None


class DocumentsPublic(SQLModel):
    data: list[DocumentListItemPublic]
    count: int


class DocumentsUploadResponse(SQLModel):
    documents: list[DocumentDetailPublic]


class ExtractionResultUpdate(SQLModel):
    extracted_json: dict[str, Any]
