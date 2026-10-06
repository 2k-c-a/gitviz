"""AI 모델 공용 정의.

users는 별도 async Base(app.database)에 있어 같은 metadata에 없다. 복합 FK가 참조할 수 있도록
id만 가진 참조용 Table을 둔다. 실제 users 테이블 생성·변경은 기존 migration이 담당하며
migrations/env.py의 include_object가 이 참조 Table을 autogenerate에서 제외한다.
"""
import uuid

from sqlalchemy import Column, DateTime, Table, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base

users_ref = Table("users", Base.metadata, Column("id", UUID(as_uuid=True), primary_key=True),
                  info={"external": True})

HEX64 = r"^[0-9a-f]{64}$"
SHA = r"^[0-9a-f]{40}([0-9a-f]{24})?$"


def uuid_pk():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                  server_default=text("gen_random_uuid()"))


def created_at():
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at():
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
