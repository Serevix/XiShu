"""集中导入数据库模型，让建表和导出表结构时能找到所有模型定义。"""
from backend.models.base import Base
from backend.models.user import User
from backend.models.dataset import Dataset
from backend.models.customer import Customer
from backend.models.product import Product
from backend.models.order import Order
from backend.models.order_item import OrderItem
from backend.models.analysis_task import AnalysisTask
from backend.models.memory import UserMemory, QuestionPattern
from backend.models.conversation import Conversation, ConversationMessage

__all__ = ['Base', 'User', 'Dataset', 'Customer', 'Product', 'Order', 'OrderItem', 'AnalysisTask',
           'UserMemory', 'QuestionPattern', 'Conversation', 'ConversationMessage']
