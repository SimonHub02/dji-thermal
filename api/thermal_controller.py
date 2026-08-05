from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from model.thermal_models import (
    AnalyzeRequest,
    AnalyzeResponse,
    PointRequest,
    PointResponse,
    RegionRequest,
    RegionResponse,
)
from service.thermal_service import ThermalService

router = APIRouter(prefix="/api/thermal", tags=["thermal"])


def get_thermal_service(request: Request) -> ThermalService:
    return request.app.state.thermal_service


ThermalServiceDependency = Annotated[ThermalService, Depends(get_thermal_service)]


@router.post("/analyze", response_model=AnalyzeResponse, response_model_by_alias=True)
async def analyze(
    request: AnalyzeRequest,
    service: ThermalServiceDependency,
) -> AnalyzeResponse:
    analysis = await service.analyze(str(request.file_url))
    return AnalyzeResponse.from_analysis(analysis)


@router.post("/point", response_model=PointResponse, response_model_by_alias=True)
async def point_temperature(
    request: PointRequest,
    service: ThermalServiceDependency,
) -> PointResponse:
    temperature = await service.point_temperature(
        str(request.file_url), request.x, request.y
    )
    return PointResponse(x=request.x, y=request.y, temperature=temperature)


@router.post("/region", response_model=RegionResponse, response_model_by_alias=True)
async def region_temperature(
    request: RegionRequest,
    service: ThermalServiceDependency,
) -> RegionResponse:
    analysis = await service.analyze_region(
        str(request.file_url),
        x=request.x,
        y=request.y,
        x1=request.x1,
        y1=request.y1,
    )
    return RegionResponse.from_analysis(analysis)
