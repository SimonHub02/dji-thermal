from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from service.thermal_service import ThermalAnalysis


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class AnalyzeRequest(APIModel):
    file_url: HttpUrl = Field(alias="fileUrl")


class PointRequest(AnalyzeRequest):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class TemperaturePoint(APIModel):
    x: int
    y: int


class AnalyzeResponse(APIModel):
    success: bool = True
    file_url: str = Field(alias="fileUrl")
    width: int
    height: int
    max_temperature: float = Field(alias="maxTemperature")
    min_temperature: float = Field(alias="minTemperature")
    average_temperature: float = Field(alias="averageTemperature")
    max_point: TemperaturePoint = Field(alias="maxPoint")
    min_point: TemperaturePoint = Field(alias="minPoint")

    @classmethod
    def from_analysis(cls, analysis: ThermalAnalysis) -> "AnalyzeResponse":
        return cls.model_validate(
            {
                "file_url": analysis.file_url,
                "width": analysis.width,
                "height": analysis.height,
                "max_temperature": analysis.max_temperature,
                "min_temperature": analysis.min_temperature,
                "average_temperature": analysis.average_temperature,
                "max_point": TemperaturePoint(x=analysis.max_x, y=analysis.max_y),
                "min_point": TemperaturePoint(x=analysis.min_x, y=analysis.min_y),
            }
        )


class PointResponse(APIModel):
    x: int
    y: int
    temperature: float
