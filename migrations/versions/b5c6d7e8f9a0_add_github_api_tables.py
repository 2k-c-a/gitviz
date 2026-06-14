"""add github api tables

Revision ID: b5c6d7e8f9a0
Revises: f9a0b1c2d3e4
Create Date: 2026-06-11 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, Sequence[str], None] = 'f9a0b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pull_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('github_pr_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('author_contributor_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(), nullable=True),
        sa.Column('base_branch', sa.String(), nullable=True),
        sa.Column('head_branch', sa.String(), nullable=True),
        sa.Column('merged_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('changed_file_count', sa.Integer(), nullable=True),
        sa.Column('is_large_pr', sa.Boolean(), nullable=True),
        sa.Column('merged_commit_sha', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id']),
        sa.ForeignKeyConstraint(['author_contributor_id'], ['contributors.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repository_id', 'github_pr_number', name='uq_pull_requests_repo_pr_number'),
    )
    op.create_index(op.f('ix_pull_requests_id'), 'pull_requests', ['id'], unique=False)

    op.create_table(
        'pr_commits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pull_request_id', sa.Integer(), nullable=False),
        sa.Column('commit_id', sa.Integer(), nullable=True),
        sa.Column('commit_sha', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['pull_request_id'], ['pull_requests.id']),
        sa.ForeignKeyConstraint(['commit_id'], ['commits.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pull_request_id', 'commit_sha', name='uq_pr_commits_pr_sha'),
    )
    op.create_index(op.f('ix_pr_commits_id'), 'pr_commits', ['id'], unique=False)

    op.create_table(
        'pr_reviews',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pull_request_id', sa.Integer(), nullable=False),
        sa.Column('reviewer_login', sa.String(), nullable=True),
        sa.Column('reviewer_contributor_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(), nullable=True),
        sa.Column('submitted_at', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['pull_request_id'], ['pull_requests.id']),
        sa.ForeignKeyConstraint(['reviewer_contributor_id'], ['contributors.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pr_reviews_id'), 'pr_reviews', ['id'], unique=False)

    op.create_table(
        'pull_request_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pull_request_id', sa.Integer(), nullable=False),
        sa.Column('file_id', sa.Integer(), nullable=True),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('additions', sa.Integer(), nullable=True),
        sa.Column('deletions', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['pull_request_id'], ['pull_requests.id']),
        sa.ForeignKeyConstraint(['file_id'], ['files.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pull_request_id', 'filename', name='uq_pull_request_files_pr_filename'),
    )
    op.create_index(op.f('ix_pull_request_files_id'), 'pull_request_files', ['id'], unique=False)

    op.create_table(
        'releases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('tag_name', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('base_commit_sha', sa.String(), nullable=True),
        sa.Column('is_prerelease', sa.Boolean(), nullable=True),
        sa.Column('published_at', sa.String(), nullable=True),
        sa.Column('release_type', sa.String(), nullable=True),
        sa.Column('commit_count', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repository_id', 'tag_name', name='uq_releases_repo_tag'),
    )
    op.create_index(op.f('ix_releases_id'), 'releases', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_releases_id'), table_name='releases')
    op.drop_table('releases')
    op.drop_index(op.f('ix_pull_request_files_id'), table_name='pull_request_files')
    op.drop_table('pull_request_files')
    op.drop_index(op.f('ix_pr_reviews_id'), table_name='pr_reviews')
    op.drop_table('pr_reviews')
    op.drop_index(op.f('ix_pr_commits_id'), table_name='pr_commits')
    op.drop_table('pr_commits')
    op.drop_index(op.f('ix_pull_requests_id'), table_name='pull_requests')
    op.drop_table('pull_requests')
