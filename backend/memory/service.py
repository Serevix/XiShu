"""保存用户明确偏好和常问模板，异步同步记忆向量。历史分析答案不作为新任务的计算结果。"""
import asyncio
import hashlib
import json
import re
from sqlalchemy import select, text
from backend.core.config import get_settings
from backend.core.database import Session, engine
from backend.models import User, UserMemory, QuestionPattern, AnalysisTask
from backend.models.base import utcnow
from backend.memory.vectors import embed, search_vectors, write_vector, delete_vector

# 偏好只允许指定的图表和报告风格，不允许把用户文本写成系统指令，也不允许改变统计口径。
PREFERENCE_VALUES = {'chart_kind': {'bar', 'line'}, 'report_style': {'concise', 'detailed'}}


def enqueue(reference: str):
    """向队列发送记忆记录的 ID。发送失败时保留待同步状态，由定时任务重试，不在消息中放密钥或向量。"""
    from backend.services.analysis_tasks import dispatch
    return dispatch('analytics.memory_sync', reference)


def serialize_memory(row, kind: str) -> dict:
    """整理用户可以查看的记忆内容和同步状态，不返回连接配置。"""
    body = {'id': row.id, 'kind': kind, 'version': row.version, 'sync_status': row.sync_status, 'sync_error': row.sync_error}
    if kind == 'preference':
        body.update(key=row.key, value=row.value['value'], text=row.text)
    else:
        body.update(template=row.template, use_count=row.use_count, last_used_at=row.last_used_at.isoformat())
    return body


async def set_preferences(user_id: str, updates: dict) -> list[dict]:
    """锁住用户记录后更新允许的偏好。null 表示删除偏好；内容改变时增加版本号，并在提交后发送同步任务。"""
    references, result = [], []
    async with Session.begin() as session:
        # 同一用户的偏好修改依次执行。即使偏好记录尚不存在，也能避免并发请求创建重复记录。
        await session.get(User, user_id, with_for_update=True)
        for key, value in updates.items():
            if key not in PREFERENCE_VALUES or value is not None and value not in PREFERENCE_VALUES[key]:
                raise ValueError('偏好键或值无效')
            row = await session.scalar(select(UserMemory).where(UserMemory.user_id == user_id, UserMemory.key == key))
            if row is None:
                if value is None:
                    continue
                row = UserMemory(user_id=user_id, key=key, value={'value': value}, text=f'用户明确偏好 {key}: {value}')
                session.add(row)
                await session.flush()
            elif row.value.get('value') != value or row.deleted != (value is None):
                row.value, row.deleted = {'value': value}, value is None
                row.text = f'用户明确偏好 {key}: {value}'
                row.version += 1
                # 内容改变后先标记 pending，表示需要同步。耗时的嵌入请求和 Milvus 操作由后台处理。
                row.sync_status, row.sync_error = 'pending', None
            else:
                if row.sync_status == 'indexed':
                    result.append(serialize_memory(row, 'preference'))
                    continue
            row.updated_at = utcnow()
            references.append('preference:' + row.id)
            result.append(serialize_memory(row, 'preference'))
    for reference in references:
        # 先提交 PostgreSQL 记录，再发送同步任务。发送失败时 pending 状态仍在，Beat 可以再次发送任务。
        enqueue(reference)
    return result


async def explicit_preferences(user_id: str, question: str):
    """只从用户明确表示要长期保留的要求中提取允许的偏好，不从工具结果或助手回答中推测偏好。"""
    if not get_settings().memory_enabled or not re.search(r'记住|以后|默认|总是', question):
        return
    updates = {}
    for clause, scope in preference_clauses(question):
        if scope == 'long' and not re.search(r'不要|不想|别用|禁止|不使用|不用|不喜欢|不需要', clause):
            for key, values in clause_preferences(clause).items():
                if len(values) == 1:
                    updates[key] = next(iter(values))
    if updates:
        await set_preferences(user_id, updates)


