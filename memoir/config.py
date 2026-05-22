"""运行时读取 Hermes 配置，获取辅助模型 API 信息。

读取 ~/.hermes/config.yaml 中 auxiliary.amsm.provider 字段，
在 custom_providers 列表中匹配对应条目，获取 base_url、model、api_key_env。

不缓存、不硬编码任何模型名/API地址/端口号。
每次 initialize 钩子执行时重新读取。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml


def load_hermes_config(hermes_home: Optional[str] = None) -> Dict[str, Any]:
    """动态读取 Hermes 配置，返回 AMSM 所需的辅助模型 API 配置。

    Parameters
    ----------
    hermes_home : str, optional
        Hermes 主目录路径。若未传入则使用 ``~/.hermes``。

    Returns
    -------
    dict
        包含以下键:
        - ``base_url`` (str): 模型 API 的 base URL
        - ``model`` (str): 模型名称
        - ``api_key`` (str): API Key（从环境变量读取，若无则为空字符串）
        - ``timeout`` (int): 超时秒数
    """
    if not hermes_home:
        hermes_home = os.path.expanduser("~/.hermes")
    
    config_path = Path(hermes_home) / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Hermes config not found at: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    # 读取 auxiliary.amsm.provider 和 timeout
    amsm_config = config_data.get("auxiliary", {}).get("amsm", {})
    provider_str = amsm_config.get("provider", "")
    timeout = amsm_config.get("timeout", 120)

    if not provider_str:
        # 降级使用 default_aux_config
        provider_str = config_data.get("default_aux_config", {}).get("provider", "")
        
    if not provider_str:
        raise ValueError("No amsm provider or default_aux_config.provider configured in Hermes config.")

    # provider_str 通常形如 "custom:SN2-6.7"
    if provider_str.startswith("custom:"):
        target_name = provider_str.split("custom:", 1)[1]
    else:
        target_name = provider_str

    # 在 custom_providers 中匹配
    custom_providers = config_data.get("custom_providers", [])
    target_provider = None
    for cp in custom_providers:
        if cp.get("name") == target_name:
            target_provider = cp
            break
            
    if not target_provider:
        raise ValueError(f"Provider '{target_name}' not found in custom_providers.")

    base_url = target_provider.get("base_url", "")
    model = target_provider.get("model", "")
    api_key_env = target_provider.get("api_key_env", "")
    
    api_key = ""
    if api_key_env:
        api_key = os.environ.get(api_key_env, "")

    return {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "timeout": timeout
    }
