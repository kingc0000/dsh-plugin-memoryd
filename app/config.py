"""配置加载：/etc/memoryd/config.env（可选）覆盖内置默认。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PATH = "/etc/memoryd/config.env"


def _load_env_file() -> dict:
    env: dict = {}
    if Path(ENV_PATH).exists():
        for line in Path(ENV_PATH).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


@dataclass
class Settings:
    # 存储 / 服务
    db_path: str = "/var/lib/memoryd/memoryd.db"
    listen_host: str = "127.0.0.1"
    listen_port: int = 3233
    # LLM（OpenAI 兼容 /chat/completions 端点，经 /etc/memoryd/config.env 配置）
    llm_base_url: str = "https://api.example.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    # 行为
    agent_scope_shared: bool = True   # agent 类型经验是否跨设备共享
    secret_redline: bool = True       # 密钥红线：非 device 区拒绝含密钥内容
    search_top_k: int = 100           # BM25 召回上限

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        # 优先级：MEMORYD_* 环境变量 > /etc/memoryd/config.env > 内置默认
        env: dict = _load_env_file()
        for field_name in cls.__dataclass_fields__:
            key = field_name.upper()
            env_val = os.environ.get(f"MEMORYD_{key}")
            if env_val is not None:
                env[key] = env_val
        for field_name in cls.__dataclass_fields__:
            key = field_name.upper()
            if key in env:
                val: str = env[key]
                current = getattr(s, field_name)
                if isinstance(current, bool):
                    setattr(s, field_name, val.lower() in ("1", "true", "yes", "on"))
                elif isinstance(current, int):
                    setattr(s, field_name, int(val))
                elif isinstance(current, str):
                    setattr(s, field_name, val)
        # LLM key 兜底（常用环境变量名）
        if not s.llm_api_key:
            s.llm_api_key = os.environ.get("LLM_API_KEY", "")
        return s


settings = Settings.load()