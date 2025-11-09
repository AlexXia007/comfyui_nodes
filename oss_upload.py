import io
import os
from collections.abc import Mapping
import uuid
import datetime
import mimetypes
import zipfile
from typing import Tuple, Optional

import oss2
import torch
import numpy as np
from PIL import Image
import wave


class OSSUploadFromData:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "endpoint": ("STRING", {"default": "", "multiline": False}),
                "access_key_id": ("STRING", {"default": "", "password": True}),
                "access_key_secret": ("STRING", {"default": "", "password": True}),
                "bucket_name": ("STRING", {"default": "", "multiline": False}),
                "object_prefix": ("STRING", {"default": "uploads/"}),
                "use_signed_url": ("BOOLEAN", {"default": True}),
                "signed_url_expire_seconds": ("INT", {"default": 3600, "min": 60, "max": 604800}),
            },
            "optional": {
                "image": ("IMAGE",),                     # 图片
                "audio": ("AUDIO",),                     # 音频对象
                "video": ("VIDEO",),                     # 视频对象（从中提取源文件路径）
                "file_name": ("STRING", {"default": ""}),
                "mime_type": ("STRING", {"default": ""}),
                "security_token": ("STRING", {"default": "", "password": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("url",)
    FUNCTION = "OSS_upload"
    CATEGORY = "AIxIA_nodes_tools"

    def _build_object_key(self, suggested_name: str, prefix: str) -> str:
        today = datetime.datetime.utcnow()
        date_path = f"{today.year:04d}/{today.month:02d}/{today.day:02d}"
        base = suggested_name.strip() or f"file_{uuid.uuid4().hex[:8]}.bin"
        base = base.replace("\\", "/").split("/")[-1]
        key = "/".join(x.strip("/\\") for x in [prefix, date_path, base] if x)
        return key.replace("\\", "/")

    def _img_batch_to_payload(self, image: torch.Tensor) -> Tuple[bytes, str, str]:
        # ComfyUI 的 IMAGE tensor 范围是 0-1，需要标准化到 0-255
        image = image.clamp(0, 1)
        batch = image.shape[0] if len(image.shape) == 4 else 1
        if batch == 1:
            arr = (image[0].cpu().numpy() * 255).astype(np.uint8) if len(image.shape) == 4 else (image.cpu().numpy() * 255).astype(np.uint8)
            
            # 检查通道数并相应处理
            channels = arr.shape[2] if arr.ndim >= 3 else 1
            has_alpha = channels == 4
            
            if has_alpha:
                # RGBA 4通道：保留完整透明度
                pil = Image.fromarray(arr, mode='RGBA')
            elif channels == 3:
                # RGB 3通道：无透明通道
                pil = Image.fromarray(arr, mode='RGB')
            else:
                pil = Image.fromarray(arr)
            
            bio = io.BytesIO()
            # 保存 PNG 时保留完整质量和透明度
            # compress_level=1: 最快压缩但不损失质量
            pil.save(bio, format="PNG", optimize=False, compress_level=1)
            return bio.getvalue(), f"image_{uuid.uuid4().hex[:8]}.png", "image/png"
        else:
            bio = io.BytesIO()
            with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for i in range(batch):
                    arr = (image[i].cpu().numpy() * 255).astype(np.uint8)
                    
                    # 检查通道数并相应处理
                    channels = arr.shape[2] if arr.ndim >= 3 else 1
                    has_alpha = channels == 4
                    
                    if has_alpha:
                        # RGBA 4通道：保留完整透明度
                        pil = Image.fromarray(arr, mode='RGBA')
                    elif channels == 3:
                        # RGB 3通道：无透明通道
                        pil = Image.fromarray(arr, mode='RGB')
                    else:
                        pil = Image.fromarray(arr)
                    
                    f_bio = io.BytesIO()
                    # 保存 PNG 时保留完整质量和透明度
                    pil.save(f_bio, format="PNG", optimize=False, compress_level=1)
                    zf.writestr(f"image_{i+1:04d}.png", f_bio.getvalue())
            return bio.getvalue(), f"images_{uuid.uuid4().hex[:8]}.zip", "application/zip"

    def _audio_input_to_bytes(self, audio: object, file_name: str, mime_type: str) -> Tuple[bytes, str, str]:
        # 0) Already bytes
        if isinstance(audio, (bytes, bytearray)):
            name = file_name.strip() or f"audio_{uuid.uuid4().hex[:8]}.wav"
            mt = mime_type.strip() or (mimetypes.guess_type(name)[0] or "audio/wav")
            return (bytes(audio), name, mt)

        # 1) Try common file path attributes
        potential_path = None
        for attr in ("file", "path", "file_path", "filepath", "audio_path", "filename"):
            if hasattr(audio, attr):
                val = getattr(audio, attr)
                if isinstance(val, str) and os.path.isfile(val):
                    potential_path = val
                    break
        if potential_path is None and isinstance(audio, str) and os.path.isfile(audio):
            potential_path = audio
        if potential_path:
            with open(potential_path, "rb") as f:
                data = f.read()
            name = file_name.strip() or os.path.basename(potential_path)
            mt = mime_type.strip() or (mimetypes.guess_type(name)[0] or "application/octet-stream")
            return data, name, mt

        # 2) Try common export methods to get wav bytes
        for meth in ("to_wav_bytes", "get_wav_bytes"):
            fn = getattr(audio, meth, None)
            if callable(fn):
                try:
                    data = fn()
                    if isinstance(data, (bytes, bytearray)):
                        name = file_name.strip() or f"audio_{uuid.uuid4().hex[:8]}.wav"
                        mt = mime_type.strip() or "audio/wav"
                        return bytes(data), name, mt
                except Exception:
                    pass
        for meth in ("export", "save", "write"):
            fn = getattr(audio, meth, None)
            if callable(fn):
                try:
                    bio = io.BytesIO()
                    try:
                        fn(bio, format="wav")
                    except Exception:
                        fn(bio)
                    data = bio.getvalue()
                    if data:
                        name = file_name.strip() or f"audio_{uuid.uuid4().hex[:8]}.wav"
                        mt = mime_type.strip() or "audio/wav"
                        return data, name, mt
                except Exception:
                    pass

        # 3) Treat as waveform tensor/array
        sr = 44100
        data = None
        if isinstance(audio, Mapping):
            # LazyAudioMap implements Mapping and resolves on first access
            # Fetch sample rate without boolean evaluation on tensors
            for k in ("sample_rate", "sr"):
                try:
                    v = audio.get(k)  # type: ignore[attr-defined]
                    if v is not None:
                        sr = int(v)
                        break
                except Exception:
                    pass
            for k in ("samples", "waveform", "audio"):
                try:
                    v = audio.get(k)  # type: ignore[attr-defined]
                    if v is not None:
                        data = v
                        break
                except Exception:
                    continue
        else:
            data = audio

        # 2.5) Attribute-style containers (e.g., objects with .waveform / .sample_rate)
        if data is audio and not isinstance(audio, (bytes, bytearray)) and not isinstance(audio, Mapping):
            try:
                sr_attr = getattr(audio, "sample_rate", None)
                wf_attr = getattr(audio, "waveform", None)
                if sr_attr is not None and wf_attr is not None:
                    try:
                        sr = int(sr_attr)
                    except Exception:
                        pass
                    data = wf_attr
            except Exception:
                pass

        if isinstance(data, torch.Tensor):
            data_np = data.detach().cpu().numpy()
        else:
            try:
                data_np = np.asarray(data)
            except Exception:
                data_np = None

        if data_np is None or not np.issubdtype(getattr(data_np, "dtype", np.float32), np.number):
            raise RuntimeError("Unsupported AUDIO input: cannot extract waveform or file path from object. Provide a numeric waveform, a valid file path, or an object with export methods.")

        if data_np.ndim == 3 and data_np.shape[0] == 1:
            # [1, C, S] -> [C, S]
            data_np = data_np[0]
        if data_np.ndim == 1:
            data_np = data_np[None, :]
        elif data_np.ndim != 2:
            raise RuntimeError(f"Unsupported audio array shape: {data_np.shape}")

        data_np = data_np.astype(np.float32, copy=False)
        data_np = np.clip(data_np, -1.0, 1.0)
        pcm_i16 = (data_np * 32767.0).astype(np.int16)
        frames = pcm_i16.T.tobytes()

        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(pcm_i16.shape[0])
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(frames)
        name = file_name.strip() or f"audio_{uuid.uuid4().hex[:8]}.wav"
        mt = mime_type.strip() or "audio/wav"
        return bio.getvalue(), name, mt

    def _choose_payload(
        self,
        image: Optional[torch.Tensor],
        audio: Optional[object],
        video: Optional[object],
        file_name: str,
        mime_type: str,
    ) -> Tuple[bytes, str, str]:
        # 1) 图片
        if image is not None:
            return self._img_batch_to_payload(image)
        # 2) 音频 → WAV
        if audio is not None:
            return self._audio_input_to_bytes(audio, file_name, mime_type)
        # 3) VIDEO 对象：尝试从常见属性中取原始文件路径
        if video is not None:
            potential_path = None
            for attr in ("file", "path", "file_path", "filepath", "fullpath", "filename"):
                if hasattr(video, attr):
                    val = getattr(video, attr)
                    if isinstance(val, str) and os.path.isfile(val):
                        potential_path = val
                        break
            if potential_path is None and isinstance(video, str) and os.path.isfile(video):
                potential_path = video
            if potential_path:
                with open(potential_path, "rb") as f:
                    data = f.read()
                name = file_name.strip() or os.path.basename(potential_path)
                mt = mime_type.strip() or (mimetypes.guess_type(name)[0] or "application/octet-stream")
                return data, name, mt
        # 无有效载荷
        raise RuntimeError("No payload provided. Connect one of: image, audio, or video.")

    def _to_public_url(self, endpoint: str, bucket_name: str, object_key: str) -> str:
        scheme = "https"
        ep = endpoint
        if endpoint.startswith("http://"):
            scheme = "http"; ep = endpoint[len("http://"):]
        elif endpoint.startswith("https://"):
            ep = endpoint[len("https://"):]
        return f"{scheme}://{bucket_name}.{ep}/{object_key}"

    def OSS_upload(
        self,
        endpoint: str,
        access_key_id: str,
        access_key_secret: str,
        bucket_name: str,
        object_prefix: str,
        use_signed_url: bool,
        signed_url_expire_seconds: int,
        image: Optional[torch.Tensor] = None,
        audio: Optional[object] = None,
        video: Optional[object] = None,
        file_name: str = "",
        mime_type: str = "",
        security_token: str = "",
    ):
        if not endpoint or not access_key_id or not access_key_secret or not bucket_name:
            raise RuntimeError("Missing required OSS configuration.")

        payload, suggested_name, content_type = self._choose_payload(
            image=image,
            audio=audio,
            video=video,
            file_name=file_name,
            mime_type=mime_type,
        )

        object_key = self._build_object_key(suggested_name, object_prefix)
        auth = oss2.StsAuth(access_key_id, access_key_secret, security_token) if security_token else oss2.Auth(access_key_id, access_key_secret)
        bucket = oss2.Bucket(auth, endpoint, bucket_name)

        headers = {"Content-Type": content_type}
        result = bucket.put_object(object_key, payload, headers=headers)
        if not (200 <= result.status < 300):
            raise RuntimeError(f"Upload failed: status={result.status}")

        url = bucket.sign_url('GET', object_key, signed_url_expire_seconds) if use_signed_url else self._to_public_url(endpoint, bucket_name, object_key)
        return (url,)


