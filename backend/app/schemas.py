#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pydantic schemas for API + WebSocket messages.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class Viewport(BaseModel):
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class PageInfo(BaseModel):
    url: str = ""
    title: str = ""
    ready: bool = False
    error: Optional[str] = None


class WSInitMessage(BaseModel):
    type: Literal["init"] = "init"
    info: Optional[PageInfo] = None
    default_url: Optional[str] = None
    viewport: Viewport


class WSNavigateResult(BaseModel):
    type: Literal["navigate_result"] = "navigate_result"
    success: bool


class WSErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    error: str
    detail: Optional[Any] = None


class WSCommandNavigate(BaseModel):
    action: Literal["navigate"]
    url: str = Field(min_length=1)


class WSCommandClick(BaseModel):
    action: Literal["click"]
    x: float
    y: float


class WSCommandType(BaseModel):
    action: Literal["type"]
    text: str


class WSCommandKey(BaseModel):
    action: Literal["key"]
    key: str = Field(min_length=1)


class WSCommandScroll(BaseModel):
    action: Literal["scroll"]
    deltaX: float = 0
    deltaY: float = 0


WSCommand = Annotated[
    Union[WSCommandNavigate, WSCommandClick, WSCommandType, WSCommandKey, WSCommandScroll],
    Field(discriminator="action"),
]


class LogsResponse(BaseModel):
    success: bool
    logs: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 0
    error: Optional[str] = None