def question_template(question: str) -> str | None:
    """把问题中的旧日期换成待填写的时间参数。涉及密码、令牌等敏感信息的问题不用于学习模板。"""
    if re.search(r'密码|身份证|银行卡|api.?key|\btoken\b|sk-', question, re.I):
        return None
    # 保存的是常问问题的模板，不是旧答案。用时间占位符替换旧日期，推荐时由用户填写本次范围。
    result = re.sub(r'\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2})?月?', '[时间范围]', question)
    result = re.sub(r'\s+', ' ', result).strip()
    return result[:800] if result else None


async def learn_task(task_id: str):
    """只统计成功任务的常问模式。问题频次和已学习标记一起提交，同一任务重试时不会重复计数。"""
    if not get_settings().memory_enabled:
        return
    reference = None
    async with Session.begin() as session:
        task = await session.get(AnalysisTask, task_id, with_for_update=True)
        # memory_recorded 表示这个任务已经学习过。恢复时再次处理同一任务，也不能再次增加问题频次。
        if task is None or task.status != 'succeeded' or task.config_snapshot.get('memory_recorded'):
            return
        template = question_template(task.question)
        if template:
            await session.get(User, task.user_id, with_for_update=True)
            fingerprint = hashlib.sha256(template.encode()).hexdigest()
            row = await session.scalar(select(QuestionPattern).where(QuestionPattern.user_id == task.user_id, QuestionPattern.fingerprint == fingerprint))
            if row is None:
                row = QuestionPattern(user_id=task.user_id, fingerprint=fingerprint, template=template)
                session.add(row)
                await session.flush()
                reference = 'pattern:' + row.id
            elif not row.deleted:
                row.use_count += 1
                row.last_used_at = utcnow()
                if row.sync_status != 'indexed':
                    reference = 'pattern:' + row.id
        # 问题频次和已学习标记一起保存，避免计数完成后进程崩溃，重试时又多计一次。
        task.config_snapshot = {**task.config_snapshot, 'memory_recorded': True}
    if reference:
        enqueue(reference)


async def synchronize(reference: str):
    """同步指定记忆的向量，并用锁避免同时同步同一条记录。同步失败时保存错误，等待后续重试。"""
    kind, identifier = reference.split(':', 1)
    model = {'preference': UserMemory, 'pattern': QuestionPattern}.get(kind)
    if model is None:
        raise ValueError('记忆类型无效')
    async with engine.connect() as lock:
        acquired = await lock.scalar(text('SELECT pg_try_advisory_lock(hashtextextended(:key,0))'), {'key': 'memory:' + reference})
        await lock.commit()
        if not acquired:
            return
        version = None
        try:
            async with Session() as session:
                row = await session.get(model, identifier)
                if row is None:
                    return
                if row.sync_status in ('indexed', 'deleted'):
                    return
                owner, version, deleted = row.user_id, row.version, row.deleted
                content = row.text if kind == 'preference' else row.template
            # 在没有占用业务事务时调用慢接口。完成后再次核对版本号，防止旧同步任务覆盖新版本的同步状态。
            if deleted:
                await asyncio.to_thread(delete_vector, identifier, owner)
            else:
                vector = await embed(content)
                await asyncio.to_thread(write_vector, {'id': identifier, 'user_id': owner, 'kind': kind,
                                                      'text': content, 'version': version, 'vector': vector})
            async with Session.begin() as session:
                current = await session.get(model, identifier, with_for_update=True)
                if current and current.version == version:
                    current.sync_status, current.sync_error = ('deleted' if deleted else 'indexed'), None
        except Exception as error:
            async with Session.begin() as session:
                row = await session.get(model, identifier, with_for_update=True)
                if row and version is not None and row.version == version:
                    row.sync_status, row.sync_error = 'failed', '向量同步失败：' + type(error).__name__
        finally:
            await lock.execute(text('SELECT pg_advisory_unlock(hashtextextended(:key,0))'), {'key':'memory:' + reference})
            await lock.commit()


