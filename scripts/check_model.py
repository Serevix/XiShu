"""用固定测试文本检查真实模型的工具调用，不发送用户业务数据。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agent.model import model_action
from backend.core.config import get_settings


async def check_model() -> bool:
    """检查模型是否支持项目工具协议，只输出模型名称、选择的动作和用量，不输出密钥。"""
    settings = get_settings()
    print('模型：' + settings.model_name)
    try:
        action, _, usage = await model_action({'messages':[
            {'role':'system','content':'你是工具调用测试助手。只调用 get_dataset_schema 一次，不执行分析。'},
            {'role':'user','content':'请获取数据集表结构。'}]})
        print('工具调用：' + action['name'])
        print('Token 用量：' + str(usage.get('total_tokens', '供应商未返回')))
        return action['name'] == 'get_dataset_schema'
    except ValueError as error:
        print('检查未通过：' + str(error))
        return False


if __name__ == '__main__':
    sys.exit(0 if asyncio.run(check_model()) else 1)
