"""research v2 subsystem tables (SQLite; vectors live in Astra DB)

Revision ID: 0005_research_v2
Revises: 0004_conversations
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_research_v2"
down_revision = "0004_conversations"
branch_labels = None
depends_on = None


def _ts() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "research_runs_v2",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True, unique=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("depth", sa.String(16), nullable=False, server_default="standard"),
        sa.Column("state", sa.String(32), nullable=False, server_default="created"),
        sa.Column("options", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("budget", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("spent", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_v2_owner", "research_runs_v2", ["owner_id"])
    op.create_index("ix_runs_v2_conv", "research_runs_v2", ["conversation_id"])
    op.create_index("ix_runs_v2_state", "research_runs_v2", ["state"])

    op.create_table(
        "run_stages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "stage", "attempt"),
    )
    op.create_index("ix_run_stages_run", "run_stages", ["run_id"])

    op.create_table(
        "run_events",
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False, server_default="system"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "steer_messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        _ts(),
    )
    op.create_index("ix_steer_run", "steer_messages", ["run_id"])

    op.create_table(
        "questions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.String(32),
                  sa.ForeignKey("questions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("aspect", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="planner"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_questions_run_status", "questions", ["run_id", "status"])

    op.create_table(
        "sources",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False, server_default="html"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_ref", sa.Text(), nullable=True),
        sa.Column("text_ref", sa.Text(), nullable=True),
        sa.Column("source_class", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("score_detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("canonical_url", "content_hash"),
    )
    op.create_index("ix_sources_domain", "sources", ["domain"])

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.String(32),
                  sa.ForeignKey("questions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_id", sa.String(32),
                  sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("span_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("vector_ref", sa.String(64), nullable=True),
        sa.Column("extracted_by", sa.String(64), nullable=False, server_default="worker"),
        _ts(),
    )
    op.create_index("ix_evidence_run", "evidence", ["run_id"])
    op.create_index("ix_evidence_question", "evidence", ["question_id"])
    op.create_index("ix_evidence_source", "evidence", ["source_id"])

    op.create_table(
        "claims",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.String(32),
                  sa.ForeignKey("questions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_key", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("verdict", sa.String(32), nullable=False, server_default="unchecked"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("independent_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verifier_note", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_claims_run", "claims", ["run_id"])

    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.String(32),
                  sa.ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("evidence_id", sa.String(32),
                  sa.ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relation", sa.String(16), nullable=False, server_default="supports"),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("outline", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("components", sa.JSON(), nullable=True),
        sa.Column("quality", sa.JSON(), nullable=False, server_default="{}"),
        _ts(),
    )
    op.create_index("ix_reports_run", "reports", ["run_id"])

    op.create_table(
        "report_citations",
        sa.Column("report_id", sa.String(32),
                  sa.ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("n", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.String(32),
                  sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        _ts(),
    )
    op.create_index("ix_artifacts_run", "artifacts", ["run_id"])

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("model", sa.String(255), nullable=False, server_default=""),
        sa.Column("tier", sa.String(32), nullable=False, server_default="worker"),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=True),
        _ts(),
    )
    op.create_index("ix_llm_calls_run", "llm_calls", ["run_id"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32),
                  sa.ForeignKey("research_runs_v2.id", ondelete="CASCADE"), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False, server_default=""),
        sa.Column("tool", sa.String(48), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts(),
    )
    op.create_index("ix_tool_calls_run", "tool_calls", ["run_id"])

    op.create_table(
        "fetch_cache",
        sa.Column("canonical_url", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.String(32),
                  sa.ForeignKey("sources.id", ondelete="SET NULL"), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_s", sa.Integer(), nullable=False, server_default="86400"),
        sa.Column("etag", sa.String(255), nullable=True),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_kind", "jobs", ["kind"])
    op.create_index("ix_jobs_run", "jobs", ["run_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    for table in (
        "jobs", "fetch_cache", "tool_calls", "llm_calls", "artifacts",
        "report_citations", "reports", "claim_evidence", "claims", "evidence",
        "sources", "questions", "steer_messages", "run_events", "run_stages",
        "research_runs_v2",
    ):
        op.drop_table(table)
