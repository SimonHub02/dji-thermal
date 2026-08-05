# DJI Thermal SDK 热成像解析微服务

基于 Python 3.11、FastAPI、ctypes 和 NumPy 的 DJI R-JPEG 温度解析服务。业务系统只需提供对象存储中的图片 URL，服务会完成下载、校验、SDK 解析、温度统计和缓存。

## 功能

- `POST /api/thermal/analyze`：返回图像尺寸、最高/最低/平均温度及极值坐标。
- `POST /api/thermal/point`：返回指定像素坐标的温度，矩阵索引为 `temperature[y][x]`。
- `POST /api/thermal/region`：返回指定矩形区域的最高/最低/平均温度及极值坐标。
- HTTP 流式下载，支持连接/读取超时、失败重试、指数退避和文件大小限制。
- 进程内 TTL + LRU 缓存；相同 URL 的并发首次请求只下载、解析一次。
- SDK 调用与接口层隔离，确保每个成功创建的 DIRP 句柄最终都会销毁。
- 记录 URL、下载耗时、文件大小、SDK 耗时、分辨率和温度范围。
- Linux Docker 部署，包含 SDK x64 动态库和 `LD_LIBRARY_PATH` 配置。

## 项目结构

```text
thermal-service/
├── app.py
├── api/
│   └── thermal_controller.py
├── config/
│   └── settings.py
├── model/
│   └── thermal_models.py
├── service/
│   ├── http_downloader.py
│   └── thermal_service.py
├── sdk/
│   ├── dji_thermal_sdk.py
│   └── tsdk-core/                  # DJI 官方 SDK
├── tests/
├── config.yaml
├── requirements.txt
├── requirements-dev.txt
└── Dockerfile
```

## 配置

默认读取项目根目录的 `config.yaml`：

```yaml
app:
  host: 0.0.0.0
  port: 8000
  log_level: info

sdk:
  library_path: sdk/tsdk-core/lib/linux/release_x64/libdirp.so
  max_concurrent_calls: 4

download:
  connect_timeout_seconds: 5
  read_timeout_seconds: 30
  retries: 2
  retry_backoff_seconds: 0.5
  max_file_size_mb: 50
  follow_redirects: true

cache:
  max_entries: 128
  ttl_seconds: 3600
```

环境变量：

- `THERMAL_CONFIG_PATH`：指定其他 YAML 配置文件。
- `SDK_LIBRARY_PATH`：覆盖 YAML 中的 SDK 库路径，Docker 默认使用 `/opt/dji/thermal-sdk/libdirp.so`。

`library_path` 为相对路径时，以配置文件所在目录为基准解析。DJI SDK 不只有 `libdirp.so`，`libv_list.ini`、`libv_*.so`、`libMicro*.so` 和 `libexif.so.12` 等依赖必须和它一起部署。

缓存保存在单个服务进程内。多 worker 或多副本部署时，各进程拥有独立缓存；如需跨实例共享，应在业务层增加 Redis 或持久化结果缓存。缓存中包含完整 `float32` 温度矩阵，内存容量可按约 `宽 × 高 × 4 × max_entries` 字节估算。

## 本地启动

生产目标环境为 Python 3.11：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Windows 本地调试时，把 `sdk.library_path` 改为：

```yaml
sdk:
  library_path: sdk/tsdk-core/lib/windows/release_x64/libdirp.dll
```

封装会把 DLL 所在目录加入依赖搜索路径。Windows 配置仅用于开发验证，正式容器仍使用 Linux `.so`。

启动时会加载原生 SDK；路径错误或依赖缺失会直接导致启动失败，避免服务在无法解析图片时仍错误地报告健康。

## API

### 全图分析

```bash
curl -X POST http://localhost:8000/api/thermal/analyze \
  -H 'Content-Type: application/json' \
  -d '{"fileUrl":"http://minio.example.com/bucket/DJI_0001_R.JPG"}'
```

响应示例：

```json
{
  "success": true,
  "fileUrl": "http://minio.example.com/bucket/DJI_0001_R.JPG",
  "width": 640,
  "height": 512,
  "maxTemperature": 56.5,
  "minTemperature": 19.1,
  "averageTemperature": 38.9,
  "maxPoint": {"x": 310, "y": 201},
  "minPoint": {"x": 22, "y": 17}
}
```

