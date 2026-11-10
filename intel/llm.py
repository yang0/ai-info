"""OpenAI-compatible Chat Completions transport. Credentials stay server-side."""
import asyncio
import json
import os
import re
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ModelConfig(BaseModel):
    base_url: str = Field(default='', max_length=2000)
    model: str = Field(default='', max_length=200)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    timeout_seconds: int = Field(default=60, ge=5, le=180)
    max_tokens: int = Field(default=800, ge=100, le=4000)
    json_mode: bool = False

    @field_validator('base_url')
    @classmethod
    def base_valid(cls, value):
        value = value.strip().rstrip('/')
        if not value:
            return ''
        p = urlparse(value)
        if (not p.hostname or p.scheme not in ('http', 'https') or p.username or p.password
                or p.query or p.fragment or any(c.isspace() for c in value)):
            raise ValueError('接口地址须为完整 URL，不能包含账号、密码、查询参数或片段')
        if p.scheme == 'http' and p.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('远程接口请使用 HTTPS，本机接口可使用 HTTP')
        try:
            p.port
        except ValueError:
            raise ValueError('接口端口无效') from None
        return value

    @field_validator('model')
    @classmethod
    def model_valid(cls, value):
        value = value.strip()
        if any(ord(c) < 32 for c in value):
            raise ValueError('模型名称不能包含控制字符')
        return value

    @field_validator('api_key')
    @classmethod
    def key_valid(cls, value):
        if any(ord(c) < 32 for c in value.get_secret_value()):
            raise ValueError('密钥不能包含控制字符')
        return value

    @property
    def ready(self):
        return bool(self.base_url and self.model)

    @property
    def endpoint(self):
        return self.base_url if self.base_url.endswith('/chat/completions') else self.base_url+'/chat/completions'

    def public(self):
        return {**self.model_dump(exclude={'api_key'}),
                'api_key_set': bool(self.api_key.get_secret_value()), 'ready': self.ready}

    @classmethod
    def environment(cls):
        return cls(base_url=os.getenv('OPENAI_BASE_URL', ''), model=os.getenv('OPENAI_MODEL', ''),
                   api_key=os.getenv('OPENAI_API_KEY', ''))


class ModelUpdate(ModelConfig):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    enabled: bool | None = None

    @field_validator('api_key')
    @classmethod
    def key_valid(cls, value):
        return ModelConfig.key_valid(value) if value is not None else value


class ModelError(Exception):
    """Only controlled, credential-free messages may cross the API boundary."""


def status_error(status):
    messages = {401: '模型接口鉴权失败 (401)，请检查 API Key',
                403: '模型接口拒绝访问 (403)，请检查权限和模型授权',
                404: '模型接口或模型不存在 (404)，请检查 Base URL 和模型名称',
                429: '模型接口限流或额度不足 (429)，请稍后重试并检查额度'}
    return ModelError(messages.get(status, f'模型接口返回 HTTP {status}，请检查兼容协议和服务状态'))


async def completion(config: ModelConfig, messages: list, client: httpx.AsyncClient, *, test=False):
    if not config.ready:
        raise ModelError('请先配置 OpenAI 兼容接口地址和模型名称')
    headers = {}
    key = config.api_key.get_secret_value()
    if key:
        headers['Authorization'] = 'Bearer '+key
    body = {'model': config.model, 'messages': messages, 'stream': False,
            'max_tokens': 32 if test else config.max_tokens}
    if config.json_mode and not test:
        body['response_format'] = {'type': 'json_object'}
    try:
        response = await client.post(config.endpoint, headers=headers, json=body,
                                     timeout=config.timeout_seconds)
        # Some relays implement Chat Completions without JSON mode. Negotiate only
        # this explicit unsupported-parameter case; never retry 401/429/5xx.
        if response.status_code in (400, 422) and 'response_format' in body:
            detail = response.text[:10000].lower()
            if 'response_format' in detail or 'json_object' in detail:
                body.pop('response_format')
                response = await client.post(config.endpoint, headers=headers, json=body,
                                             timeout=config.timeout_seconds)
        if not response.is_success:
            raise status_error(response.status_code)
        payload = response.json()
        text = payload['choices'][0]['message']['content']
        if not isinstance(text, str) or not text.strip():
            raise ModelError('模型返回空内容，请检查模型名称或增大输出上限')
        if len(text) > 50000:
            raise ModelError('模型返回内容过长，请减小输出上限')
        return text
    except httpx.TimeoutException:
        raise ModelError('模型请求超时，请检查网络或增加超时秒数') from None
    except httpx.HTTPError:
        raise ModelError('无法连接模型接口，请检查地址、网络和证书') from None
    except (ValueError, KeyError, TypeError, IndexError):
        raise ModelError('接口返回格式不兼容，需要 choices[0].message.content') from None


def parse_analysis(text: str):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I)
    try:
        value = json.loads(text)
    except ValueError:
        raise ModelError('模型分析不是有效 JSON，已保留规则结果') from None
    if not isinstance(value, dict):
        raise ModelError('模型分析结构无效，需要 JSON 对象')
    return value


async def test_connection(config):
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        await completion(config, [{'role': 'user', 'content': 'Reply with OK.'}], client, test=True)
    # Do not echo provider content: a proxy may reflect headers in an error payload.
    return {'ok': True, 'message': '连接成功，Chat Completions 接口可用', 'model': config.model}
