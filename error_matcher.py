import re


class ErrorMatcherError(Exception):
    """自定义错误匹配异常类，用于触发ComfyUI系统报错"""
    __module__ = ''  # 隐藏模块路径，避免在traceback中显示完整路径
    
    def __init__(self, error_code, error_message):
        self.error_code = error_code
        self.error_message = error_message
        # 不调用super().__init__，避免显示文件路径信息
        self.args = (error_code, error_message)
        
        # 创建匿名类，隐藏类名，让traceback只显示错误消息
        error_msg = f"错误代码-{self.error_code}:{self.error_message}"
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
        return f"错误代码-{self.error_code}:{self.error_message}"
    
    def __repr__(self):
        """返回格式化的异常信息，不包含文件路径"""
        return self.__str__()


class ErrorMatcherNode:
    """
    错误匹配节点
    用于接收文本信息并根据配置的错误规则进行匹配和报错
    """
    
    def __init__(self):
        pass
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "input_text1": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "输入要检测的文本内容1"
                }),
                "input_text2": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "输入要检测的文本内容2"
                }),
                "input_text3": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "输入要检测的文本内容3"
                }),
            },
            "optional": {
                "error_rules": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "错误匹配规则，每行格式：\"接收文本\":\"错误代码\":\"错误信息\"\n例如：\"错误啦\":\"404\":\"系统出错，请重试\""
                }),
                "fuzzy_match": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "开启后使用模糊匹配（关键词匹配）"
                }),
                "system_error": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "开启后使用ComfyUI系统报错，关闭则仅输出错误信息"
                }),
            }
        }
    
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("error_code", "error_message")
    FUNCTION = "match_error"
    CATEGORY = "AIxIA_nodes_tools"
    
    def match_error(self, input_text1, input_text2, input_text3, error_rules="", fuzzy_match=False, system_error=True):
        """
        匹配错误并返回或报错
        
        Args:
            input_text1: 输入的文本1
            input_text2: 输入的文本2
            input_text3: 输入的文本3
            error_rules: 错误规则配置（多行）
            fuzzy_match: 是否使用模糊匹配
            system_error: 是否使用系统报错
            
        Returns:
            tuple: (error_code, error_message)
        """
        # 如果没有配置错误规则
        if not error_rules.strip():
            return ("0", "无错误")
        
        # 解析错误规则 - 使用双引号包裹的格式
        # 格式："接收文本":"错误代码":"错误信息"
        rules = []
        for line in error_rules.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # 使用正则表达式匹配双引号内的内容
            # 匹配格式："...":"...":"..."
            pattern = r'\s*"([^"]*)"\s*:\s*"([^"]*)"\s*:\s*"([^"]*)"\s*'
            match = re.match(pattern, line)
            
            if not match:
                raise Exception("报错配置格式错误：每行应为 \"接收文本\":\"错误代码\":\"错误信息\"，例如：\"错误啦\":\"404\":\"系统出错，请重试\"")
            
            match_text = match.group(1)
            error_code = match.group(2)
            error_msg = match.group(3)
            
            rules.append({
                'match': match_text,
                'code': error_code,
                'message': error_msg
            })
        
        # 如果没有有效规则
        if not rules:
            return ("0", "无错误")
        
        # 收集所有输入的文本
        inputs = [input_text1, input_text2, input_text3]
        
        # 检查是否有任何输入为空
        if all(not text or not text.strip() for text in inputs):
            if system_error:
                raise ErrorMatcherError("404", "无输入信息")
            return ("404", "无输入信息")
        
        # 遍历每个输入文本，检查是否匹配任何规则
        for input_text in inputs:
            input_text = input_text.strip() if input_text else ""
            if not input_text:
                continue
                
            # 对当前输入，检查是否匹配任何规则
            for rule in rules:
                matched = False
                
                if fuzzy_match:
                    # 模糊匹配：检查关键词是否在输入文本中
                    matched = rule['match'] in input_text
                else:
                    # 精确匹配：完全相等
                    matched = (rule['match'] == input_text)
                
                if matched:
                    error_code = rule['code']
                    error_message = rule['message']
                    
                    if system_error:
                        raise ErrorMatcherError(error_code, error_message)
                    else:
                        return (error_code, error_message)
        
        # 没有匹配到任何规则
        return ("0", "无错误")


# 导出节点类
__all__ = ['ErrorMatcherNode']

