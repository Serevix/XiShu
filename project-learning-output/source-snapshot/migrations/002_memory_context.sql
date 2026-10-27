-- 此文件包含新增记忆和会话表的建表语句。运行 scripts/init_memory.py 可创建缺少的表和向量集合。

CREATE TABLE user_memories (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	key VARCHAR(40) NOT NULL, 
	value JSONB NOT NULL, 
	text TEXT NOT NULL, 
	version INTEGER NOT NULL, 
	deleted BOOLEAN NOT NULL, 
	sync_status VARCHAR(16) NOT NULL, 
	sync_error VARCHAR(300), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_memory_key UNIQUE (user_id, key), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)

;


CREATE TABLE user_question_patterns (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	template TEXT NOT NULL, 
	use_count INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	deleted BOOLEAN NOT NULL, 
	sync_status VARCHAR(16) NOT NULL, 
	sync_error VARCHAR(300), 
	last_used_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_pattern_fingerprint UNIQUE (user_id, fingerprint), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)

;


CREATE TABLE analysis_conversations (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	dataset_id VARCHAR(36) NOT NULL, 
	summary TEXT NOT NULL, 
	summary_cursor INTEGER NOT NULL, 
	next_sequence INTEGER NOT NULL, 
	summary_error VARCHAR(300), 
	summary_usage JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE
)

;


CREATE TABLE conversation_messages (
	id VARCHAR(36) NOT NULL, 
	conversation_id VARCHAR(36) NOT NULL, 
	task_id VARCHAR(36) NOT NULL, 
	sequence INTEGER NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	context_text TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_conversation_task_role UNIQUE (task_id, role), 
	CONSTRAINT uq_conversation_sequence UNIQUE (conversation_id, sequence), 
	FOREIGN KEY(conversation_id) REFERENCES analysis_conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(task_id) REFERENCES analysis_tasks (id) ON DELETE CASCADE
)

;

CREATE INDEX ix_user_memories_user_id ON user_memories (user_id);

CREATE INDEX ix_user_memories_sync_status ON user_memories (sync_status);

CREATE INDEX ix_user_question_patterns_sync_status ON user_question_patterns (sync_status);

CREATE INDEX ix_user_question_patterns_user_id ON user_question_patterns (user_id);

CREATE INDEX ix_analysis_conversations_dataset_id ON analysis_conversations (dataset_id);

CREATE INDEX ix_analysis_conversations_user_id ON analysis_conversations (user_id);

CREATE INDEX ix_conversation_messages_conversation_id ON conversation_messages (conversation_id);