极值坐标是热成像温度矩阵自身的像素坐标，因此必须满足 `0 <= x < width`、`0 <= y < height`，并不对应另一张可见光照片的分辨率。

### 点温查询

```bash
curl -X POST http://localhost:8000/api/thermal/point \
  -H 'Content-Type: application/json' \
  -d '{"fileUrl":"http://minio.example.com/bucket/DJI_0001_R.JPG","x":100,"y":200}'
```

```json
{"x":100,"y":200,"temperature":36.5}
```

点温接口与全图接口共享缓存。坐标超界会返回 HTTP 422。

### 区域温度分析

`(x, y)` 是区域左上角，`(x1, y1)` 是区域右下角，两个端点都包含在统计范围内。

```bash
curl -X POST http://localhost:8000/api/thermal/region \
  -H 'Content-Type: application/json' \
  -d '{"fileUrl":"http://minio.example.com/bucket/DJI_0001_R.JPG","x":100,"y":100,"x1":199,"y1":199}'
```

```json
{
  "success": true,
  "fileUrl": "http://minio.example.com/bucket/DJI_0001_R.JPG",
  "x": 100,
  "y": 100,
  "x1": 199,
  "y1": 199,
  "width": 100,
  "height": 100,
  "maxTemperature": 48.6,
  "minTemperature": 25.2,
  "averageTemperature": 34.7,
  "maxPoint": {"x": 153, "y": 172},
  "minPoint": {"x": 108, "y": 115}
}
```

区域极值点使用原图绝对坐标。必须满足 `0 <= x <= x1 < 图片宽度`、`0 <= y <= y1 < 图片高度`；不合法或越界时返回 HTTP 422。区域接口同样复用已经解析的完整温度矩阵，不会重复下载或调用 SDK。

错误响应统一为：

```json
{
  "success": false,
  "error": {
    "code": "INVALID_DJI_RJPEG",
    "message": "file is not a DJI radiometric JPEG"
  }
}
```

主要状态码：下载上游失败 `502`、文件过大 `413`、非 DJI R-JPEG 或坐标越界 `422`、SDK 不可用 `503`、SDK 运行错误 `500`。请求字段校验错误使用 FastAPI 默认的 `422` 响应。

接口文档启动后可访问：

- Swagger UI：`http://localhost:8000/docs`
- OpenAPI JSON：`http://localhost:8000/openapi.json`
- 健康检查：`GET http://localhost:8000/health`

## Docker 部署

从项目根目录构建：

```bash
docker build -t thermal-service:1.0.0 .
docker run --rm -p 8000:8000 thermal-service:1.0.0
```

使用外部配置文件：

```bash
docker run --rm -p 8000:8000 \
  -e THERMAL_CONFIG_PATH=/app/runtime-config.yaml \
  -v "$PWD/config.yaml:/app/runtime-config.yaml:ro" \
  thermal-service:1.0.0
```

镜像复制 `sdk/tsdk-core/lib/linux/release_x64/` 下的整套依赖到 `/opt/dji/thermal-sdk`，并配置：

```text
LD_LIBRARY_PATH=/opt/dji/thermal-sdk
SDK_LIBRARY_PATH=/opt/dji/thermal-sdk/libdirp.so
```

DJI 提供的 SDK Linux 二进制仅支持对应 CPU 架构。当前 Dockerfile 是 `linux/amd64`/x64 方案，不可直接在 ARM64 容器中运行。

## 测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

测试覆盖统计/坐标语义、缓存、下载重试、大小限制、API JSON 契约和配置路径解析。仓库内 `sdk/dataset` 的样例图片可用于部署后的真实 SDK 验收。

## 实现说明

`dirp_measure()` 返回 `width × height × 2` 字节的 `int16` 数据，每个 LSB 表示 0.1°C。封装将其转换为形状为 `(height, width)` 的 NumPy `float32` 数组，再计算统计量。分辨率来自 `dirp_get_rjpeg_resolution()`，没有硬编码为 640×512，因此也能处理 SDK 支持的其他热成像分辨率。
