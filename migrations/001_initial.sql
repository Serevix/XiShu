-- 此文件由 scripts/init_db.py 生成。首次初始化应运行该脚本，同时配置行级权限和 checkpoint 表。
CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	username VARCHAR(40) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

CREATE TABLE datasets (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	source_type VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	file_manifest JSONB NOT NULL, 
	field_mapping JSONB NOT NULL, 
	metric_definitions JSONB NOT NULL, 
	import_summary JSONB NOT NULL, 
	error_info VARCHAR(2000), 
	data_version VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE analysis_tasks (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	dataset_id VARCHAR(36) NOT NULL, 
	question TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	thread_id VARCHAR(36) NOT NULL, 
	model_mode VARCHAR(10) NOT NULL, 
	config_snapshot JSONB NOT NULL, 
	events JSONB NOT NULL, 
	artifacts JSONB NOT NULL, 
	usage JSONB NOT NULL, 
	resume_payload TEXT, 
	clarification TEXT, 
	error_info VARCHAR(2000), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE, 
	UNIQUE (thread_id)
);

CREATE TABLE customers (
	dataset_id VARCHAR(36) NOT NULL, 
	customer_id VARCHAR(100) NOT NULL, 
	customer_unique_id VARCHAR(100), 
	city VARCHAR(100), 
	state VARCHAR(50), 
	PRIMARY KEY (dataset_id, customer_id), 
	FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE
);

CREATE TABLE products (
	dataset_id VARCHAR(36) NOT NULL, 
	product_id VARCHAR(100) NOT NULL, 
	category_name VARCHAR(120), 
	PRIMARY KEY (dataset_id, product_id), 
	FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE
);

CREATE TABLE orders (
	dataset_id VARCHAR(36) NOT NULL, 
	order_id VARCHAR(100) NOT NULL, 
	customer_id VARCHAR(100) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	purchase_time TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	delivered_time TIMESTAMP WITHOUT TIME ZONE, 
	estimated_delivery_time TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (dataset_id, order_id), 
	FOREIGN KEY(dataset_id, customer_id) REFERENCES customers (dataset_id, customer_id), 
	FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE
);

CREATE TABLE order_items (
	dataset_id VARCHAR(36) NOT NULL, 
	order_id VARCHAR(100) NOT NULL, 
	order_item_id INTEGER NOT NULL, 
	product_id VARCHAR(100) NOT NULL, 
	price NUMERIC(18, 2) NOT NULL, 
	freight_value NUMERIC(18, 2) NOT NULL, 
	PRIMARY KEY (dataset_id, order_id, order_item_id), 
	FOREIGN KEY(dataset_id, order_id) REFERENCES orders (dataset_id, order_id), 
	FOREIGN KEY(dataset_id, product_id) REFERENCES products (dataset_id, product_id), 
	CONSTRAINT ck_item_nonnegative CHECK (price >= 0 AND freight_value >= 0), 
	CONSTRAINT ck_item_positive_id CHECK (order_item_id > 0), 
	FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE
);

CREATE INDEX ix_datasets_user_id ON datasets (user_id);

CREATE INDEX ix_datasets_status ON datasets (status);

CREATE INDEX ix_analysis_tasks_user_id ON analysis_tasks (user_id);

CREATE INDEX ix_analysis_tasks_dataset_id ON analysis_tasks (dataset_id);

CREATE INDEX ix_analysis_tasks_status ON analysis_tasks (status);

CREATE INDEX ix_orders_dataset_time ON orders (dataset_id, purchase_time);