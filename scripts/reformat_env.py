"""按中文分类模板整理本地配置，保留已有凭据和用户自定义配置项。"""
import argparse
import json
import re
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def reformat_env(updates: dict[str, str] | None = None, target: Path | None = None,
                 template: Path | None = None) -> None:
    """按 .env.example 的分类顺序重写 .env，保留本地已有值，不打印配置密钥。"""
    target, template = target or ROOT / '.env', template or ROOT / '.env.example'
    existing = dict(dotenv_values(target)) if target.exists() else {}
    existing.update(updates or {})
    defaults = dict(dotenv_values(template))
    used, lines = set(), []
    for line in template.read_text(encoding='utf-8').splitlines():
        match = re.match(r'^([A-Z][A-Z0-9_]*)=', line)
        if match:
            key = match.group(1)
            value = existing.get(key, defaults.get(key)) or ''
            lines.append(key + '=' + json.dumps(str(value), ensure_ascii=False))
            used.add(key)
        else:
            lines.append(line.replace('复制为 .env 后填写真实值', '本地实际配置'))
    extras = {key:value for key,value in existing.items() if key not in used}
    if extras:
        lines += ['', '# ----- 14. 保留的用户自定义配置 -----']
        for key, value in extras.items():
            lines.append(key + '=' + json.dumps(value or '', ensure_ascii=False))
    temporary = target.with_suffix('.env.tmp')
    temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    temporary.replace(target)


def main() -> None:
    """整理配置，可从标准输入读取硅基流动密钥，不把密钥放在命令行参数中。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--siliconflow-key-stdin', action='store_true')
    arguments = parser.parse_args()
    updates = None
    if arguments.siliconflow_key_stdin:
        key = sys.stdin.readline().strip()
        if not key:
            raise ValueError('没有收到 API Key')
        updates = {'MODEL_PROVIDER':'siliconflow','MODEL_MODE':'llm',
                   'MODEL_BASE_URL':'https://api.siliconflow.cn/v1',
                   'MODEL_NAME':'deepseek-ai/DeepSeek-V4-Flash','MODEL_API_KEY':key}
    reformat_env(updates)
    print('本地 .env 已按分类整理；密钥未输出。')


if __name__ == '__main__':
    main()
