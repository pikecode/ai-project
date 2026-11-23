-- 仅用于旧 schema.sql 创建的 PostgreSQL；执行前使用 pg_dump 备份。
-- 同一事务内重命名列，保留原有数据和 UUID 主外键。
BEGIN;
DO $$
DECLARE table_name_to_migrate TEXT;
BEGIN
    FOREACH table_name_to_migrate IN ARRAY ARRAY['conversations', 'messages'] LOOP
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = table_name_to_migrate AND column_name = 'metadata')
           AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = table_name_to_migrate AND column_name = 'meta') THEN
            EXECUTE format('ALTER TABLE %I RENAME COLUMN metadata TO meta', table_name_to_migrate);
        END IF;
    END LOOP;
END $$;
COMMIT;
