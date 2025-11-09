from .input_validator import InputValidatorNode
from .error_matcher import ErrorMatcherNode
from .oss_upload import OSSUploadFromData

NODE_CLASS_MAPPINGS = {
    "InputValidatorNode": InputValidatorNode,
    "ErrorMatcherNode": ErrorMatcherNode,
    "OSSUploadFromData": OSSUploadFromData,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "InputValidatorNode": "Input Validator Node",
    "ErrorMatcherNode": "Error Matcher Node",
    "OSSUploadFromData": "OSS Upload",
}

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']

