"""Pydantic 请求/响应模型。"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Scope = Literal["user", "project", "device", "agent", "session"]


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: Literal["admin", "device"] = "device"


class DeviceCreateResponse(BaseModel):
    device_id: int
    name: str
    role: str
    token: str  # 仅签发时返回一次
    note: str = "token 只显示这一次，服务端仅存哈希"


class ProjectCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{0,63}$")
    name: str = Field(min_length=1, max_length=128)


class MembershipUpdate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{0,63}$")
    device_id: int
    role: Literal["owner", "member", "readonly"]


class ProposalCreate(BaseModel):
    """设备提交申请：scope 升级 / 加入项目。"""
    type: Literal["scope_upgrade", "project_join"]
    memory_id: Optional[int] = None       # scope_upgrade 必填
    target_scope: str = Field(pattern=r"^(user|agent|project:[a-z0-9][a-z0-9\-]{0,63})$")
    reason: str = Field(default="", max_length=2000)


class ProposalDecision(BaseModel):
    note: str = Field(default="", max_length=500)


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    scope: Scope = "device"
    scope_ref: str = ""
    kind: str = "fact"
    tags: list[str] = []
    source_ref: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    agent_id: str = "root"
    session_id: Optional[str] = None


class MemoryCreateResponse(BaseModel):
    id: int
    scope: str
    scope_ref: str
    version: int
    accepted: bool = True
    note: str = ""


class MemoryUpdate(BaseModel):
    content: Optional[str] = Field(default=None, min_length=1, max_length=20000)
    kind: Optional[str] = None
    tags: Optional[list[str]] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    valid_until: Optional[str] = None


class MemoryPromote(BaseModel):
    to_scope: Scope
    to_scope_ref: str = ""
    reason: str = ""


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    hard: bool = False   # True=物理删除（含 FTS/关联项），默认软删除


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    scopes: Optional[list[str]] = None  # 缺省=该设备全部可读 scope
    limit: int = Field(default=8, ge=1, le=50)
    since: Optional[str] = None
    include_deleted: bool = False
    include_insight: bool = False   # 缺省跳过 insight（聚合条目），显式包含才召回


class SessionPost(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20000)
    agent_id: str = "sub"


class LLMAdjudicate(BaseModel):
    """提取+裁决结果（M3 用）。"""
    action: Literal["ADD", "UPDATE", "NOOP"] = "ADD"
    content: str
    target_scope: Scope = "device"
    scope_ref: str = ""
    kind: str = "fact"
    confidence: float = 0.6
    matches: list[int] = []
    reason: str = ""

class RegisterBody(BaseModel):
    """匿名注册请求：agent 自报名字 + 本地指纹。"""
    name: str = Field(min_length=1, max_length=64)
    fingerprint: str = Field(min_length=16, max_length=128)
    note: str = Field(default="", max_length=500)


class RegisterClaimBody(BaseModel):
    """凭指纹领取 token。"""
    fingerprint: str = Field(min_length=16, max_length=128)
