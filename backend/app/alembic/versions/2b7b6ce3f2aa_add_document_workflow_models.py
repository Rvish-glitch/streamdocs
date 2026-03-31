"""Add document workflow models

Revision ID: 2b7b6ce3f2aa
Revises: fe56fa70289e
Create Date: 2026-03-31

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "2b7b6ce3f2aa"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jobstatus = postgresql.ENUM(
        "QUEUED",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
        name="jobstatus",
        create_type=False,
    )
    reviewstatus = postgresql.ENUM(
        "DRAFT",
        "FINAL",
        name="reviewstatus",
        create_type=False,
    )

    bind = op.get_bind()
    jobstatus.create(bind, checkfirst=True)
    reviewstatus.create(bind, checkfirst=True)

    op.create_table(
        "document",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_document_owner_id", "document", ["owner_id"], unique=False)
    op.create_index(
        "ix_document_created_at", "document", ["created_at"], unique=False
    )

    op.create_table(
        "processingjob",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("status", jobstatus, nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("current_stage", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_processingjob_document_id",
        "processingjob",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_processingjob_created_at",
        "processingjob",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "extractionresult",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("extracted_json", postgresql.JSONB(), nullable=False),
        sa.Column("review_status", reviewstatus, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["processingjob.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("document_id"),
    )


def downgrade() -> None:
    op.drop_table("extractionresult")
    op.drop_index("ix_processingjob_created_at", table_name="processingjob")
    op.drop_index("ix_processingjob_document_id", table_name="processingjob")
    op.drop_table("processingjob")
    op.drop_index("ix_document_created_at", table_name="document")
    op.drop_index("ix_document_owner_id", table_name="document")
    op.drop_table("document")

    bind = op.get_bind()
    sa.Enum("DRAFT", "FINAL", name="reviewstatus").drop(bind, checkfirst=True)
    sa.Enum(
        "QUEUED",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
        name="jobstatus",
    ).drop(bind, checkfirst=True)