async def personalize(user_id: str, question: str) -> dict:
    """读取明确偏好，并检索相关常问模板。使用向量候选前，再核对数据库中的用户、版本和删除状态。"""
    result = {'preferences': {}, 'patterns': [], 'vector_status': 'not_needed'}
    if not get_settings().memory_enabled:
        return result
    async with Session() as session:
        preferences = list(await session.scalars(select(UserMemory).where(UserMemory.user_id == user_id, UserMemory.deleted.is_(False))))
        patterns = list(await session.scalars(select(QuestionPattern).where(QuestionPattern.user_id == user_id, QuestionPattern.deleted.is_(False),
                       QuestionPattern.use_count >= get_settings().memory_pattern_min_uses)))
    result['preferences'] = {row.key: row.value['value'] for row in preferences if row.key in PREFERENCE_VALUES}
    # 明确偏好直接从 PostgreSQL 读取，不用向量检索。常问模板达到使用次数门槛后才检索相关模式。
    if patterns:
        try:
            candidates = await asyncio.to_thread(search_vectors, user_id, await embed(question))
            valid = {row.id: row for row in patterns}
            for item in candidates:
                entity = item.get('entity', {})
                row = valid.get(str(item['id']))
                # Milvus 只负责找候选，最终使用 PostgreSQL 中仍有效的当前用户记录，并拒绝版本不符的旧向量。
                if row and entity.get('user_id') == user_id and entity.get('version') == row.version and item['distance'] >= get_settings().memory_min_similarity:
                    result['patterns'].append({'id': row.id, 'template': row.template, 'use_count': row.use_count, 'similarity': round(item['distance'], 4)})
            result['vector_status'] = 'searched'
        except Exception:
            # 向量服务不可用时跳过模板推荐，但明确偏好仍可读取，基础分析仍可继续。
            result['vector_status'] = 'unavailable'
    return result


def preference_clauses(question: str) -> list[tuple[str, str]]:
    """将问题拆为短句，判断各句是在描述长期偏好还是本次要求。未写明范围的后续短句沿用最近的范围。"""
    clauses, scope = [], 'unspecified'
    for clause in re.split(r'[,，。；;\n]|但是|但', question):
        if re.search(r'本次|这次|此次|现在|今天', clause):
            scope = 'current'
        elif re.search(r'记住|以后|默认|总是', clause):
            scope = 'long'
        if clause.strip():
            clauses.append((clause, scope))
    return clauses


def clause_preferences(clause: str) -> dict[str, set[str]]:
    """从短句中提取允许的图表或报告风格值。出现多个冲突值时，由调用方决定如何处理。"""
    patterns = {'chart_kind': {'line': r'折线图|\bline\b', 'bar': r'柱状图|条形图|\bbar\b'},
                'report_style': {'concise': r'简洁|简短|简要', 'detailed': r'详细|详尽'}}
    return {key: {value for value, pattern in candidates.items() if re.search(pattern, clause, re.I)}
            for key, candidates in patterns.items()}


def resolve_personalization(question: str, preferences: dict) -> dict:
    """合并历史偏好和本次要求，本次明确要求优先。只返回允许的展示偏好，不改变指标口径。"""
    selected = dict(preferences)
    clauses = preference_clauses(question)
    if any(scope == 'current' for _, scope in clauses):
        # 例如用户说“以后用柱状图，但这次用折线图”，本次使用折线图，长期偏好由保存偏好的函数另外处理。
        clauses = [(clause, scope) for clause, scope in clauses if scope == 'current']
    requested = {key: set() for key in PREFERENCE_VALUES}
    for clause, _ in clauses:
        for key, values in clause_preferences(clause).items():
            if re.search(r'不要|不想|别用|禁止|不使用|不用|不喜欢|不需要', clause):
                if selected.get(key) in values:
                    selected.pop(key, None)
            else:
                requested[key].update(values)
    for key, values in requested.items():
        if len(values) == 1:
            selected[key] = next(iter(values))
        elif len(values) > 1:
            selected.pop(key, None)
    return {key: value for key, value in selected.items() if key in PREFERENCE_VALUES and value in PREFERENCE_VALUES[key]}
