# OSS Upload Node for ComfyUI

一个目录式自定义节点，提供将图片/音频/视频上传到阿里云 OSS 并返回可访问地址的能力。

## 安装
- 将整个文件夹 `comfyui-oss-upload` 放入你的 ComfyUI 安装目录下的 `custom_nodes/`。
- 在 ComfyUI 使用的同一 Python 环境安装依赖：
  ```powershell
  pip install oss2 pillow
  ```
- 重启 ComfyUI。日志会打印：`[comfyui-oss-upload] Loaded. Registered nodes: Cloud: OSS Upload From Data`

## 节点
- 显示名：`Cloud: OSS Upload From Data`
- 类名：`OSSUploadFromData`
- 分类：`IO/Cloud`

### 输入参数
必填（手动输入）：
- `endpoint` (STRING)：OSS 访问域名，需包含协议。例如 `https://oss-cn-hangzhou.aliyuncs.com`。
- `access_key_id` (STRING, password)：阿里云 AK。
- `access_key_secret` (STRING, password)：阿里云 SK。
- `bucket_name` (STRING)：桶名。
- `object_prefix` (STRING)：对象前缀，如 `uploads/`（会与日期路径拼接）。
- `use_signed_url` (BOOLEAN)：是否返回签名 URL（私有桶建议开启）。
- `signed_url_expire_seconds` (INT)：签名 URL 有效期（秒）。

可选（连接其一，否则报错）：
- `image` (IMAGE)：上游图像。单图保存为 PNG；多图自动打包为 ZIP。
- `audio` (AUDIO)：上游音频对象。自动转为 WAV（PCM16，采样率优先取对象 `sample_rate/sr`，默认 44100）。
- `video` (BYTES)：上游视频二进制数据，原样上传。

其他可选：
- `file_name` (STRING)：自定义文件名（含扩展名）。
- `mime_type` (STRING)：覆盖 Content-Type（如 `video/mp4`, `audio/wav`）。
- `security_token` (STRING, password)：STS 临时凭证，非必填。

### 输出
- `url` (STRING)：上传后可访问的地址。
  - 私有桶：返回签名 URL（受 `use_signed_url` 与过期时间控制）。
  - 公有读桶：返回公网直链。

### 命名与对象路径
- 最终对象键为：`<object_prefix>/<YYYY>/<MM>/<DD>/<文件名>`。
- 默认文件名：
  - `image`: `image_<uuid>.png`（单图）或 `images_<uuid>.zip`（多图）
  - `audio`: `audio_<uuid>.wav`
  - `video`: `video_<uuid>.mp4`
- 若填写 `file_name`，将使用该名字（建议与实际格式一致，例如 `demo.mp4`）。

### 入口选择优先级
按以下优先顺序选取第一个存在的输入作为上传载荷：
1. `image` → PNG/ZIP
2. `audio` → WAV
3. `video` → 原始 BYTES

若三者均未连接，将报错：`No payload provided. Connect one of: image, audio, or video.`

## 典型用法
- 图片上传：`Load Image.image → OSS Upload.image`，填写 OSS 参数，运行输出 `url`。
- 音频上传（如 VideoHelperSuite 的 `audio` 输出）：`audio → OSS Upload.audio`。
- 视频上传：将上游产出的二进制 `BYTES` 连接到 `video`；建议设置 `file_name=xxx.mp4` 与 `mime_type=video/mp4`。

## 常见问题
- 403 访问被拒：
  - 私有桶需开启 `use_signed_url=true`；
  - 公有读桶检查 ACL 或用签名 URL。
- 域名错误：`endpoint` 需包含协议（`http/https`）。
- 签名失效：适当增大 `signed_url_expire_seconds`，或在过期前访问。

## 安全建议
- 生产环境建议使用 STS（`security_token`），避免长期 AK/SK 暴露在前端。
- 对外分享签名 URL 时控制有效期，并注意权限边界。

---
如需扩展更多类型或自定义命名策略，可在 `oss_node.py` 的实现中调整对应分支逻辑。