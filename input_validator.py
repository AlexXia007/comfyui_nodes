import re
import os
import urllib.parse
from PIL import Image
import requests
from io import BytesIO
import math


class InputValidationError(Exception):
    """自定义输入验证异常类，用于触发ComfyUI系统报错"""
    __module__ = ''  # 隐藏模块路径，避免在traceback中显示完整路径
    
    def __init__(self, status_code, error_message):
        self.status_code = status_code
        self.error_message = error_message
        # 不调用super().__init__，避免显示文件路径信息
        self.args = (status_code, error_message)
        
        # 创建匿名类，隐藏类名，让traceback只显示错误消息
        error_msg = f"输入限制:{self.status_code},{self.error_message}"
        # 创建一个没有名称的异常类
        AnonymousException = type('', (Exception,), {
            '__module__': '',
            '__str__': lambda self: error_msg,
            '__repr__': lambda self: error_msg
        })
        # 替换实例的类，这样traceback中不会显示类名
        self.__class__ = AnonymousException
    
    def __str__(self):
        """返回格式化的异常信息，不包含文件路径和类名"""
        return f"输入限制:{self.status_code},{self.error_message}"
    
    def __repr__(self):
        """返回格式化的异常信息，不包含文件路径"""
        return self.__str__()


class InputValidatorNode:
    """
    输入限制校验节点
    用于对用户输入的图片和提示词进行各种限制筛选
    """
    
    def __init__(self):
        pass
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                # 主要输入
                "prompt_text": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "输入提示词内容"
                }),
                "image_urls": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "输入图片URL地址，每行一个"
                }),
                
                # 提示词内容限制
                "banned_words": ("STRING", {
                    "default": "",
                    "tooltip": "禁止关键词列表（黑名单），用分号(;)分隔，检测到任意词即失败，如：badword1;badword2"
                }),
                "char_count_limit": ("STRING", {
                    "default": "0,0",
                    "tooltip": "字数限制格式：最小字数,最大字数，如：10,500"
                }),
                "supported_languages": ("STRING", {
                    "default": "",
                    "tooltip": "支持的语种，用逗号(,)分隔，如：zh,en,ja,ko"
                }),
                
                # 图片限制
                "url_encoding": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "是否对中文URL进行编码转换"
                }),
                "image_count_limit": ("STRING", {
                    "default": "0,0",
                    "tooltip": "图片数量限制格式：最小数量,最大数量，如：1,10"
                }),
                "total_size_limit": ("STRING", {
                    "default": "0,0",
                    "tooltip": "图片总大小限制(KB)格式：最小,最大，如：100,10240"
                }),
                "single_size_limit": ("STRING", {
                    "default": "0,0",
                    "tooltip": "单图大小限制(KB)格式：最小,最大，如：10,4096"
                }),
                "long_edge_limit": ("STRING", {
                    "default": "0,0",
                    "tooltip": "长边像素限制格式：最小,最大，如：10,3000"
                }),
                "short_edge_limit": ("STRING", {
                    "default": "0,0",
                    "tooltip": "短边像素限制格式：最小,最大，如：10,3000"
                }),
                "aspect_ratio_limit": ("STRING", {
                    "default": "0,0",
                    "tooltip": "长短边比例限制格式：最小值,最大值，如：0.1,0.9"
                }),
                "fixed_ratios": ("STRING", {
                    "default": "0:0",
                    "tooltip": "固定宽高比限制，用逗号分隔，如：4:3,16:9"
                }),
                "image_formats": ("STRING", {
                    "default": "",
                    "tooltip": "允许的图片格式，用逗号分隔，如：jpg,png,webp"
                }),
                "transparency_check": (["disabled", "only_transparent", "no_transparent"], {
                    "default": "disabled",
                    "tooltip": "透明背景检测选项"
                }),
                
                # 系统报错配置
                "trigger_system_error": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "是否触发ComfyUI系统报错：开启后，输入触发限制时返回非200状态码并抛出系统报错"
                }),
            }
        }
    
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("status_code", "status", "error_message", "image_urls", "prompt_text", "prompt_status", "image_status")
    FUNCTION = "validate"
    CATEGORY = "AIxIA_nodes_tools"
    
    def calculate_char_count(self, text):
        """
        计算文本字符数（英文按0.5字符计算）
        
        Args:
            text: 输入文本
            
        Returns:
            字符数
        """
        count = 0
        for char in text:
            if ord(char) > 127:  # 非ASCII字符（中文等）
                count += 1
            else:  # ASCII字符（英文等）
                count += 0.5
        return count
    
    def validate_banned_words(self, text, banned_words):
        """
        验证提示词是否包含禁止关键词（黑名单模式）
        
        Args:
            text: 提示词文本
            banned_words: 禁止关键词列表（分号分隔），检测到任意关键词即失败
            
        Returns:
            (bool, str): (是否通过, 错误信息)
        """
        if not banned_words or banned_words.strip() == "":
            return True, ""
        
        banned_list = [w.strip() for w in banned_words.split(";") if w.strip()]
        if not banned_list:
            return True, ""
        
        # 检查是否包含禁止关键词（黑名单模式）
        for banned_word in banned_list:
            if banned_word in text:
                return False, f"文本输入包含限制词汇：{banned_word}"
        
        return True, ""
    
    def validate_char_count(self, text, limit_str):
        """
        验证字数限制
        
        Args:
            text: 提示词文本
            limit_str: 限制格式 "最小,最大"（整数，0表示不限制）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        if not limit_str or limit_str == "0,0":
            return True, ""
        
        try:
            min_count, max_count = map(int, limit_str.split(","))
            if min_count == 0 and max_count == 0:
                return True, ""
            
            char_count = self.calculate_char_count(text)
            
            # 最小限制检查
            if min_count > 0 and char_count < min_count:
                return False, f"字数不足，当前{int(char_count)}字符，最少需要{min_count}字符", 302
            
            # 最大限制检查：只有当 max_count > 0 时才进行上限检查，0 表示无上限
            if max_count > 0 and char_count > max_count:
                return False, f"字数超限，当前{int(char_count)}字符，最多允许{max_count}字符", 303
            
            return True, ""
        except Exception as e:
            return False, f"字数限制配置错误：{str(e)}", 417
    
    def detect_language(self, text):
        """
        检测文本语种（简化版，基于字符范围判断）
        
        Args:
            text: 输入文本
            
        Returns:
            语种代码列表，如 ['zh', 'en', 'ja']
        """
        languages = []
        
        # 中文字符范围
        if re.search(r'[\u4e00-\u9fff]', text):
            languages.append('zh')
        
        # 英文字符
        if re.search(r'[a-zA-Z]', text):
            languages.append('en')
        
        # 日文平假名、片假名
        if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', text):
            languages.append('ja')
        
        # 韩文字符
        if re.search(r'[\uAC00-\uD7AF]', text):
            languages.append('ko')
        
        return languages if languages else ['unknown']
    
    def validate_language(self, text, supported_languages):
        """
        验证语种限制
        
        Args:
            text: 提示词文本
            supported_languages: 支持的语种列表（逗号分隔）
            
        Returns:
            (bool, str): (是否通过, 错误信息)
        """
        if not supported_languages or supported_languages.strip() == "":
            return True, ""
        
        supported_list = [lang.strip() for lang in supported_languages.split(",") if lang.strip()]
        if not supported_list:
            return True, ""
        
        detected_languages = self.detect_language(text)
        
        # 检查是否有任意语种匹配
        for lang in detected_languages:
            if lang in supported_list:
                return True, ""
        
        return False, f"提示词语种不符合要求，检测到：{','.join(detected_languages)}，支持：{supported_languages}"
    
    def convert_url_encoding(self, url):
        """
        对URL中的中文字符进行编码转换
        
        Args:
            url: 原始URL
            
        Returns:
            编码后的URL
        """
        parsed = urllib.parse.urlparse(url)
        encoded_path = urllib.parse.quote(parsed.path, safe='/')
        encoded_params = urllib.parse.urlencode(urllib.parse.parse_qs(parsed.query), doseq=True)
        
        return f"{parsed.scheme}://{parsed.netloc}{encoded_path}{'?' + encoded_params if encoded_params else ''}"
    
    def get_image_info(self, url):
        """
        获取图片信息
        
        Args:
            url: 图片URL
            
        Returns:
            dict: 图片信息 {width, height, size, format, has_transparency}
        """
        try:
            response = requests.get(url, timeout=10, stream=True)
            response.raise_for_status()
            
            # 获取图片字节数
            content = response.content
            size_kb = len(content) / 1024
            
            # 读取图片信息
            img = Image.open(BytesIO(content))
            width, height = img.size
            format_name = img.format.lower() if img.format else "unknown"
            
            # 检查是否有透明通道
            has_transparency = img.mode in ('RGBA', 'LA', 'P') and ('transparency' in img.info or img.mode == 'RGBA')
            
            return {
                "width": width,
                "height": height,
                "size_kb": size_kb,
                "format": format_name,
                "has_transparency": has_transparency,
                "url": url
            }
        except Exception as e:
            return {
                "error": str(e),
                "url": url
            }
    
    def validate_image_count(self, image_urls, limit_str):
        """
        验证图片数量限制
        
        Args:
            image_urls: 图片URL列表
            limit_str: 限制格式 "最小,最大"（整数，0表示不限制）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        if not limit_str or limit_str == "0,0":
            return True, ""
        
        try:
            min_count, max_count = map(int, limit_str.split(","))
            if min_count == 0 and max_count == 0:
                return True, ""
            
            count = len(image_urls)
            
            # 最小限制检查
            if min_count > 0 and count < min_count:
                return False, f"图片数量不足，当前{count}张，最少需要{min_count}张", 401
            
            # 最大限制检查：只有当 max_count > 0 时才进行上限检查，0 表示无上限
            if max_count > 0 and count > max_count:
                return False, f"图片数量超限，当前{count}张，最多允许{max_count}张", 402
            
            return True, ""
        except Exception as e:
            return False, f"图片数量限制配置错误：{str(e)}", 417
    
    def validate_total_size(self, image_urls, limit_str):
        """
        验证图片总大小限制
        
        Args:
            image_urls: 图片URL列表
            limit_str: 限制格式 "最小,最大"（整数，KB，0表示不限制）
            
        Returns:
            (bool, str, float, int): (是否通过, 错误信息, 总大小, 错误代码)
        """
        if not limit_str or limit_str == "0,0":
            return True, "", 0
        
        try:
            min_size, max_size = map(int, limit_str.split(","))
            if min_size == 0 and max_size == 0:
                return True, "", 0
            
            total_size = 0
            valid_count = 0
            
            for url in image_urls:
                info = self.get_image_info(url)
                if "error" not in info:
                    total_size += info["size_kb"]
                    valid_count += 1
            
            # 最小限制检查
            if min_size > 0 and total_size < min_size:
                return False, f"图片总大小不足，当前{int(total_size)}KB，最少需要{min_size}KB", total_size, 403
            
            # 最大限制检查：只有当 max_size > 0 时才进行上限检查，0 表示无上限
            if max_size > 0 and total_size > max_size:
                return False, f"图片总大小超限，当前{int(total_size)}KB，最多允许{max_size}KB", total_size, 404
            
            return True, "", total_size
        except Exception as e:
            return False, f"图片总大小限制配置错误：{str(e)}", 0, 417
    
    def validate_single_size(self, size_kb, limit_str):
        """
        验证单图大小限制
        
        Args:
            size_kb: 图片大小(KB)
            limit_str: 限制格式 "最小,最大"（整数，KB，0表示不限制）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        if not limit_str or limit_str == "0,0":
            return True, ""
        
        try:
            min_size, max_size = map(int, limit_str.split(","))
            if min_size == 0 and max_size == 0:
                return True, ""
            
            # 最小限制检查
            if min_size > 0 and size_kb < min_size:
                return False, f"图片大小不足，当前{int(size_kb)}KB，最少需要{min_size}KB", 405
            
            # 最大限制检查：只有当 max_size > 0 时才进行上限检查，0 表示无上限
            if max_size > 0 and size_kb > max_size:
                return False, f"图片大小超限，当前{int(size_kb)}KB，最多允许{max_size}KB", 406
            
            return True, ""
        except Exception as e:
            return False, f"图片大小限制配置错误：{str(e)}", 417
    
    def validate_edge_size(self, width, height, long_edge_limit_str, short_edge_limit_str):
        """
        验证图片长边和短边像素限制
        
        Args:
            width: 图片宽度
            height: 图片高度
            long_edge_limit_str: 长边限制 "最小,最大"（整数，像素，0表示不限制）
            short_edge_limit_str: 短边限制 "最小,最大"（整数，像素，0表示不限制）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        long_edge = max(width, height)
        short_edge = min(width, height)
        
        # 验证长边
        if long_edge_limit_str and long_edge_limit_str != "0,0":
            try:
                min_edge, max_edge = map(int, long_edge_limit_str.split(","))
                # 最小边限制检查
                if min_edge > 0 and long_edge < min_edge:
                    return False, f"长边像素不足，当前{long_edge}px，最少需要{min_edge}px", 407
                # 最大边限制检查：只有当 max_edge > 0 时才进行上限检查，0 表示无上限
                if max_edge > 0 and long_edge > max_edge:
                    return False, f"长边像素超限，当前{long_edge}px，最多允许{max_edge}px", 408
            except Exception as e:
                return False, f"长边限制配置错误：{str(e)}", 417
        
        # 验证短边
        if short_edge_limit_str and short_edge_limit_str != "0,0":
            try:
                min_edge, max_edge = map(int, short_edge_limit_str.split(","))
                # 最小边限制检查
                if min_edge > 0 and short_edge < min_edge:
                    return False, f"短边像素不足，当前{short_edge}px，最少需要{min_edge}px", 409
                # 最大边限制检查：只有当 max_edge > 0 时才进行上限检查，0 表示无上限
                if max_edge > 0 and short_edge > max_edge:
                    return False, f"短边像素超限，当前{short_edge}px，最多允许{max_edge}px", 410
            except Exception as e:
                return False, f"短边限制配置错误：{str(e)}", 417
        
        return True, ""
    
    def validate_aspect_ratio(self, width, height, limit_str):
        """
        验证长短边比例限制
        
        Args:
            width: 图片宽度
            height: 图片高度
            limit_str: 限制格式 "最小值,最大值"（小数，如0.1,0.9，0表示不限制）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        if not limit_str or limit_str == "0,0":
            return True, ""
        
        try:
            min_ratio, max_ratio = map(float, limit_str.split(","))
            if min_ratio == 0 and max_ratio == 0:
                return True, ""
            
            ratio = min(width, height) / max(width, height)
            
            # 最小比例检查
            if min_ratio > 0 and ratio < min_ratio:
                return False, f"长短边比例不足，当前{ratio:.2f}，最少需要{min_ratio}", 411
            
            # 最大比例检查：只有当 max_ratio > 0 时才进行上限检查，0 表示无上限
            if max_ratio > 0 and ratio > max_ratio:
                return False, f"长短边比例超限，当前{ratio:.2f}，最多允许{max_ratio}", 412
            
            return True, ""
        except Exception as e:
            return False, f"长短边比例限制配置错误：{str(e)}", 417
    
    def validate_fixed_ratio(self, width, height, limit_str):
        """
        验证宽高固定比例限制
        
        Args:
            width: 图片宽度
            height: 图片高度
            limit_str: 限制格式 "宽:高"，多个用逗号分隔，如 "4:3,16:9"（字符串）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        if not limit_str or limit_str == "0:0":
            return True, ""
        
        try:
            ratio_list = limit_str.split(",")
            current_ratio = width / height
            
            for ratio_str in ratio_list:
                ratio_str = ratio_str.strip()
                if ":" in ratio_str:
                    w, h = map(float, ratio_str.split(":"))
                    target_ratio = w / h
                    
                    # 允许一定误差
                    if abs(current_ratio - target_ratio) < 0.01:
                        return True, ""
            
            return False, f"图片宽高比例不符合要求，当前{width}:{height}(约{current_ratio:.2f})，要求：{limit_str}", 413
        except Exception as e:
            return False, f"宽高比例限制配置错误：{str(e)}", 417
    
    def validate_image_format(self, format_name, limit_str):
        """
        验证图片格式限制
        
        Args:
            format_name: 图片格式
            limit_str: 允许的格式列表（逗号分隔的字符串）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        if not limit_str or limit_str.strip() == "":
            return True, ""
        
        allowed_formats = [f.strip().lower() for f in limit_str.split(",")]
        
        # jpg和jpeg视为同一格式
        if "jpg" in allowed_formats and format_name == "jpeg":
            return True, ""
        if "jpeg" in allowed_formats and format_name == "jpg":
            return True, ""
        
        if format_name.lower() in allowed_formats:
            return True, ""
        
        return False, f"图片格式不支持，当前{format_name}，支持：{limit_str}", 414
    
    def validate_transparency(self, has_transparency, check_type):
        """
        验证透明背景限制
        
        Args:
            has_transparency: 是否有透明通道
            check_type: 检测类型（disabled/only_transparent/no_transparent）
            
        Returns:
            (bool, str, int): (是否通过, 错误信息, 错误代码)
        """
        if check_type == "disabled":
            return True, ""
        
        if check_type == "only_transparent":
            if has_transparency:
                return True, ""
            else:
                return False, "图片必须为透明背景", 415
        
        if check_type == "no_transparent":
            if not has_transparency:
                return True, ""
            else:
                return False, "图片不允许透明背景", 415
        
        return True, ""
    
    def validate(self, prompt_text="", image_urls="", **kwargs):
        """
        主验证函数
        
        Args:
            prompt_text: 提示词文本（可选）
            image_urls: 图片URL列表（文本，换行分隔，可选）
            **kwargs: 各种限制参数
            
        Returns:
            tuple: (status_code, status, error_message, image_urls, prompt_text, prompt_status, image_status)
        """
        # 初始化状态
        prompt_status = ""
        image_status = ""
        final_status_code = "200"
        final_status = "success"
        final_error_message = ""
        
        # 检查是否至少有一个输入
        has_prompt = prompt_text and prompt_text.strip()
        has_images = image_urls and image_urls.strip()
        
        if not has_prompt and not has_images:
            return ("400", "error", "至少需要输入 prompt_text 或 image_urls 中的一项", "", "", "no_input", "no_input")
        
        # === 验证提示词部分 ===
        prompt_error_found = False
        prompt_error_code = ""
        prompt_error_msg = ""
        
        if has_prompt:
            # 1. 验证提示词关键词
            if kwargs.get("banned_words"):
                valid, error = self.validate_banned_words(prompt_text, kwargs["banned_words"])
                if not valid:
                    prompt_status = "failed"
                    prompt_error_found = True
                    prompt_error_code = "301"
                    prompt_error_msg = error
                    # 更新最终状态
                    if final_status_code == "200":
                        final_status_code = "301"
                        final_status = "error"
                        final_error_message = error
        
            # 2. 验证提示词字数
            if kwargs.get("char_count_limit") and not prompt_error_found:
                result = self.validate_char_count(prompt_text, kwargs["char_count_limit"])
                if isinstance(result, tuple) and len(result) > 2:
                    valid, error, error_code = result
                    if not valid:
                        prompt_status = "failed"
                        prompt_error_found = True
                        prompt_error_code = str(error_code)
                        prompt_error_msg = error
                        # 更新最终状态
                        if final_status_code == "200":
                            final_status_code = str(error_code)
                            final_status = "error"
                            final_error_message = error
                else:
                    valid, error = result
                    if not valid:
                        prompt_status = "failed"
                        prompt_error_found = True
                        prompt_error_code = "302"
                        prompt_error_msg = error
                        # 更新最终状态
                        if final_status_code == "200":
                            final_status_code = "302"
                            final_status = "error"
                            final_error_message = error
            
            # 3. 验证提示词语种
            if kwargs.get("supported_languages") and not prompt_error_found:
                valid, error = self.validate_language(prompt_text, kwargs["supported_languages"])
                if not valid:
                    prompt_status = "failed"
                    prompt_error_found = True
                    prompt_error_code = "304"
                    prompt_error_msg = error
                    # 更新最终状态
                    if final_status_code == "200":
                        final_status_code = "304"
                        final_status = "error"
                        final_error_message = error
            
            if not prompt_error_found:
                prompt_status = "success"
        
        # === 验证图片部分 ===
        image_error_found = False
        url_list = []
        processed_urls = []
        
        if has_images:
            # 解析图片URL列表
            url_list = [url.strip() for url in image_urls.split("\n") if url.strip()]
            
            if not url_list:
                image_status = "failed"
                image_error_found = True
                # 只有当提示词验证通过时才覆盖最终状态
                if not prompt_error_found:
                    final_status_code = "418"
                    final_status = "error"
                    final_error_message = "未检测到有效的图片URL"
            else:
                # 4. 图片URL编码转换
                url_encoding = kwargs.get("url_encoding", True)
                for url in url_list:
                    # 检查是否包含中文字符
                    if url_encoding and any(ord(c) > 127 for c in url):
                        processed_url = self.convert_url_encoding(url)
                    else:
                        processed_url = url
                    processed_urls.append(processed_url)
                
                # 5. 验证图片数量
                if kwargs.get("image_count_limit"):
                    result = self.validate_image_count(url_list, kwargs["image_count_limit"])
                    if isinstance(result, tuple) and len(result) > 2:
                        valid, error, error_code = result
                        if not valid:
                            image_status = "failed"
                            image_error_found = True
                            # 只有当提示词验证通过时才覆盖最终状态
                            if not prompt_error_found:
                                final_status_code = str(error_code)
                                final_status = "error"
                                final_error_message = error
                    else:
                        valid, error = result
                        if not valid:
                            image_status = "failed"
                            image_error_found = True
                            # 只有当提示词验证通过时才覆盖最终状态
                            if not prompt_error_found:
                                final_status_code = "401"
                                final_status = "error"
                                final_error_message = error
                
                # 6. 对每张图片进行单独验证（获取图片信息）
                # 先获取所有图片信息，检查是否有读取失败
                image_info_list = []  # 存储所有图片的信息
                for i, url in enumerate(url_list):
                    # 如果已经发现错误，跳过后续检测
                    if image_error_found:
                        break
                        
                    info = self.get_image_info(url)
                    image_info_list.append(info)
                    
                    # 检查图片读取是否失败
                    if "error" in info:
                        image_status = "failed"
                        image_error_found = True
                        # 只有当提示词验证通过时才覆盖最终状态
                        if not prompt_error_found:
                            final_status_code = "416"
                            final_status = "error"
                            final_error_message = f"第{i+1}张图片读取失败：{info['error']}"
                        break  # 读取失败，停止检测后续图片和后续验证步骤
                
                # 7. 如果图片读取都成功，验证图片总大小
                if not image_error_found and kwargs.get("total_size_limit"):
                    total_size = sum(info.get("size_kb", 0) for info in image_info_list if "error" not in info)
                    min_size, max_size = map(int, kwargs["total_size_limit"].split(","))
                    
                    # 最小限制检查
                    if min_size > 0 and total_size < min_size:
                        image_status = "failed"
                        image_error_found = True
                        if not prompt_error_found:
                            final_status_code = "403"
                            final_status = "error"
                            final_error_message = f"图片总大小不足，当前{int(total_size)}KB，最少需要{min_size}KB"
                    # 最大限制检查：只有当 max_size > 0 时才进行上限检查，0 表示无上限
                    elif max_size > 0 and total_size > max_size:
                        image_status = "failed"
                        image_error_found = True
                        if not prompt_error_found:
                            final_status_code = "404"
                            final_status = "error"
                            final_error_message = f"图片总大小超限，当前{int(total_size)}KB，最多允许{max_size}KB"
                
                # 8. 对每张图片进行单独验证（如果还没有发现错误）
                for i, info in enumerate(image_info_list):
                    # 如果已经发现错误，跳过后续检测
                    if image_error_found:
                        break
                    
                    # 8.1 验证单图大小
                    if kwargs.get("single_size_limit"):
                        result = self.validate_single_size(info["size_kb"], kwargs["single_size_limit"])
                        if isinstance(result, tuple) and len(result) > 2:
                            valid, error, error_code = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = str(error_code)
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break  # 发现错误，停止检测当前图片和后续图片
                        else:
                            valid, error = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = "405"
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break  # 发现错误，停止检测当前图片和后续图片
                    
                    # 8.2 验证长边和短边
                    if kwargs.get("long_edge_limit") or kwargs.get("short_edge_limit"):
                        if image_error_found:
                            break
                        result = self.validate_edge_size(
                            info["width"], 
                            info["height"], 
                            kwargs.get("long_edge_limit", "0,0"),
                            kwargs.get("short_edge_limit", "0,0")
                        )
                        if isinstance(result, tuple) and len(result) > 2:
                            valid, error, error_code = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = str(error_code)
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                        else:
                            valid, error = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = "407"
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                    
                    # 8.3 验证长短边比例
                    if kwargs.get("aspect_ratio_limit"):
                        if image_error_found:
                            break
                        result = self.validate_aspect_ratio(info["width"], info["height"], kwargs["aspect_ratio_limit"])
                        if isinstance(result, tuple) and len(result) > 2:
                            valid, error, error_code = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = str(error_code)
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                        else:
                            valid, error = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = "411"
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                    
                    # 8.4 验证宽高固定比例
                    if kwargs.get("fixed_ratios"):
                        if image_error_found:
                            break
                        result = self.validate_fixed_ratio(info["width"], info["height"], kwargs["fixed_ratios"])
                        if isinstance(result, tuple) and len(result) > 2:
                            valid, error, error_code = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = str(error_code)
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                        else:
                            valid, error = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = "413"
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                    
                    # 8.5 验证图片格式
                    if kwargs.get("image_formats"):
                        if image_error_found:
                            break
                        result = self.validate_image_format(info["format"], kwargs["image_formats"])
                        if isinstance(result, tuple) and len(result) > 2:
                            valid, error, error_code = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = str(error_code)
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                        else:
                            valid, error = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = "414"
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                    
                    # 8.6 验证透明背景
                    if kwargs.get("transparency_check") and kwargs["transparency_check"] != "disabled":
                        if image_error_found:
                            break
                        result = self.validate_transparency(info["has_transparency"], kwargs["transparency_check"])
                        if isinstance(result, tuple) and len(result) > 2:
                            valid, error, error_code = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = str(error_code)
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                        else:
                            valid, error = result
                            if not valid:
                                image_status = "failed"
                                image_error_found = True
                                # 只有当提示词验证通过时才覆盖最终状态
                                if not prompt_error_found:
                                    final_status_code = "415"
                                    final_status = "error"
                                    final_error_message = f"第{i+1}张图片：{error}"
                                break
                
                # 如果所有图片检测都完成且没有错误，标记为成功
                if image_status != "failed":
                    image_status = "success"
        
        # 返回结果
        if not has_prompt:
            prompt_status = "no_input"
        if not has_images:
            image_status = "no_input"
        
        # 构建返回的图片URL
        if has_images and url_list:
            final_image_urls = "\n".join(processed_urls)
        else:
            final_image_urls = image_urls if has_images else ""
        
        # 检查是否触发系统报错
        if kwargs.get("trigger_system_error", False) and final_status_code != "200":
            # 抛出异常，触发ComfyUI系统报错
            raise InputValidationError(final_status_code, final_error_message)
        
        return (final_status_code, final_status, final_error_message, final_image_urls, prompt_text, prompt_status, image_status)

